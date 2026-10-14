import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from skyradar_ml import eta, forecast, outliers
from skyradar_ml.marts import read_mart, write_parquet_atomic


def make_eta_features(n_flights: int = 80, seed: int = 0) -> pd.DataFrame:
    """Flights that slow down on approach: the naive ETA is systematically optimistic."""
    rng = np.random.default_rng(seed)
    rows = []
    for f in range(n_flights):
        airport = ["LFPG", "EGLL", "EDDF"][f % 3]
        arrival = pd.Timestamp("2026-01-01") + pd.Timedelta(hours=f)
        cruise = rng.uniform(220, 250)
        for remaining in range(20, 600, 20):
            # 40 km at 120 m/s for the approach, the rest at cruise speed.
            approach = min(remaining, 40)
            seconds = approach * 1000 / 120 + (remaining - approach) * 1000 / cruise
            rows.append(
                {
                    "flight_id": f"f{f}",
                    "arrival_airport": airport,
                    "arrival_time": arrival,
                    "target_seconds": seconds,
                    "remaining_km": float(remaining),
                    "velocity_ms": 120.0 if remaining <= 40 else cruise,
                    "altitude_m": 2000.0 if remaining <= 40 else 11000.0,
                    "vertical_rate_ms": -4.0 if remaining <= 80 else 0.0,
                    "heading_alignment": 1.0,
                    "hour_utc": 12,
                    "day_of_week": 3,
                    "elapsed_min": 30.0,
                }
            )
    return pd.DataFrame(rows)


def test_eta_skips_without_enough_history(tmp_path: Path) -> None:
    assert eta.train(tmp_path).status == "skipped"
    write_parquet_atomic(make_eta_features(10), tmp_path / "ml_eta_features.parquet")
    result = eta.train(tmp_path, min_flights=50)
    assert result.status == "skipped"
    assert "threshold" in result.reason


def test_eta_model_beats_naive_baseline_and_exports(tmp_path: Path) -> None:
    write_parquet_atomic(make_eta_features(), tmp_path / "ml_eta_features.parquet")
    result = eta.train(tmp_path, min_flights=50, params={"n_estimators": 150})
    assert result.status == "trained"
    assert result.metrics["mae_min"] < result.metrics["baseline_mae_min"]
    assert result.promoted
    meta = json.loads((tmp_path / "models" / "eta_model.json").read_text())
    assert meta["features"] == eta.FEATURES
    assert set(meta["airport_codes"]) == {"LFPG", "EGLL", "EDDF"}
    history = read_mart("ml_model_metrics", tmp_path)
    assert history is not None
    assert set(history["metric"]) == {"mae_min", "mape"}


def test_split_by_time_keeps_flights_whole() -> None:
    df = make_eta_features(20)
    train, test = eta.split_by_time(df)
    assert not set(train["flight_id"]) & set(test["flight_id"])
    assert train["arrival_time"].max() < test["arrival_time"].min()


def test_eta_model_not_promoted_when_worse_than_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_parquet_atomic(make_eta_features(), tmp_path / "ml_eta_features.parquet")
    assert eta.train(tmp_path, min_flights=50, params={"n_estimators": 50}).promoted
    assert (tmp_path / "models" / "eta_model.txt").exists()
    # The next training does not beat the baseline: the served model is withdrawn.
    monkeypatch.setattr(eta, "should_promote", lambda metrics: False)
    result = eta.train(tmp_path, min_flights=50, params={"n_estimators": 50})
    assert result.status == "trained"
    assert not result.promoted
    assert "baseline" in result.reason
    assert not (tmp_path / "models" / "eta_model.txt").exists()
    assert not (tmp_path / "models" / "eta_model.json").exists()


