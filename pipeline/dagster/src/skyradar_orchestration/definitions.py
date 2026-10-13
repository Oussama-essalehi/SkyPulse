"""Dagster definitions: raw lake -> dbt models (+ dbt tests as asset checks)
-> data quality checks -> ML assets, with hourly / daily schedules."""

# No `from __future__ import annotations`: Dagster inspects runtime type hints.
import os
import time
from pathlib import Path
from typing import Any

import duckdb
from dagster import (
    AssetCheckResult,
    AssetCheckSeverity,
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
    asset_check,
    define_asset_job,
)
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets

from skyradar_ml.reconstruction import track_reconstruction_quality

DBT_PROJECT_DIR = Path(os.getenv("DBT_PROJECT_DIR", Path(__file__).parents[3] / "dbt"))
dbt_project = DbtProject(project_dir=DBT_PROJECT_DIR, profiles_dir=DBT_PROJECT_DIR)
dbt_project.prepare_if_dev()

RAW_STATES = AssetKey(["lake", "aircraft_states"])
RAW_ALERTS = AssetKey(["lake", "alerts"])
FRESHNESS_MAX_AGE_S = int(os.getenv("DQ_FRESHNESS_MAX_AGE_SECONDS", "1800"))

# The raw lake is written continuously by the streaming sink (outside Dagster):
# declared as external assets so the lineage starts at the Parquet files.
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


def _lake_connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    lake_root = os.getenv("SKYRADAR_LAKE_ROOT", "s3://skyradar-lake")
    if lake_root.startswith("s3://"):
        con.execute(
            """
            create secret lake (type s3, key_id $key, secret $secret, endpoint $endpoint,
                                region $region, url_style 'path', use_ssl $ssl)
            """,
            {
                "key": os.getenv("S3_ACCESS_KEY", "skyradar"),
                "secret": os.getenv("S3_SECRET_KEY", "skyradar-secret"),
                "endpoint": os.getenv("S3_ENDPOINT_HOST", "localhost:8333"),
                "region": os.getenv("S3_REGION", "us-east-1"),
                "ssl": os.getenv("S3_USE_SSL", "false").lower() == "true",
            },
        )
    return con


@asset_check(asset=RAW_STATES, description="Latest lake partition is recent (sink is alive).")
def raw_states_freshness() -> AssetCheckResult:
    lake_root = os.getenv("SKYRADAR_LAKE_ROOT", "s3://skyradar-lake")
    con = _lake_connection()
    files = con.execute(
        f"select count(*) from glob('{lake_root}/raw/aircraft_states/*/*/*.parquet')"
    ).fetchone()
    if not files or files[0] == 0:
        return AssetCheckResult(
            passed=False, severity=AssetCheckSeverity.WARN, metadata={"reason": "lake is empty"}
        )
    row = con.execute(
        f"""
        select max(snapshot_time), count(*)
        from read_parquet('{lake_root}/raw/aircraft_states/*/*/*.parquet', hive_partitioning = true)
        where "date" >= current_date - 1
        """
    ).fetchone()
    latest, rows_2d = row or (None, 0)
    age = time.time() - latest if latest else float("inf")
    return AssetCheckResult(
        passed=age <= FRESHNESS_MAX_AGE_S,
        severity=AssetCheckSeverity.WARN,
        metadata={
            "latest_snapshot_age_s": MetadataValue.float(round(age, 1)),
            "rows_last_2_days": MetadataValue.int(int(rows_2d or 0)),
            "threshold_s": MetadataValue.int(FRESHNESS_MAX_AGE_S),
        },
    )


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
    selection=AssetSelection.assets(skyradar_dbt) | AssetSelection.checks(raw_states_freshness),
    description="Lake freshness check, then rebuild staging / intermediate / marts + dbt tests.",
)
ml_daily_job = define_asset_job(
    "ml_daily",
    selection=AssetSelection.groups("ml"),
    description="Model training / evaluation tracked in MLflow.",
)

defs = Definitions(
    assets=[*raw_lake, skyradar_dbt, flight_reconstruction_quality],
    asset_checks=[raw_states_freshness],
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
