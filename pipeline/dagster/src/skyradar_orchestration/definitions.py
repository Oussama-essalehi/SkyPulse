"""Dagster definitions: raw lake -> dbt models (+ dbt tests as asset checks)
-> data quality checks -> ML assets, with hourly / daily schedules."""

# No `from __future__ import annotations`: Dagster inspects runtime type hints.
import os
from pathlib import Path
from typing import Any

from dagster import (
    AssetExecutionContext,
    AssetKey,
    AssetSelection,
    AssetSpec,
    DefaultScheduleStatus,
    Definitions,
    MaterializeResult,
    MetadataValue,
    ScheduleDefinition,
    asset,
    define_asset_job,
)
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets

from skyradar_ml.reconstruction import track_reconstruction_quality
from skyradar_orchestration.quality import build_soda_checks

DBT_PROJECT_DIR = Path(os.getenv("DBT_PROJECT_DIR", Path(__file__).parents[3] / "dbt"))
dbt_project = DbtProject(project_dir=DBT_PROJECT_DIR, profiles_dir=DBT_PROJECT_DIR)
dbt_project.prepare_if_dev()

RAW_STATES = AssetKey(["lake", "aircraft_states"])
RAW_ALERTS = AssetKey(["lake", "alerts"])

# The raw lake is written continuously by the streaming sink (outside Dagster):
# declared as external assets so the lineage starts at the Parquet files.
soda_checks = build_soda_checks()

raw_lake = [
    AssetSpec(
        RAW_STATES,
        description="Raw state vectors (Parquet, date=/hour= partitions)",
        group_name="lake",
        kinds={"parquet", "s3"},
    ),
    AssetSpec(
        RAW_ALERTS,
        description="Raw alert events (Parquet)",
        group_name="lake",
        kinds={"parquet", "s3"},
    ),
]


@dbt_assets(manifest=dbt_project.manifest_path, project=dbt_project)
def skyradar_dbt(context: AssetExecutionContext, dbt: DbtCliResource) -> Any:
    """All dbt models; every dbt test becomes a Dagster asset check."""
    yield from dbt.cli(["build"], context=context).stream()


@asset(
    deps=[AssetKey(["fct_flights"])],
    group_name="ml",
    kinds={"python", "mlflow"},
    description="Share of reconstructed flights with identified departure / arrival (MLflow).",
)
def flight_reconstruction_quality(context: AssetExecutionContext) -> MaterializeResult:
    params = {
        "flight_gap_seconds": 1800,
        "endpoint_max_altitude_m": 1500,
        "endpoint_max_distance_km": 15,
    }
    metrics = track_reconstruction_quality(params)
    context.log.info(f"reconstruction metrics: {metrics}")
    return MaterializeResult(metadata={k: MetadataValue.float(v) for k, v in metrics.items()})


dbt_hourly_job = define_asset_job(
    "dbt_hourly",
    selection=AssetSelection.assets(skyradar_dbt) | AssetSelection.checks(*soda_checks),
    description="Soda contracts on the raw lake, dbt build (models + tests), Soda on marts.",
)
ml_daily_job = define_asset_job(
    "ml_daily",
    selection=AssetSelection.groups("ml"),
    description="Model training / evaluation tracked in MLflow.",
)

defs = Definitions(
    assets=[*raw_lake, skyradar_dbt, flight_reconstruction_quality],
    asset_checks=soda_checks,
    jobs=[dbt_hourly_job, ml_daily_job],
    schedules=[
        ScheduleDefinition(
            job=dbt_hourly_job,
            cron_schedule="5 * * * *",
            default_status=DefaultScheduleStatus.RUNNING,
        ),
        ScheduleDefinition(
            job=ml_daily_job,
            cron_schedule="30 3 * * *",
            default_status=DefaultScheduleStatus.RUNNING,
        ),
    ],
    resources={"dbt": DbtCliResource(project_dir=dbt_project)},
)