def test_forecast_beats_naive_on_seasonal_series(tmp_path: Path) -> None:
    hours = pd.date_range("2026-01-01", periods=24 * 5, freq="h")
    base = 1000 + 400 * np.sin(2 * np.pi * hours.hour / 24)
    traffic = pd.DataFrame(
        {
            "hour": np.tile(hours, 2),
            "zone": ["Europe"] * len(hours) + ["Asia"] * len(hours),
            "airborne_aircraft": np.concatenate([base, base / 2]).round(),
        }
    )
    write_parquet_atomic(traffic, tmp_path / "fct_traffic_hourly.parquet")
    result = forecast.run(tmp_path, horizon=6, holdout=6)
    assert result.status == "trained"
    assert result.zones == 2
    assert result.metrics["mae_aircraft"] < result.metrics["baseline_mae_aircraft"] + 1e-6
    out = read_mart("ml_traffic_forecast", tmp_path)
    assert out is not None
    assert (out["kind"] == "forecast").sum() == 12


def test_forecast_skips_short_history(tmp_path: Path) -> None:
    traffic = pd.DataFrame(
        {
            "hour": pd.date_range("2026-01-01", periods=5, freq="h"),
            "zone": "Europe",
            "airborne_aircraft": 10,
        }
    )
    write_parquet_atomic(traffic, tmp_path / "fct_traffic_hourly.parquet")
    assert forecast.run(tmp_path).status == "skipped"


def test_isolation_forest_flags_circling_flight(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    n = 300
    flights = pd.DataFrame(
        {
            "flight_id": [f"f{i}" for i in range(n)],
            "icao24": [f"{i:06x}" for i in range(n)],
            "callsign": [f"TST{i}" for i in range(n)],
            "first_seen_ts": pd.Timestamp("2026-01-01"),
            "last_seen_ts": pd.Timestamp("2026-01-01"),
            "last_latitude": 48.0,
            "last_longitude": 2.0,
            "n_positions": rng.integers(20, 80, n),
            "duration_min": rng.uniform(60, 180, n),
            "path_km": rng.uniform(600, 2000, n),
            "straightness": rng.uniform(0.9, 1.0, n),
            "max_altitude_m": rng.uniform(9000, 12000, n),
            "altitude_std_m": rng.uniform(2000, 4000, n),
            "max_abs_vertical_rate_ms": rng.uniform(8, 15, n),
            "mean_speed_ms": rng.uniform(200, 240, n),
            "speed_std_ms": rng.uniform(10, 30, n),
            "turn_rate_deg_per_min": rng.uniform(0, 3, n),
            "n_gaps": rng.integers(0, 2, n),
        }
    )
    flights.loc[0, ["straightness", "turn_rate_deg_per_min", "path_km"]] = [0.02, 180.0, 400.0]
    write_parquet_atomic(flights, tmp_path / "ml_flight_features.parquet")
    result = outliers.score(tmp_path, contamination=0.01)
    assert result.status == "scored"
    assert result.scores is not None
    top = result.scores.iloc[0]
    assert top["flight_id"] == "f0"
    assert top["is_outlier"]
    assert "taux de virage" in top["explanation"] or "rectitude" in top["explanation"]


def test_eta_wind_feature_is_measured_by_ablation(tmp_path: Path) -> None:
    """Wind ahead differs from the wind already in the ground speed: the model needs it."""
    features = make_eta_features()
    rng = np.random.default_rng(3)
    per_flight = {f: rng.uniform(-40, 40) for f in features["flight_id"].unique()}
    features["tailwind_ms"] = features["flight_id"].map(per_flight)
    features["target_seconds"] *= 1 - features["tailwind_ms"] / 400
    write_parquet_atomic(features, tmp_path / "ml_eta_features.parquet")
    result = eta.train(tmp_path, min_flights=50, params={"n_estimators": 150})
    assert result.metrics["wind_coverage"] == 1.0
    assert result.metrics["mae_min"] < result.metrics["mae_min_without_wind"]


def test_eta_without_weather_data(tmp_path: Path) -> None:
    write_parquet_atomic(make_eta_features(), tmp_path / "ml_eta_features.parquet")
    result = eta.train(tmp_path, min_flights=50, params={"n_estimators": 50})
    assert result.status == "trained"
    assert result.metrics["wind_coverage"] == 0.0
    assert "mae_min_without_wind" not in result.metrics
