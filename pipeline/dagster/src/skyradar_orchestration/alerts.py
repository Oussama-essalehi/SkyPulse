"""Publish batch-detected anomalies (diversions, trajectory outliers) to ``aircraft.alerts``.

The API and the map treat them exactly like the streaming alerts. Only events that
happened during the last ``lookback`` window are published, so an hourly job reports
each event about once (the alert id is deterministic: a duplicate is harmless).
"""

import asyncio
import os
import time

import pandas as pd

from skyradar_common.kafka import KafkaSettings, ensure_topics, start_producer
from skyradar_common.models import Alert, AlertType, Severity
from skyradar_common.topics import AIRCRAFT_ALERTS

LOOKBACK_MINUTES = int(os.getenv("ML_ALERTS_LOOKBACK_MINUTES", "75"))


def publishing_enabled() -> bool:
    return os.getenv("ML_PUBLISH_ALERTS", "true").lower() == "true"


async def _publish(alerts: list[Alert]) -> None:
    settings = KafkaSettings()
    await ensure_topics(settings)
    producer = await start_producer(settings)
    try:
        for alert in alerts:
            await producer.send(AIRCRAFT_ALERTS, alert.to_bytes(), key=alert.icao24.encode())
        await producer.flush()
    finally:
        await producer.stop()


def publish(alerts: list[Alert]) -> int:
    if alerts and publishing_enabled():
        asyncio.run(_publish(alerts))
        return len(alerts)
    return 0


def _epoch(value: object) -> int:
    return int(pd.Timestamp(value).timestamp())


def recent(frame: pd.DataFrame, column: str, now: float | None = None) -> pd.DataFrame:
    now = now or time.time()
    cutoff = pd.Timestamp(now - LOOKBACK_MINUTES * 60, unit="s")
    return frame[pd.to_datetime(frame[column]) >= cutoff]


def diversion_alerts(diversions: pd.DataFrame, now: float | None = None) -> list[Alert]:
    now = now or time.time()
    alerts = []
    for row in recent(diversions, "arrival_time", now).itertuples():
        alerts.append(
            Alert(
                alert_id=f"diversion:{row.icao24}:{_epoch(row.departure_time)}",
                icao24=row.icao24,
                callsign=row.callsign,
                type=AlertType.DIVERSION,
                severity=Severity.WARNING,
                message=(
                    f"Déroutement probable : arrivée à {row.arrival_airport} au lieu de "
                    f"{row.usual_arrival_airport} (destination habituelle depuis "
                    f"{row.departure_airport}, {int(row.usual_arrival_flights)} vols, "
                    f"{row.usual_arrival_share:.0%})"
                ),
                latitude=None if pd.isna(row.arrival_latitude) else float(row.arrival_latitude),
                longitude=None if pd.isna(row.arrival_longitude) else float(row.arrival_longitude),
                event_time=_epoch(row.arrival_time),
                detected_at=now,
                details={
                    "flight_id": row.flight_id,
                    "usual_arrival": row.usual_arrival_airport,
                    "arrival": row.arrival_airport,
                },
            )
        )
    return alerts


def outlier_alerts(scores: pd.DataFrame, now: float | None = None) -> list[Alert]:
    now = now or time.time()
    alerts = []
    flagged = scores[scores["is_outlier"]]
    for row in recent(flagged, "last_seen_ts", now).itertuples():
        alerts.append(
            Alert(
                alert_id=f"trajectory_outlier:{row.icao24}:{row.flight_id.split('-')[-1]}",
                icao24=row.icao24,
                callsign=row.callsign,
                type=AlertType.TRAJECTORY_OUTLIER,
                severity=Severity.INFO,
                message=f"Trajectoire atypique (Isolation Forest) : {row.explanation}",
                latitude=float(row.last_latitude),
                longitude=float(row.last_longitude),
                event_time=_epoch(row.last_seen_ts),
                detected_at=now,
                details={"flight_id": row.flight_id, "anomaly_score": round(row.anomaly_score, 4)},
            )
        )
    return alerts
