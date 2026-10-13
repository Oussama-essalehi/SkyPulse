"""Parquet sink service: aircraft.states + aircraft.alerts -> object storage."""

from __future__ import annotations

import asyncio
import signal
import time

import structlog
from prometheus_client import Counter, Gauge, start_http_server
from pyarrow import fs as pafs
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from skyradar_common.kafka import KafkaSettings, ensure_topics, start_consumer
from skyradar_common.logging import configure_logging
from skyradar_common.storage import S3Settings, ensure_bucket, s3_filesystem
from skyradar_common.topics import AIRCRAFT_ALERTS, AIRCRAFT_STATES
from skyradar_streaming.parquet_sink.sink import ParquetSink

log = structlog.get_logger(__name__)

ROWS = Counter("skyradar_sink_rows_total", "Rows written to Parquet")
FILES = Counter("skyradar_sink_files_total", "Parquet files written")
BUFFERED = Gauge("skyradar_sink_buffered_rows", "Rows waiting for the next flush")
LAST_FLUSH = Gauge("skyradar_sink_last_flush_timestamp", "Unix time of the last flush")


class SinkSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    target: str = Field("s3", alias="SINK_TARGET")  # "s3" or "local"
    local_root: str = Field("/data/lake", alias="SINK_LOCAL_ROOT")
    flush_interval_seconds: float = Field(300, alias="SINK_FLUSH_INTERVAL_SECONDS")
    max_rows: int = Field(250_000, alias="SINK_MAX_ROWS")
    group_id: str = Field("parquet-sink", alias="SINK_GROUP_ID")
    metrics_port: int = Field(9101, alias="METRICS_PORT")


def build_filesystem(settings: SinkSettings) -> tuple[pafs.FileSystem, str]:
    if settings.target == "local":
        return pafs.LocalFileSystem(), settings.local_root
    s3 = S3Settings()
    filesystem = s3_filesystem(s3)
    ensure_bucket(s3, filesystem)
    return filesystem, s3.bucket


async def run_async() -> None:
    settings = SinkSettings()
    kafka = KafkaSettings()
    filesystem, base = build_filesystem(settings)
    start_http_server(settings.metrics_port)  # healthy only once storage is reachable
    sink = ParquetSink(filesystem, base)
    await ensure_topics(kafka)
    consumer = await start_consumer(
        kafka,
        [AIRCRAFT_STATES, AIRCRAFT_ALERTS],
        group_id=settings.group_id,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        max_partition_fetch_bytes=8 * 1024 * 1024,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    async def flush() -> None:
        rows = sink.buffered
        # Writing to S3 is blocking I/O: keep the event loop (and Kafka heartbeats) alive.
        paths = await asyncio.to_thread(sink.flush)
        await consumer.commit()
        ROWS.inc(rows)
        FILES.inc(len(paths))
        LAST_FLUSH.set(time.time())
        log.info("sink_flushed", rows=rows, files=len(paths))

    last_flush = time.monotonic()
    log.info(
        "sink_started",
        target=settings.target,
        base=base,
        flush_interval_s=settings.flush_interval_seconds,
    )
    try:
        while not stop.is_set():
            batches = await consumer.getmany(timeout_ms=1000, max_records=20_000)
            for tp, records in batches.items():
                for record in records:
                    if record.value is not None:
                        sink.add(tp.topic, tp.partition, record.offset, record.value)
            BUFFERED.set(sink.buffered)
            due = time.monotonic() - last_flush >= settings.flush_interval_seconds
            if sink.buffered >= settings.max_rows or (due and sink.buffered):
                await flush()
                last_flush = time.monotonic()
            elif due:
                last_flush = time.monotonic()
        if sink.buffered:
            await flush()  # graceful shutdown: nothing buffered is lost
    finally:
        await consumer.stop()
        log.info("sink_stopped")


def run() -> None:
    configure_logging("parquet-sink")
    asyncio.run(run_async())


if __name__ == "__main__":
    run()
