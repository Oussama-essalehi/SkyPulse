"""Upper-air winds for the ETA model, from the Open-Meteo forecast API.

A ground-speed-based ETA ignores the wind still ahead of the aircraft; the ETA model gets
the tail-wind component at cruise level as a feature. Winds at 250 hPa (~FL340, typical
cruise) come from Open-Meteo (free, no key, CC BY 4.0, non-commercial use) on a coarse
10 degree grid: 432 points, fetched once a day with yesterday's and today's hourly values
(``past_days=1``), so a missed day is backfilled by the next fetch.

Rows land in the lake like the sink's: ``raw/upper_winds/date=<fetch day>/hour=00/``.
The time column is ``valid_time`` (``hour`` is the partition key); dbt keeps the latest
value per (grid point, hour).
"""

from __future__ import annotations

import io
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
from pyarrow import fs as pafs

from skyradar_orchestration.compaction import filesystem_and_base

API_URL = "https://api.open-meteo.com/v1/forecast"
GRID_LATS = range(-40, 71, 10)
GRID_LONS = range(-180, 180, 10)
CHUNK = 100  # locations per request
DATASET = "raw/upper_winds"

WINDS_SCHEMA = pa.schema(
    [
        ("grid_lat", pa.int16()),
        ("grid_lon", pa.int16()),
        ("valid_time", pa.timestamp("us")),
        ("wind_speed_ms", pa.float32()),
        ("wind_direction_deg", pa.float32()),
        ("fetched_at", pa.float64()),
    ]
)


@dataclass
class WeatherReport:
    status: str
    rows: int = 0
    path: str = ""
    reason: str = ""


def grid() -> list[tuple[int, int]]:
    return [(lat, lon) for lat in GRID_LATS for lon in GRID_LONS]


def fetch(
    points: list[tuple[int, int]], client: httpx.Client, fetched_at: float | None = None
) -> pa.Table:
    """Hourly 250 hPa wind for every grid point (Open-Meteo multi-location requests)."""
    fetched_at = fetched_at or time.time()
    rows: dict[str, list[object]] = {name: [] for name in WINDS_SCHEMA.names}
    for start in range(0, len(points), CHUNK):
        chunk = points[start : start + CHUNK]
        response = client.get(
            API_URL,
            params={
                "latitude": ",".join(str(lat) for lat, _ in chunk),
                "longitude": ",".join(str(lon) for _, lon in chunk),
                "hourly": "wind_speed_250hPa,wind_direction_250hPa",
                "wind_speed_unit": "ms",
                "past_days": 1,
                "forecast_days": 1,
                "timezone": "GMT",
            },
        )
        response.raise_for_status()
        payload = response.json()
        # One location -> one object; several -> a list in request order.
        locations = payload if isinstance(payload, list) else [payload]
        for (lat, lon), location in zip(chunk, locations, strict=True):
            hourly = location["hourly"]
            for ts, speed, direction in zip(
                hourly["time"],
                hourly["wind_speed_250hPa"],
                hourly["wind_direction_250hPa"],
                strict=True,
            ):
                if speed is None or direction is None:
                    continue
                rows["grid_lat"].append(lat)
                rows["grid_lon"].append(lon)
                rows["valid_time"].append(datetime.fromisoformat(ts))
                rows["wind_speed_ms"].append(speed)
                rows["wind_direction_deg"].append(direction)
                rows["fetched_at"].append(fetched_at)
    return pa.table(rows, schema=WINDS_SCHEMA)


def already_fetched(filesystem: pafs.FileSystem, base: str, day: str) -> bool:
    selector = pafs.FileSelector(
        f"{base}/{DATASET}/date={day}", recursive=True, allow_not_found=True
    )
    return any(i.path.endswith(".parquet") for i in filesystem.get_file_info(selector))


def update(now: float | None = None, client: httpx.Client | None = None) -> WeatherReport:
    """Fetch today's grid unless it is already in the lake; never raises on API errors."""
    if os.getenv("WEATHER_ENABLED", "true").lower() in {"0", "false", "no"}:
        return WeatherReport("skipped", reason="WEATHER_ENABLED=false")
    now = now or time.time()
    day = datetime.fromtimestamp(now, tz=UTC).strftime("%Y-%m-%d")
    filesystem, base = filesystem_and_base()
    if already_fetched(filesystem, base, day):
        return WeatherReport("skipped", reason=f"winds for {day} already in the lake")
    owns_client = client is None
    client = client or httpx.Client(timeout=60, transport=httpx.HTTPTransport(retries=3))
    try:
        table = fetch(grid(), client, fetched_at=now)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return WeatherReport("failed", reason=f"Open-Meteo: {exc}")
    finally:
        if owns_client:
            client.close()
    path = f"{base}/{DATASET}/date={day}/hour=00/winds-{int(now)}.parquet"
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd")
    filesystem.create_dir(path.rsplit("/", 1)[0], recursive=True)
    with filesystem.open_output_stream(path) as out:
        out.write(buffer.getvalue())
    return WeatherReport("fetched", rows=table.num_rows, path=path)
