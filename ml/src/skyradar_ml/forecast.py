"""Traffic forecasting: airborne aircraft per zone for the next hours.

Model: Holt-Winters exponential smoothing (statsmodels) with a daily seasonality (24 h)
once two days of history exist, Holt's linear trend before that. Baseline: seasonal
naive (same hour yesterday) or, with less than 48 h, the last observed value.

Evaluation on the last ``holdout`` hours, then refit on all the history and forecast
``horizon`` hours. Output: ``ml_traffic_forecast`` (recent actuals + forecasts, one table
for the "forecast vs actual" chart) and metrics in MLflow / ``ml_model_metrics``.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from skyradar_ml.marts import append_model_metrics, marts_root, read_mart, write_parquet_atomic
from skyradar_ml.tracking import log_run

SEASON = 24


@dataclass
class ForecastResult:
    status: str
    zones: int = 0
    metrics: dict[str, float] = field(default_factory=dict)
    reason: str = ""


def hourly_series(traffic: pd.DataFrame) -> pd.DataFrame:
    """Airborne aircraft per (hour, zone); drops the last, still incomplete, hour."""
    frame = (
        traffic.groupby(["hour", "zone"], as_index=False)["airborne_aircraft"]
        .sum()
        .pivot(index="hour", columns="zone", values="airborne_aircraft")
        .sort_index()
    )
    frame.index = pd.DatetimeIndex(frame.index)
    frame = frame.asfreq("h")
    if len(frame) > 1:
        frame = frame.iloc[:-1]
    # Short ingestion outages: interpolate up to 3 missing hours.
    return pd.DataFrame(frame.interpolate(limit=3).fillna(0.0))


def baseline_forecast(history: np.ndarray, horizon: int) -> np.ndarray:
    if len(history) >= 2 * SEASON:
        season = history[-SEASON:]
        return np.array([season[i % SEASON] for i in range(horizon)], dtype=float)
    return np.repeat(float(history[-1]), horizon)


def model_forecast(history: np.ndarray, horizon: int) -> tuple[np.ndarray, str]:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            if len(history) >= 2 * SEASON:
                fit = ExponentialSmoothing(
                    history, trend="add", seasonal="add", seasonal_periods=SEASON
                ).fit()
                name = "holt_winters_24h"
            else:
                fit = ExponentialSmoothing(history, trend="add", damped_trend=True).fit()
                name = "holt_damped"
            return np.maximum(np.asarray(fit.forecast(horizon), dtype=float), 0.0), name
        except (ValueError, np.linalg.LinAlgError):
            return baseline_forecast(history, horizon), "baseline_fallback"


def run(
    root: Path | None = None, horizon: int = 6, holdout: int = 6, min_hours: int = 12
) -> ForecastResult:
    root = root or marts_root()
    traffic = read_mart("fct_traffic_hourly", root)
    if traffic is None or traffic.empty:
        return ForecastResult("skipped", reason="no traffic history yet")
    series = hourly_series(traffic)
    if len(series) < min_hours:
        return ForecastResult("skipped", reason=f"{len(series)} hours < {min_hours}")

    generated_at = pd.Timestamp.utcnow().tz_localize(None)
    rows: list[pd.DataFrame] = []
    errors: dict[str, tuple[float, float]] = {}
    model_names: set[str] = set()
    for zone in series.columns:
        values = series[zone].to_numpy(dtype=float)
        if values.sum() == 0:
            continue
        train, test = values[:-holdout], values[-holdout:]
        model_pred, _ = model_forecast(train, holdout)
        base_pred = baseline_forecast(train, holdout)
        errors[zone] = (
            float(np.abs(model_pred - test).mean()),
            float(np.abs(base_pred - test).mean()),
        )
        future, name = model_forecast(values, horizon)
        model_names.add(name)
        last = series.index[-1]
        recent = series[zone].iloc[-48:]
        rows.append(
            pd.DataFrame(
                {
                    "zone": zone,
                    "hour": recent.index,
                    "kind": "actual",
                    "value": recent.to_numpy(),
                    "model": None,
                    "generated_at": generated_at,
                }
            )
        )
        rows.append(
            pd.DataFrame(
                {
                    "zone": zone,
                    "hour": pd.date_range(last + pd.Timedelta(hours=1), periods=horizon, freq="h"),
                    "kind": "forecast",
                    "value": np.round(future, 1),
                    "model": name,
                    "generated_at": generated_at,
                }
            )
        )
    if not rows:
        return ForecastResult("skipped", reason="all zones empty")

    write_parquet_atomic(pd.concat(rows, ignore_index=True), root / "ml_traffic_forecast.parquet")
    mae = float(np.mean([e[0] for e in errors.values()]))
    base_mae = float(np.mean([e[1] for e in errors.values()]))
    metrics = {
        "mae_aircraft": mae,
        "baseline_mae_aircraft": base_mae,
        "mae_improvement_pct": 100 * (1 - mae / base_mae) if base_mae else 0.0,
        "history_hours": float(len(series)),
        **{
            f"mae_{z.lower().replace(' ', '_').replace('&', 'and')}": e[0]
            for z, e in errors.items()
        },
    }
    log_run(
        "traffic-forecast",
        "holt-winters-vs-seasonal-naive",
        params={
            "horizon_h": horizon,
            "holdout_h": holdout,
            "models": ",".join(sorted(model_names)),
            "zones": len(errors),
        },
        metrics=metrics,
    )
    append_model_metrics(
        [
            {
                "model": "traffic_forecast",
                "metric": "mae_aircraft",
                "value": mae,
                "baseline_value": base_mae,
                "n_samples": holdout * len(errors),
                "trained_at": generated_at,
            }
        ],
        root,
    )
    return ForecastResult("trained", zones=len(errors), metrics=metrics)
