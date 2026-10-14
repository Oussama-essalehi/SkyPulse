"""Unsupervised trajectory anomalies: Isolation Forest on per-flight features.

Complements the streaming rules (squawk, descent, holding, signal loss): it flags flights
whose *combination* of features is rare (e.g. very tortuous path for its length, unusual
altitude variability, many coverage gaps) without a hand-written rule. Each outlier gets
a readable explanation: the features furthest from the median (robust z-score).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from skyradar_ml.marts import append_model_metrics, marts_root, read_mart, write_parquet_atomic
from skyradar_ml.tracking import log_run

FEATURES = [
    "n_positions",
    "duration_min",
    "path_km",
    "straightness",
    "max_altitude_m",
    "altitude_std_m",
    "max_abs_vertical_rate_ms",
    "mean_speed_ms",
    "speed_std_ms",
    "turn_rate_deg_per_min",
    "n_gaps",
]
LABELS = {
    "n_positions": "nombre de positions",
    "duration_min": "durée",
    "path_km": "distance parcourue",
    "straightness": "rectitude de la trajectoire",
    "max_altitude_m": "altitude maximale",
    "altitude_std_m": "variabilité d'altitude",
    "max_abs_vertical_rate_ms": "vitesse verticale maximale",
    "mean_speed_ms": "vitesse moyenne",
    "speed_std_ms": "variabilité de vitesse",
    "turn_rate_deg_per_min": "taux de virage",
    "n_gaps": "trous de couverture",
}


@dataclass
class OutlierResult:
    status: str
    n_flights: int = 0
    n_outliers: int = 0
    reason: str = ""
    scores: pd.DataFrame | None = field(default=None, repr=False)


def explain(row: pd.Series, median: pd.Series, iqr: pd.Series, top: int = 2) -> str:
    z = ((row[FEATURES] - median) / iqr.replace(0, np.nan)).astype(float).fillna(0.0)
    parts = []
    for name in z.abs().sort_values(ascending=False).index[:top]:
        direction = "élevé(e)" if z[name] > 0 else "faible"
        parts.append(f"{LABELS[name]} {direction} ({row[name]:.3g})")
    return ", ".join(parts)


def score(
    root: Path | None = None, contamination: float = 0.01, min_flights: int = 50
) -> OutlierResult:
    from sklearn.ensemble import IsolationForest
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import RobustScaler

    root = root or marts_root()
    flights = read_mart("ml_flight_features", root)
    if flights is None or len(flights) < min_flights:
        n = 0 if flights is None else len(flights)
        return OutlierResult("skipped", n_flights=n, reason=f"{n} flights < {min_flights}")

    x = flights[FEATURES].astype(float).fillna(0.0)
    model = make_pipeline(
        RobustScaler(),
        IsolationForest(n_estimators=200, contamination=contamination, random_state=42),
    )
    model.fit(x)
    # score_samples: the lower, the more anomalous -> negate so that high = anomalous.
    flights = flights.assign(
        anomaly_score=-model.score_samples(x),
        is_outlier=model.predict(x) == -1,
    )
    median, iqr = x.median(), x.quantile(0.75) - x.quantile(0.25)
    flights["explanation"] = [
        explain(row, median, iqr) if outlier else None
        for (_, row), outlier in zip(flights.iterrows(), flights["is_outlier"], strict=True)
    ]
    keep = [
        "flight_id",
        "icao24",
        "callsign",
        "first_seen_ts",
        "last_seen_ts",
        "last_latitude",
        "last_longitude",
        "anomaly_score",
        "is_outlier",
        "explanation",
        *FEATURES,
    ]
    scores = flights[keep].sort_values("anomaly_score", ascending=False)
    write_parquet_atomic(scores, root / "ml_flight_anomaly_scores.parquet")

    n_out = int(scores["is_outlier"].sum())
    metrics = {
        "n_flights": float(len(scores)),
        "n_outliers": float(n_out),
        "score_p50": float(scores["anomaly_score"].median()),
        "score_p99": float(scores["anomaly_score"].quantile(0.99)),
    }

    def _log_model() -> None:
        import mlflow

        mlflow.sklearn.log_model(
            model, name="model", registered_model_name="skyradar-trajectory-iforest"
        )

    log_run(
        "trajectory-anomalies",
        "isolation-forest",
        params={
            "contamination": contamination,
            "n_estimators": 200,
            "features": ",".join(FEATURES),
        },
        metrics=metrics,
        log_model=_log_model,
    )
    append_model_metrics(
        [
            {
                "model": "trajectory_iforest",
                "metric": "outlier_share",
                "value": n_out / len(scores),
                "baseline_value": contamination,
                "n_samples": len(scores),
                "trained_at": pd.Timestamp.utcnow().tz_localize(None),
            }
        ],
        root,
    )
    return OutlierResult("scored", n_flights=len(scores), n_outliers=n_out, scores=scores)
