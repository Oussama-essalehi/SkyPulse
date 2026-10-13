from pathlib import Path

import pytest

MANIFEST = Path(__file__).parents[2] / "dbt" / "target" / "manifest.json"


@pytest.mark.skipif(not MANIFEST.exists(), reason="run `dbt parse` first (make dbt-parse)")
def test_definitions_load() -> None:
    from skyradar_orchestration.definitions import defs

    repo = defs.get_repository_def()
    keys = {k.to_user_string() for k in repo.asset_graph.get_all_asset_keys()}
    assert {"lake/aircraft_states", "fct_flights", "flight_reconstruction_quality"} <= keys
    assert {"dbt_hourly", "ml_daily"} <= {j.name for j in repo.get_all_jobs()}
    # dbt tests are exposed as Dagster asset checks
    assert len(list(repo.asset_graph.asset_check_keys)) > 20
