"""Dagster definitions: raw lake -> dbt models (+ dbt tests as asset checks)
-> Soda data contracts -> ML features (dbt) -> trained models (MLflow) -> scores / alerts,
with an hourly (dbt + quality + scoring) and a daily (training) schedule."""

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

from skyradar_ml import endpoints, eta, forecast, outliers
from skyradar_ml.marts import read_mart
from skyradar_ml.reconstruction import track_reconstruction_quality
from skyradar_orchestration import alerts, compaction
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


def _meta(values: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, bool) or value is None:
            out[key] = str(value)
        elif isinstance(value, int | float):
            out[key] = MetadataValue.float(float(value))
        else:
            out[key] = str(value)
    return out


@asset(
    deps=[AssetKey(["fct_flights"])],
    group_name="ml_training",
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
    return MaterializeResult(metadata=_meta(metrics))


@asset(
    deps=[AssetKey(["ml_eta_features"])],
    group_name="ml_training",
    kinds={"lightgbm", "mlflow"},
    description="LightGBM ETA model vs naive baseline; trains once history is sufficient.",
)
def eta_model(context: AssetExecutionContext) -> MaterializeResult:
    result = eta.train()
    context.log.info(f"ETA: {result.status} {result.reason} {result.metrics}")
    return MaterializeResult(
        metadata=_meta(
            {
                "status": result.status,
                "reason": result.reason,
                "promoted": result.promoted,
                "n_flights": result.n_flights,
                "n_rows": result.n_rows,
                **result.metrics,
            }
        )
    )


@asset(
    deps=[AssetKey(["fct_traffic_hourly"])],
    group_name="ml_scoring",
    kinds={"statsmodels", "mlflow"},
    description="Airborne aircraft per zone, next 6 hours (Holt-Winters vs seasonal naive).",
)
def traffic_forecast(context: AssetExecutionContext) -> MaterializeResult:
    result = forecast.run()
    context.log.info(f"forecast: {result.status} {result.reason}")
    return MaterializeResult(
        metadata=_meta(
            {
                "status": result.status,
                "reason": result.reason,
                "zones": result.zones,
                **result.metrics,
            }
        )
    )


@asset(
    deps=[AssetKey(["ml_flight_features"])],
    group_name="ml_scoring",
    kinds={"sklearn", "mlflow"},
    description="Isolation Forest trajectory scores; recent outliers published as alerts.",
)
def trajectory_outliers(context: AssetExecutionContext) -> MaterializeResult:
    result = outliers.score()
    published = 0
    if result.scores is not None:
        published = alerts.publish(alerts.outlier_alerts(result.scores))
    context.log.info(f"outliers: {result.n_outliers}/{result.n_flights}, published {published}")
    return MaterializeResult(
        metadata=_meta(
            {
                "status": result.status,
                "reason": result.reason,
                "n_flights": result.n_flights,
                "n_outliers": result.n_outliers,
                "alerts_published": published,
            }
        )
    )


@asset(
    deps=[AssetKey(["ml_unmatched_endpoints"])],
    group_name="ml_scoring",
    kinds={"sklearn", "mlflow"},
    description="DBSCAN clusters of take-offs / landings far from any known airport.",
)
def endpoint_clusters(context: AssetExecutionContext) -> MaterializeResult:
    result = endpoints.run()
    context.log.info(
        f"endpoint clusters: {result.status} {result.reason} "
        f"{result.n_clusters} clusters, {result.n_noise}/{result.n_endpoints} noise"
    )
    return MaterializeResult(
        metadata=_meta(
            {
                "status": result.status,
                "reason": result.reason,
                "n_endpoints": result.n_endpoints,
                "n_clusters": result.n_clusters,
                "n_noise": result.n_noise,
            }
        )
    )


@asset(
    deps=[AssetKey(["fct_diversions"])],
    group_name="ml_scoring",
    kinds={"python", "kafka"},
    description="Recent diversions published to aircraft.alerts.",
)
def diversion_alerts(context: AssetExecutionContext) -> MaterializeResult:
    diversions = read_mart("fct_diversions")
    found = 0 if diversions is None else len(diversions)
    published = 0 if diversions is None else alerts.publish(alerts.diversion_alerts(diversions))
    context.log.info(f"diversions: {found} in history, {published} published")
    return MaterializeResult(metadata=_meta({"diversions": found, "alerts_published": published}))


@asset(
    deps=[RAW_STATES, RAW_ALERTS],
    group_name="maintenance",
    kinds={"parquet", "s3"},
    description="Merge the small Parquet files of closed lake partitions into one file each.",
)
def lake_compaction(context: AssetExecutionContext) -> MaterializeResult:
    report = compaction.compact(min_age_hours=compaction.compaction_min_age_hours())
    for line in report.details:
        context.log.info(line)
    return MaterializeResult(
        metadata=_meta(
            {
                "partitions": report.partitions,
                "files_before": report.files_before,
                "files_after": report.files_after,
                "rows": report.rows,
            }
        )
    )


dbt_hourly_job = define_asset_job(
    "dbt_hourly",
    selection=(
        AssetSelection.assets(skyradar_dbt)
        | AssetSelection.checks(*soda_checks)
        | AssetSelection.groups("ml_scoring")
    ),
    description="Soda on the raw lake, dbt build (+ tests), Soda on marts, then scoring: "
    "traffic forecast, trajectory outliers and diversion alerts.",
)
ml_daily_job = define_asset_job(
    "ml_daily",
    selection=AssetSelection.groups("ml_training"),
    description="Model training / evaluation tracked in MLflow (ETA, reconstruction quality).",
)

maintenance_job = define_asset_job(
    "lake_maintenance",
    selection=AssetSelection.groups("maintenance"),
    description="Daily compaction of the raw lake.",
)

defs = Definitions(
    assets=[
        *raw_lake,
        skyradar_dbt,
        flight_reconstruction_quality,
        eta_model,
        traffic_forecast,
        trajectory_outliers,
        endpoint_clusters,
        diversion_alerts,
        lake_compaction,
    ],
    asset_checks=soda_checks,
    jobs=[dbt_hourly_job, ml_daily_job, maintenance_job],
    schedules=[
        ScheduleDefinition(
            job=dbt_hourly_job,
            cron_schedule="5 * * * *",
            default_status=DefaultScheduleStatus.RUNNING,
        ),
        ScheduleDefinition(
            job=maintenance_job,
            cron_schedule="15 2 * * *",
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
