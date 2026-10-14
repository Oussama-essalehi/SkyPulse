"""Prometheus metrics of the API (scraped on /metrics by the monitoring stack)."""

from __future__ import annotations

from prometheus_client import Counter, Gauge

AIRCRAFT = Gauge("skyradar_api_aircraft", "Aircraft in the live state")
WS_CLIENTS = Gauge("skyradar_api_ws_clients", "Connected WebSocket clients")
KAFKA_CONNECTED = Gauge("skyradar_api_kafka_connected", "1 when the live consumer is assigned")
SNAPSHOT_AGE = Gauge(
    "skyradar_api_last_snapshot_age_seconds", "Age of the latest snapshot in the live state"
)
REQUESTS = Counter("skyradar_api_requests_total", "HTTP requests", ["method", "route", "status"])
RATE_LIMITED = Counter("skyradar_api_rate_limited_total", "Requests refused (429)", ["limiter"])
ROUTE_LOOKUPS = Counter("skyradar_api_route_lookups_total", "adsbdb lookups", ["kind", "result"])
