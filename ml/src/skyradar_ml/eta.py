"""ETA prediction: LightGBM vs a naive physics baseline (remaining distance / speed).

* Training rows come from the dbt model ``ml_eta_features``: every airborne position of
  a reconstructed flight whose arrival airport and time are known.
* Train / test split **by flight and by time** (the most recent 20 % of flights are the
  test set): positions of one flight never leak between the two sets.
* The model only trains once the history is large enough (``ML_ETA_MIN_FLIGHTS``);
  until then the API serves the naive estimate.
* The model learns a **multiplicative correction of the baseline** (target =
  true / naive, clipped): with little data it stays close to the physics, with more it
  learns what the baseline ignores (descent and approach, winds, holding).
* Metrics (MAE in minutes, MAPE) of both the model and the baseline go to MLflow, the
  model to the MLflow registry (``skyradar-eta``) and, for low-latency serving, to
  ``<marts>/models/eta_model.txt`` + ``eta_model.json`` read by the API.
* **Promotion gate**: a model that does not beat the baseline on the test set is logged
  but not served; a previously served model is withdrawn and the API falls back to the
  naive estimate.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from skyradar_ml.marts import append_model_metrics, marts_root, read_mart
from skyradar_ml.tracking import log_run

NUMERIC_FEATURES = [
    "naive_eta_min",
    "remaining_km",
    "velocity_ms",
    "altitude_m",
    "vertical_rate_ms",
    "heading_alignment",
    "hour_utc",
    "day_of_week",
    "elapsed_min",
]
CATEGORICAL_FEATURE = "arrival_airport_code"
FEATURES = [*NUMERIC_FEATURES, CATEGORICAL_FEATURE]
MODEL_NAME = "skyradar-eta"
RATIO_CLIP = (0.2, 5.0)


@dataclass
class EtaResult:
    status: str  # "trained" | "skipped"
    reason: str = ""
    n_flights: int = 0
    n_rows: int = 0
    metrics: dict[str, float] = field(default_factory=dict)
    model_path: str | None = None
    # Served by the API only if it beat the baseline on the test set.
    promoted: bool = False


def should_promote(metrics: dict[str, float]) -> bool:
    """Serve the model only if it beats the naive baseline on the test set."""
    return metrics["mae_min"] < metrics["baseline_mae_min"]


def naive_eta_seconds(remaining_km: np.ndarray, velocity_ms: np.ndarray) -> np.ndarray:
    """Baseline: time to cover the great-circle distance at the current ground speed."""
    return np.asarray(remaining_km * 1000.0 / np.maximum(velocity_ms, 30.0))


def split_by_time(df: pd.DataFrame, test_share: float = 0.2) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The most recent flights (by arrival time) form the test set."""
    order = df.groupby("flight_id")["arrival_time"].max().sort_values()
    n_test = max(1, int(len(order) * test_share))
    test_ids = set(order.index[-n_test:])
    is_test = df["flight_id"].isin(test_ids)
    return df[~is_test], df[is_test]


def _metrics(y: np.ndarray, pred: np.ndarray, prefix: str) -> dict[str, float]:
    err = np.abs(pred - y)
    return {
        f"{prefix}mae_min": float(err.mean() / 60),
        f"{prefix}mape": float((err / y).mean()),
        f"{prefix}median_ae_min": float(np.median(err) / 60),
    }


def prepare(df: pd.DataFrame, codes: dict[str, int]) -> pd.DataFrame:
    """Add the baseline, the encoded airport and the correction-ratio target."""
    out = df.copy()
    out[CATEGORICAL_FEATURE] = out["arrival_airport"].map(codes).fillna(-1).astype("int32")
    naive = naive_eta_seconds(
        out["remaining_km"].to_numpy(dtype=float), out["velocity_ms"].to_numpy(dtype=float)
    )
    out["naive_eta_min"] = naive / 60
    if "target_seconds" in out:
        out["target_ratio"] = np.clip(out["target_seconds"] / naive, *RATIO_CLIP)
    return out


def predict_seconds(model: Any, frame: pd.DataFrame) -> np.ndarray:
    """ETA = baseline x learned correction."""
    ratio = np.clip(model.predict(frame[FEATURES]), *RATIO_CLIP)
    return np.asarray(frame["naive_eta_min"].to_numpy(dtype=float) * 60 * ratio)


