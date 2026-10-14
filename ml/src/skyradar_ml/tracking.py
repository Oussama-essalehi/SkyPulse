"""MLflow helpers. Tracking is optional: without MLFLOW_TRACKING_URI nothing is logged,
and a tracking failure never breaks the pipeline."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def tracking_enabled() -> bool:
    return bool(os.getenv("MLFLOW_TRACKING_URI"))


def log_run(
    experiment: str,
    run_name: str,
    params: dict[str, Any],
    metrics: dict[str, float],
    log_model: Callable[[], None] | None = None,
    tags: dict[str, str] | None = None,
    artifacts: dict[str, Any] | None = None,
) -> str | None:
    """Log one run (params, metrics, optional model / JSON artifacts); returns its id."""
    if not tracking_enabled():
        log.info("mlflow_disabled", experiment=experiment, metrics=metrics)
        return None
    import mlflow

    try:
        mlflow.set_experiment(experiment)
        with mlflow.start_run(run_name=run_name) as run:
            mlflow.log_params(params)
            mlflow.log_metrics(metrics)
            if tags:
                mlflow.set_tags(tags)
            for name, payload in (artifacts or {}).items():
                mlflow.log_dict(payload, name)
            if log_model is not None:
                log_model()
            return str(run.info.run_id)
    except Exception as exc:  # tracking must never break the pipeline
        log.warning("mlflow_logging_failed", experiment=experiment, error=str(exc))
        return None
