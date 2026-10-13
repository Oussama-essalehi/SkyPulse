"""Flight reconstruction quality (spec 7.8.1): share of flights with identified endpoints.

ADS-B carries no origin / destination: dbt reconstructs them from the first / last
low-altitude fix. This module measures how often that works, and tracks it in
MLflow so that every change to the segmentation logic can be compared.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skyradar_ml.marts import connect
from skyradar_ml.tracking import log_run


def reconstruction_metrics(root: Path | None = None) -> dict[str, float]:
    con = connect(root)
    tables = {r[0] for r in con.execute("show tables").fetchall()}
    if "fct_flights" not in tables:
        return {"flights": 0.0}
    row = con.execute(
        """
        select
            count(*),
            coalesce(avg(has_departure::int), 0),
            coalesce(avg(has_arrival::int), 0),
            coalesce(avg(is_complete::int), 0),
            coalesce(median(duration_min), 0),
            coalesce(median(distance_km), 0)
        from fct_flights
        """
    ).fetchone()
    assert row is not None
    keys = (
        "flights",
        "share_with_departure",
        "share_with_arrival",
        "share_complete",
        "median_duration_min",
        "median_distance_km",
    )
    return {k: float(v) for k, v in zip(keys, row, strict=True)}


def track_reconstruction_quality(
    params: dict[str, Any], root: Path | None = None
) -> dict[str, float]:
    metrics = reconstruction_metrics(root)
    log_run("flight-reconstruction", "segmentation-quality", params, metrics)
    return metrics
