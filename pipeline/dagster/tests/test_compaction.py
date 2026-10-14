import time
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
import pytest

from skyradar_common.models import StateVector
from skyradar_common.parquet import states_to_table
from skyradar_orchestration.compaction import compact

T0 = 1_760_000_400  # 2025-10-09 09:00 UTC


def write(path: Path, rows: list[StateVector]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(states_to_table(rows), path)


def sv(icao: str, t: int) -> StateVector:
    return StateVector(
        icao24=icao,
        last_contact=t,
        time_position=t,
        latitude=48.0,
        longitude=2.0,
        snapshot_time=t,
        ingested_at=float(t),
    )


def test_compaction_merges_closed_partitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKYRADAR_LAKE_ROOT", str(tmp_path))
    old = tmp_path / "raw" / "aircraft_states" / "date=2025-10-09" / "hour=09"
    write(old / "part-a.parquet", [sv("aaaaaa", T0), sv("bbbbbb", T0)])
    write(old / "part-b.parquet", [sv("aaaaaa", T0)])  # replayed duplicate
    write(old / "part-c.parquet", [sv("cccccc", T0 + 60)])
    recent_hour = time.strftime("%H", time.gmtime())
    recent = (
        tmp_path
        / "raw"
        / "aircraft_states"
        / f"date={time.strftime('%Y-%m-%d', time.gmtime())}"
        / f"hour={recent_hour}"
    )
    write(recent / "part-x.parquet", [sv("dddddd", int(time.time()))])
    write(recent / "part-y.parquet", [sv("eeeeee", int(time.time()))])

    report = compact(min_age_hours=2)
    assert report.partitions == 1
    assert report.files_before == 3
    assert report.rows == 3
    remaining = sorted(p.name for p in old.iterdir())
    assert len(remaining) == 1
    assert remaining[0].startswith("compacted-")
    assert len(list(recent.iterdir())) == 2  # open partition untouched
    count = duckdb.sql(f"select count(*) from read_parquet('{old}/*.parquet')").fetchone()
    assert count == (3,)
    # Idempotent: a second run has nothing to do.
    assert compact(min_age_hours=2).partitions == 0
    # Same schema as the sink's files (no hive columns duplicated inside the file).
    from skyradar_common.parquet import STATES_SCHEMA

    compacted = next(old.iterdir())
    assert pq.read_schema(compacted).names == STATES_SCHEMA.names
