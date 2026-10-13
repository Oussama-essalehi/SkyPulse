"""Anomaly detector service: aircraft.states -> rules -> aircraft.alerts."""

from __future__ import annotations

import asyncio
import signal
import time

import structlog
from prometheus_client import Counter, Gauge, start_http_server
from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from skyradar_common.kafka import KafkaSettings, ensure_topics, start_consumer, start_producer
from skyradar_common.logging import configure_logging
from skyradar_common.models import StateVector
from skyradar_common.topics import AIRCRAFT_ALERTS, AIRCRAFT_STATES
from skyradar_streaming.anomaly.detector import AnomalyDetector

log = structlog.get_logger(__name__)

ALERT_EVENTS = Counter("skyradar_anomaly_alert_events_total", "Alert events", ["type", "status"])
TRACKED = Gauge("skyradar_anomaly_tracked_aircraft", "Aircraft with a sliding window")
ACTIVE = Gauge("skyradar_anomaly_active_alerts", "Currently active alerts")


class DetectorSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    group_id: str = Field("anomaly-detector", alias="ANOMALY_GROUP_ID")
    metrics_port: int = Field(9102, alias="METRICS_PORT")
    history_seconds: int = Field(1800, alias="ANOMALY_HISTORY_SECONDS")


async def run_async() -> None:
    settings = DetectorSettings()
    kafka = KafkaSettings()
    start_http_server(settings.metrics_port)
    await ensure_topics(kafka)
    detector = AnomalyDetector(history_seconds=settings.history_seconds)
    producer = await start_producer(kafka)
    consumer = await start_consumer(
        kafka,
        [AIRCRAFT_STATES],
        group_id=settings.group_id,
        enable_auto_commit=True,
        auto_offset_reset="latest",
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    last_evict = time.monotonic()
    log.info("anomaly_detector_started")
    try:
        while not stop.is_set():
            batches = await consumer.getmany(timeout_ms=1000, max_records=20_000)
            events = []
            for records in batches.values():
                for record in records:
                    if record.value is None:
                        continue
                    try:
                        state = StateVector.from_bytes(record.value)
                    except ValidationError:
                        continue
                    events.extend(detector.process(state))
            if time.monotonic() - last_evict > 60:
                events.extend(detector.evict_stale(time.time()))
                last_evict = time.monotonic()
            for alert in events:
                await producer.send(AIRCRAFT_ALERTS, alert.to_bytes(), key=alert.icao24.encode())
                ALERT_EVENTS.labels(alert.type.value, alert.status.value).inc()
                log.info(
                    "alert",
                    type=alert.type.value,
                    status=alert.status.value,
                    icao24=alert.icao24,
                    callsign=alert.callsign,
                    message=alert.message,
                )
            if events:
                await producer.flush()
            TRACKED.set(detector.tracked)
            ACTIVE.set(len(detector.active))
    finally:
        await consumer.stop()
        await producer.stop()


def run() -> None:
    configure_logging("anomaly-detector")
    asyncio.run(run_async())


if __name__ == "__main__":
    run()
