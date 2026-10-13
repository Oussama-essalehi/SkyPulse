"""Prometheus metrics exposed on ``/metrics`` (port ``INGESTION_METRICS_PORT``)."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

AIRCRAFT = Gauge("skyradar_ingestion_aircraft", "Aircraft with a position in the last snapshot")
SKIPPED = Counter("skyradar_ingestion_skipped_rows_total", "Rows without position or invalid")
SNAPSHOTS = Counter("skyradar_ingestion_snapshots_total", "Snapshots published", ["source"])
MESSAGES = Counter("skyradar_ingestion_messages_total", "State messages published")
LAST_SNAPSHOT = Gauge("skyradar_ingestion_last_snapshot_timestamp", "Unix time of last snapshot")
CREDITS = Gauge("skyradar_ingestion_opensky_credits_remaining", "X-Rate-Limit-Remaining header")
POLL_INTERVAL = Gauge("skyradar_ingestion_poll_interval_seconds", "Effective polling interval")
SOURCE_LATENCY = Histogram(
    "skyradar_ingestion_source_latency_seconds",
    "Latency of the source call",
    buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32),
)
PUBLISH_LATENCY = Histogram(
    "skyradar_ingestion_publish_seconds",
    "Time to publish a full snapshot to Redpanda",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)


def serve(port: int) -> None:
    start_http_server(port)
