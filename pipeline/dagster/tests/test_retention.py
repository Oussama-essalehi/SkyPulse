from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from skyradar_orchestration.retention import purge, retention_days


def write(lake: Path, dataset: str, date: str, hour: str, name: str = "part.parquet") -> Path:
    directory = lake / "raw" / dataset / f"date={date}" / f"hour={hour}"
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"icao24": ["abcdef"]}), directory / name)
    return directory


def test_purge_drops_partitions_older_than_the_newest_minus_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKYRADAR_LAKE_ROOT", str(tmp_path))
    old = write(tmp_path, "aircraft_states", "2025-09-01", "10")
    write(tmp_path, "aircraft_states", "2025-09-01", "11", "other.parquet")
    edge = write(tmp_path, "aircraft_states", "2025-09-09", "12")  # exactly 30 days: kept
    newest = write(tmp_path, "aircraft_states", "2025-10-09", "12")
    alerts = write(tmp_path, "alerts", "2025-09-01", "10")

    report = purge({"raw/aircraft_states": 30, "raw/alerts": 365})

    assert report.partitions_deleted == 2
    assert report.files_deleted == 2
    assert report.bytes_deleted > 0
    assert not old.exists()
    assert not old.parent.exists()  # empty day folder removed too
    assert edge.exists()
    assert newest.exists()
    assert alerts.exists()  # longer retention


def test_purge_is_anchored_on_data_not_on_the_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An old replay capture: every partition is years old, none of them is expired.
    monkeypatch.setenv("SKYRADAR_LAKE_ROOT", str(tmp_path))
    kept = write(tmp_path, "aircraft_states", "2020-01-01", "00")
    write(tmp_path, "aircraft_states", "2020-01-02", "00")
    assert purge({"raw/aircraft_states": 30}).partitions_deleted == 0
    assert kept.exists()


def test_purge_disabled_and_empty_lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKYRADAR_LAKE_ROOT", str(tmp_path))
    assert purge({"raw/aircraft_states": 30}).partitions_deleted == 0  # nothing yet
    old = write(tmp_path, "aircraft_states", "2025-01-01", "00")
    write(tmp_path, "aircraft_states", "2025-10-09", "00")
    assert purge({"raw/aircraft_states": 0}).partitions_deleted == 0  # 0 keeps everything
    assert old.exists()


def test_retention_days_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAKE_RETENTION_DAYS", "7")
    assert retention_days()["raw/aircraft_states"] == 7
    assert retention_days()["raw/alerts"] == 365
