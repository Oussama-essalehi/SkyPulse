from pathlib import Path

import pytest

MANIFEST = Path(__file__).parents[2] / "dbt" / "target" / "manifest.json"


@pytest.mark.skipif(not MANIFEST.exists(), reason="run `dbt parse` first (make dbt-parse)")
def test_definitions_load() -> None:
    from skyradar_orchestration.definitions import defs

    repo = defs.get_repository_def()
    keys = {k.to_user_string() for k in repo.asset_graph.get_all_asset_keys()}
    assert {
        "lake/aircraft_states",
        "fct_flights",
        "flight_reconstruction_quality",
        "eta_model",
        "traffic_forecast",
        "trajectory_outliers",
        "endpoint_clusters",
        "diversion_alerts",
        "lake_compaction",
        "ml_eta_features",
        "ml_flight_features",
        "fct_diversions",
        "dim_airports",
    } <= keys
    jobs = {j.name: j for j in repo.get_all_jobs()}
    assert {"dbt_hourly", "ml_daily", "lake_maintenance"} <= set(jobs)
    # Every job selects something (an empty selection is a wiring bug).
    for name in ("dbt_hourly", "ml_daily", "lake_maintenance"):
        assert jobs[name].asset_layer.executable_asset_keys, name
    assert len(repo.schedule_defs) == 3
    # dbt tests are exposed as Dagster asset checks
    assert len(list(repo.asset_graph.asset_check_keys)) > 20
