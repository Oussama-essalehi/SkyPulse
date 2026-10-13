"""Build a small, deterministic local lake for dbt tests / CI (no network needed).

Synthetic traffic -> the real ParquetSink (local filesystem) -> hive-partitioned
Parquet, plus alerts produced by the real AnomalyDetector, plus an airports.csv
fixture with the OurAirports columns.

    uv run python scripts/build_test_lake.py --root data/test_lake
    SKYRADAR_LAKE_ROOT=data/test_lake SKYRADAR_REFERENCE_ROOT=data/test_lake/reference dbt build
"""

from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path

import pyarrow.parquet as pq
from pyarrow import fs as pafs

from skyradar_common.parquet import table_to_states
from skyradar_common.topics import AIRCRAFT_ALERTS, AIRCRAFT_STATES
from skyradar_ingestion.synthetic import AIRPORTS, generate
from skyradar_streaming.anomaly.detector import AnomalyDetector
from skyradar_streaming.parquet_sink.sink import ParquetSink

AIRPORT_COLUMNS = [
    "id",
    "ident",
    "type",
    "name",
    "latitude_deg",
    "longitude_deg",
    "elevation_ft",
    "continent",
    "iso_country",
    "iso_region",
    "municipality",
    "scheduled_service",
    "icao_code",
    "iata_code",
    "gps_code",
    "local_code",
    "home_link",
    "wikipedia_link",
    "keywords",
]


def write_airports(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AIRPORT_COLUMNS)
        writer.writeheader()
        for i, (icao, lat, lon) in enumerate(AIRPORTS, start=1):
            writer.writerow(
                {
                    "id": i,
                    "ident": icao,
                    "type": "large_airport",
                    "name": f"{icao} airport",
                    "latitude_deg": lat,
                    "longitude_deg": lon,
                    "elevation_ft": 100,
                    "iso_country": icao[:2],
                    "scheduled_service": "yes",
                    "icao_code": icao,
                    "gps_code": icao,
                }
            )


def build(root: Path, minutes: int, start_time: int) -> None:
    if root.exists():
        shutil.rmtree(root)
    replay = generate(
        root / "_replay.parquet",
        flights=400,
        duration_s=minutes * 60,
        interval_s=30,
        start_time=start_time,
    )
    states = table_to_states(pq.read_table(replay))
    sink = ParquetSink(pafs.LocalFileSystem(), str(root.resolve()))
    detector = AnomalyDetector()
    states.sort(key=lambda s: (s.snapshot_time, s.icao24))
    alert_offset = 0
    for offset, state in enumerate(states):
        sink.add(AIRCRAFT_STATES, 0, offset, state.to_bytes())
        for alert in detector.process(state, now=state.snapshot_time + 1.0):
            sink.add(AIRCRAFT_ALERTS, 0, alert_offset, alert.to_bytes())
            alert_offset += 1
        if offset and offset % 20_000 == 0:
            sink.flush()  # several files per partition, like the real sink
    sink.flush()
    replay.unlink()
    write_airports(root / "reference" / "airports.csv")
    print(f"Test lake ready in {root} ({len(states)} states, {alert_offset} alert events)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/test_lake"))
    parser.add_argument("--minutes", type=int, default=150)
    parser.add_argument("--start-time", type=int, default=1_760_000_400, help="unix time")
    args = parser.parse_args()
    build(args.root, args.minutes, args.start_time)


if __name__ == "__main__":
    main()
