"""Build a small, deterministic local lake for dbt tests / CI (no network needed).

Synthetic traffic -> the real ParquetSink (local filesystem) -> hive-partitioned
Parquet, plus alerts produced by the real AnomalyDetector, plus reference fixtures with
the real file layouts: OurAirports airports.csv, OpenSky aircraft database and ICAO
aircraft types, OpenFlights airlines.dat.

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
from skyradar_ingestion.synthetic import AIRLINES, AIRPORTS, generate
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


# Header of the OpenSky aircraftDatabase.csv.
AIRCRAFT_DB_COLUMNS = [
    "icao24", "registration", "manufacturericao", "manufacturername", "model", "typecode",
    "serialnumber", "linenumber", "icaoaircrafttype", "operator", "operatorcallsign",
    "operatoricao", "operatoriata", "owner", "testreg", "registered", "reguntil", "status",
    "built", "firstflightdate", "seatconfiguration", "engines", "modes", "adsb", "acars",
    "notes", "categoryDescription",
]  # fmt: skip
# (designator, manufacturer, model, ICAO class, wake category)
AIRCRAFT_TYPES = [
    ("A320", "AIRBUS", "A320-214", "L2J", "M"),
    ("B738", "BOEING", "737-8AS", "L2J", "M"),
    ("A359", "AIRBUS", "A350-941", "L2J", "H"),
    ("B77W", "BOEING", "777-300ER", "L2J", "H"),
    ("E190", "EMBRAER", "ERJ 190-100 STD", "L2J", "M"),
    ("A388", "AIRBUS", "A380-841", "L4J", "J"),
]
AIRLINE_NAMES = {
    "AFR": "Air France", "BAW": "British Airways", "DLH": "Lufthansa",
    "KLM": "KLM Royal Dutch Airlines", "IBE": "Iberia Airlines", "UAE": "Emirates",
    "QTR": "Qatar Airways", "DAL": "Delta Air Lines", "UAL": "United Airlines",
    "AAL": "American Airlines", "ACA": "Air Canada", "JAL": "Japan Airlines",
    "SIA": "Singapore Airlines", "CPA": "Cathay Pacific", "EZY": "easyJet",
    "RYR": "Ryanair", "THY": "Turkish Airlines", "SWR": "Swiss International Air Lines",
    "RAM": "Royal Air Maroc", "QFA": "Qantas",
}  # fmt: skip


def write_aircraft_reference(directory: Path, icao24s: list[str]) -> None:
    """Every 7th aircraft is unknown to the database, like real-world gaps."""
    with (directory / "aircraft_database.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AIRCRAFT_DB_COLUMNS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerow({})  # the real file starts with an empty row
        for i, icao in enumerate(icao24s):
            if i % 7 == 0:
                continue
            code, maker, model, klass, _ = AIRCRAFT_TYPES[i % len(AIRCRAFT_TYPES)]
            writer.writerow(
                {
                    "icao24": icao,
                    "registration": f"F-T{i:04d}",
                    "manufacturericao": maker,
                    "model": model,
                    "typecode": code,
                    "icaoaircrafttype": klass,
                    "built": f"{2000 + i % 24}-01-01",
                }
            )
    with (directory / "aircraft_types.csv").open("w", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL)
        writer.writerow(
            [
                "AircraftDescription", "Description", "Designator", "EngineCount",
                "EngineType", "ManufacturerCode", "ModelFullName", "WTC",
            ]
        )  # fmt: skip
        for code, maker, model, klass, wtc in AIRCRAFT_TYPES:
            writer.writerow(["LandPlane", klass, code, klass[1], "Jet", maker, model, wtc])
            # Several model names share a designator in the real file.
            writer.writerow(
                ["LandPlane", klass, code, klass[1], "Jet", maker, f"{model} (v2)", wtc]
            )


def write_airlines(path: Path) -> None:
    """OpenFlights layout: no header, \\N for nulls, defunct duplicates of active codes."""
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerow([-1, "Unknown", "\\N", "-", "N/A", "\\N", "\\N", "Y"])
        writer.writerow([1, "Defunct Air France", "\\N", "", "AFR", "", "France", "N"])
        for i, (code, country) in enumerate(AIRLINES, start=10):
            name = AIRLINE_NAMES.get(code, code)
            writer.writerow([i, name, "\\N", code[:2], code, name.upper(), country, "Y"])


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
    # One ADS-B glitch (a fix 60 degrees away, between two real fixes): the dbt models
    # must drop it (int_flight_segments), otherwise the step_km test fails.
    victim = next(s for s in states if not s.on_ground and s.velocity)
    glitch = victim.model_copy(
        update={
            "longitude": (victim.longitude + 240) % 360 - 180,
            "time_position": (victim.time_position or victim.last_contact) + 5,
            "last_contact": victim.last_contact + 5,
        }
    )
    sink.add(AIRCRAFT_STATES, 1, 0, glitch.to_bytes())
    sink.flush()
    replay.unlink()
    write_airports(root / "reference" / "airports.csv")
    write_aircraft_reference(root / "reference", sorted({s.icao24 for s in states}))
    write_airlines(root / "reference" / "airlines.dat")
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