def train(
    root: Path | None = None,
    min_flights: int | None = None,
    params: dict[str, Any] | None = None,
) -> EtaResult:
    import lightgbm as lgb

    root = root or marts_root()
    min_flights = (
        min_flights if min_flights is not None else int(os.getenv("ML_ETA_MIN_FLIGHTS", "200"))
    )
    df = read_mart("ml_eta_features", root)
    if df is None or df.empty:
        return EtaResult("skipped", "no training data yet")
    n_flights = int(df["flight_id"].nunique())
    if n_flights < min_flights:
        return EtaResult(
            "skipped",
            f"{n_flights} flights < threshold {min_flights}",
            n_flights=n_flights,
            n_rows=len(df),
        )

    codes = {a: i for i, a in enumerate(sorted(df["arrival_airport"].unique()))}
    df = prepare(df, codes)
    train_df, test_df = split_by_time(df)
    hyper = {
        "objective": "l1",
        "n_estimators": 400,
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_child_samples": 20,
        "subsample": 0.8,
        "subsample_freq": 1,
        "colsample_bytree": 0.9,
        "random_state": 42,
        "verbose": -1,
        **(params or {}),
    }
    model = lgb.LGBMRegressor(**hyper)
    model.fit(
        train_df[FEATURES], train_df["target_ratio"], categorical_feature=[CATEGORICAL_FEATURE]
    )

    y = test_df["target_seconds"].to_numpy(dtype=float)
    pred = predict_seconds(model, test_df)
    base = test_df["naive_eta_min"].to_numpy(dtype=float) * 60
    metrics = {**_metrics(y, pred, ""), **_metrics(y, base, "baseline_")}
    metrics["mae_improvement_pct"] = 100 * (1 - metrics["mae_min"] / metrics["baseline_mae_min"])
    metrics["test_rows"] = float(len(test_df))

    model_dir = root / "models"
    promoted = should_promote(metrics)
    metrics["promoted"] = float(promoted)
    importance: dict[str, float] = {}
    _log_model = None
    if promoted:
        # Serving model: refit on every flight (the holdout only served the evaluation).
        final = lgb.LGBMRegressor(**hyper)
        final.fit(df[FEATURES], df["target_ratio"], categorical_feature=[CATEGORICAL_FEATURE])
        model_dir.mkdir(parents=True, exist_ok=True)
        tmp = model_dir / "eta_model.txt.tmp"
        final.booster_.save_model(str(tmp))
        os.replace(tmp, model_dir / "eta_model.txt")
        importance = dict(
            zip(FEATURES, final.booster_.feature_importance("gain").tolist(), strict=True)
        )
        metadata = {
            "features": FEATURES,
            "target": "ratio to naive ETA (remaining_km * 1000 / velocity_ms), clipped",
            "ratio_clip": list(RATIO_CLIP),
            "airport_codes": codes,
            "metrics": metrics,
            "n_flights": n_flights,
            "n_rows": len(df),
            "trained_at": time.time(),
            "feature_importance_gain": importance,
        }
        (model_dir / "eta_model.json").write_text(json.dumps(metadata, indent=2))

        def _log_model() -> None:
            import mlflow

            mlflow.lightgbm.log_model(
                final.booster_, name="model", registered_model_name=MODEL_NAME
            )
    else:
        for name in ("eta_model.txt", "eta_model.json"):
            (model_dir / name).unlink(missing_ok=True)

    log_run(
        "eta-prediction",
        "lightgbm-vs-naive",
        params={
            **{k: v for k, v in hyper.items() if k != "verbose"},
            "n_flights": n_flights,
            "n_rows": len(df),
            "split": "time, 20% latest flights",
        },
        metrics=metrics,
        log_model=_log_model,
        artifacts={"feature_importance.json": importance},
    )
    trained_at = pd.Timestamp.utcnow().tz_localize(None)
    append_model_metrics(
        [
            {
                "model": "eta",
                "metric": "mae_min",
                "value": metrics["mae_min"],
                "baseline_value": metrics["baseline_mae_min"],
                "n_samples": len(test_df),
                "trained_at": trained_at,
            },
            {
                "model": "eta",
                "metric": "mape",
                "value": metrics["mape"],
                "baseline_value": metrics["baseline_mape"],
                "n_samples": len(test_df),
                "trained_at": trained_at,
            },
        ],
        root,
    )
    return EtaResult(
        "trained",
        reason="" if promoted else "not promoted: does not beat the naive baseline",
        n_flights=n_flights,
        n_rows=len(df),
        metrics=metrics,
        model_path=str(model_dir / "eta_model.txt") if promoted else None,
        promoted=promoted,
    )


def result_dict(result: EtaResult) -> dict[str, Any]:
    return asdict(result)
