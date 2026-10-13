from pathlib import Path

import duckdb

from skyradar_ml.reconstruction import reconstruction_metrics


def test_metrics_on_empty_marts(tmp_path: Path) -> None:
    assert reconstruction_metrics(tmp_path) == {"flights": 0.0}


def test_metrics(tmp_path: Path) -> None:
    duckdb.sql(
        "copy (select * from (values (true, true, true, 60.0, 500.0),"
        " (true, false, false, 30.0, 200.0))"
        " as t(has_departure, has_arrival, is_complete, duration_min, distance_km))"
        f" to '{tmp_path}/fct_flights.parquet' (format parquet)"
    )
    metrics = reconstruction_metrics(tmp_path)
    assert metrics["flights"] == 2
    assert metrics["share_with_departure"] == 1.0
    assert metrics["share_complete"] == 0.5
