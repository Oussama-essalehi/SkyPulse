from pathlib import Path

import duckdb
from pyarrow import fs as pafs

from skyradar_common.models import Alert, AlertType, Severity, StateVector
from skyradar_common.topics import AIRCRAFT_ALERTS, AIRCRAFT_STATES
from skyradar_streaming.parquet_sink.sink import ParquetSink, partition_of

T0 = 1_760_000_000  # 2025-10-09 08:53:20 UTC


def state(icao: str, t: int) -> bytes:
    return StateVector(
        icao24=icao,
        last_contact=t,
        time_position=t,
        longitude=2.0,
        latitude=48.0,
        snapshot_time=t,
        ingested_at=float(t),
    ).to_bytes()


def test_partition_of() -> None:
    assert partition_of(T0) == ("2025-10-09", "08")


def test_flush_writes_hive_partitions_readable_by_duckdb(tmp_path: Path) -> None:
    sink = ParquetSink(pafs.LocalFileSystem(), str(tmp_path))
    for offset, t in enumerate([T0, T0 + 10, T0 + 3600]):
        sink.add(AIRCRAFT_STATES, 0, offset, state("abc123", t))
    sink.add(AIRCRAFT_STATES, 1, 0, b"not json")  # skipped, offset still tracked
    alert = Alert(
        alert_id="x",
        icao24="abc123",
        type=AlertType.SQUAWK_EMERGENCY,
        severity=Severity.CRITICAL,
        message="m",
        event_time=T0,
        detected_at=T0,
        details={"squawk": "7700"},
    )
    sink.add(AIRCRAFT_ALERTS, 0, 0, alert.to_bytes())
    assert sink.buffered == 4

    paths = sink.flush()
    assert len(paths) == 3
    assert sink.buffered == 0
    assert sink.flush() == []

    con = duckdb.connect()
    rows = con.execute(
        "select date, hour, count(*) "
        f"from read_parquet('{tmp_path}/raw/aircraft_states/**/*.parquet', hive_partitioning=true)"
        " group by all order by all"
    ).fetchall()
    assert [(str(d), str(h).zfill(2), n) for d, h, n in rows] == [
        ("2025-10-09", "08", 2),
        ("2025-10-09", "09", 1),
    ]
    details = con.execute(
        f"select details_json from read_parquet('{tmp_path}/raw/alerts/**/*.parquet')"
    ).fetchone()
    assert details == ('{"squawk":"7700"}',)


def test_batch_file_names_are_deterministic(tmp_path: Path) -> None:
    names = []
    for run in range(2):
        sink = ParquetSink(pafs.LocalFileSystem(), str(tmp_path / str(run)))
        sink.add(AIRCRAFT_STATES, 0, 42, state("abc123", T0))
        names.append(Path(sink.flush()[0]).name)
    assert names[0] == names[1]
