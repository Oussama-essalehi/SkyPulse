"""Ingestion entry point: source -> validation -> Redpanda."""

from __future__ import annotations

import asyncio
import signal
import tempfile
import time
from pathlib import Path

import structlog
from aiokafka import AIOKafkaProducer

from skyradar_common.kafka import KafkaSettings, ensure_topics, start_producer
from skyradar_common.logging import configure_logging
from skyradar_common.models import SnapshotMeta
from skyradar_common.topics import AIRCRAFT_SNAPSHOTS, AIRCRAFT_STATES
from skyradar_ingestion import metrics
from skyradar_ingestion.config import IngestionSettings
from skyradar_ingestion.quota import AccountTier, BoundingBox, resolve_poll_interval
from skyradar_ingestion.sources.base import FlightDataSource, Snapshot
from skyradar_ingestion.sources.opensky import OpenSkySource
from skyradar_ingestion.sources.replay import ReplaySource

log = structlog.get_logger(__name__)


def build_source(settings: IngestionSettings) -> FlightDataSource:
    if settings.source == "replay":
        path = Path(settings.replay_path)
        if not path.exists():
            if not settings.replay_synthetic_fallback:
                raise FileNotFoundError(f"REPLAY_PATH not found: {path}")
            from skyradar_ingestion.synthetic import generate

            # Written to a temp dir: the replay mount may be read-only for the container user.
            path = Path(tempfile.gettempdir()) / "skyradar-synthetic.parquet"
            log.warning(
                "replay_missing_generating_synthetic",
                configured=settings.replay_path,
                path=str(path),
            )
            generate(path)
        return ReplaySource(path, speed=settings.replay_speed, loop=settings.replay_loop)

    bbox = BoundingBox.parse(settings.opensky_bbox) if settings.opensky_bbox else None
    if settings.has_credentials:
        tier = AccountTier.FEEDER if settings.opensky_feeder else AccountTier.STANDARD
    else:
        tier = AccountTier.ANONYMOUS
    interval, reason = resolve_poll_interval(
        settings.poll_interval_seconds,
        tier,
        bbox,
        margin=settings.opensky_quota_margin,
        daily_credits=settings.opensky_daily_credits,
    )
    log.info(
        "opensky_polling_plan",
        tier=tier.value,
        interval_s=interval,
        reason=reason,
        bbox=settings.opensky_bbox or "global",
        calls_per_day=round(86_400 / interval),
    )
    return OpenSkySource(
        poll_interval_seconds=interval,
        client_id=settings.opensky_client_id,
        client_secret=settings.opensky_client_secret,
        bbox=bbox,
        api_url=settings.opensky_api_url,
        token_url=settings.opensky_token_url,
    )


async def publish_snapshot(producer: AIOKafkaProducer, snapshot: Snapshot) -> SnapshotMeta:
    """Publish one message per aircraft (key = icao24) then the snapshot metadata."""
    started = time.perf_counter()
    for state in snapshot.states:
        await producer.send(AIRCRAFT_STATES, value=state.to_bytes(), key=state.icao24.encode())
    meta = SnapshotMeta(
        snapshot_time=snapshot.snapshot_time,
        ingested_at=time.time(),
        aircraft_count=len(snapshot.states),
        source=snapshot.source,
        poll_interval_seconds=snapshot.poll_interval_seconds,
        latency_ms=snapshot.latency_ms,
        credits_remaining=snapshot.credits_remaining,
    )
    await producer.flush()
    # Metadata goes last so that consumers see it after the states it describes.
    await producer.send_and_wait(AIRCRAFT_SNAPSHOTS, value=meta.to_bytes(), key=b"snapshot")
    metrics.PUBLISH_LATENCY.observe(time.perf_counter() - started)
    return meta


async def run_async(settings: IngestionSettings | None = None) -> None:
    settings = settings or IngestionSettings()
    kafka = KafkaSettings()
    metrics.serve(settings.metrics_port)
    source = build_source(settings)
    metrics.POLL_INTERVAL.set(source.poll_interval_seconds)
    await ensure_topics(kafka)
    producer = await start_producer(kafka)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    async def consume() -> None:
        async for snapshot in source.stream():
            meta = await publish_snapshot(producer, snapshot)
            metrics.AIRCRAFT.set(meta.aircraft_count)
            metrics.SNAPSHOTS.labels(snapshot.source).inc()
            metrics.MESSAGES.inc(meta.aircraft_count)
            metrics.SKIPPED.inc(snapshot.skipped_rows)
            metrics.LAST_SNAPSHOT.set(snapshot.snapshot_time)
            if snapshot.latency_ms is not None:
                metrics.SOURCE_LATENCY.observe(snapshot.latency_ms / 1000)
            if snapshot.credits_remaining is not None:
                metrics.CREDITS.set(snapshot.credits_remaining)
            log.info(
                "snapshot_published",
                source=snapshot.source,
                aircraft=meta.aircraft_count,
                snapshot_time=snapshot.snapshot_time,
                lag_s=round(meta.ingested_at - snapshot.snapshot_time, 1),
            )

    task = asyncio.create_task(consume())
    stopper = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait({task, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            task.result()  # surface exceptions
    finally:
        task.cancel()
        stopper.cancel()
        await producer.stop()
        await source.aclose()
        log.info("ingestion_stopped")


def run() -> None:
    configure_logging("ingestion")
    asyncio.run(run_async())


if __name__ == "__main__":
    run()
