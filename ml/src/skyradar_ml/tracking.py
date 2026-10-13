"""MLflow helpers. Tracking is optional: without MLFLOW_TRACKING_URI nothing is logged."""

from __future__ import annotations

import os
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def log_run(
    experiment: str, run_name: str, params: dict[str, Any], metrics: dict[str, float]
) -> str | None:
    """Log one run; returns its id, or None when tracking is disabled / unreachable."""
    if not os.getenv("MLFLOW_TRACKING_URI"):
        log.info("mlflow_disabled", experiment=experiment, metrics=metrics)
        return None
    import mlflow

    try:
        mlflow.set_experiment(experiment)
        with mlflow.start_run(run_name=run_name) as run:
            mlflow.log_params(params)
            mlflow.log_metrics(metrics)
            return str(run.info.run_id)
    except Exception as exc:  # tracking must never break the pipeline
        log.warning("mlflow_logging_failed", error=str(exc))
        return None
