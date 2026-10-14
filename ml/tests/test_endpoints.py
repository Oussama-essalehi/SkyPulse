from pathlib import Path

import numpy as np
import pandas as pd

from skyradar_ml import endpoints
from skyradar_ml.marts import read_mart, write_parquet_atomic


def make_endpoints() -> pd.DataFrame:
    rng = np.random.default_rng(1)
    rows = []

    def add(lat: float, lon: float, n: int, spread: float, prefix: str) -> None:
        for i in range(n):
            rows.append(
                {
                    "flight_id": f"{prefix}{i}",
                    "icao24": f"{prefix}{i % 3}",
                    "callsign": f"{prefix.upper()}{i % 2}",
                    "endpoint": "departure" if i % 2 else "arrival",
                    "latitude": lat + rng.normal(0, spread),
                    "longitude": lon + rng.normal(0, spread),
                    "endpoint_ts": pd.Timestamp("2026-10-01") + pd.Timedelta(hours=i),
                }
            )

    add(48.60, 2.00, 12, 0.003, "heli")  # a heliport (~300 m spread)
    add(45.00, 5.00, 6, 0.003, "farm")  # an unlisted private strip
    for i, (lat, lon) in enumerate([(40, -30), (10, 60), (-20, 120), (60, 20), (0, 0)]):
        add(lat, lon, 1, 0.0, f"noise{i}_")  # isolated: lost signal at low altitude
    return pd.DataFrame(rows)


AIRPORTS = pd.DataFrame(
    [
        {"ident": "LF0001", "type": "heliport", "name": "Test heliport",
         "latitude": 48.603, "longitude": 2.002},
        {"ident": "LFPG", "type": "large_airport", "name": "Paris CDG",
         "latitude": 49.0097, "longitude": 2.5479},
    ]
)  # fmt: skip


def test_dbscan_finds_airfields_and_labels_them() -> None:
    clusters, labels = endpoints.cluster(make_endpoints(), eps_km=2, airports=AIRPORTS)
    assert len(clusters) == 2
    assert int((labels < 0).sum()) == 5
    heli, farm = clusters.iloc[0], clusters.iloc[1]
    assert heli["n_endpoints"] == 12
    assert heli["n_departures"] + heli["n_arrivals"] == 12
    assert heli["nearest_ident"] == "LF0001"
    assert heli["nearest_type"] == "heliport"
    assert heli["nearest_km"] < 1
    # Nothing known within 10 km: a genuinely unlisted place.
    assert pd.isna(farm["nearest_ident"])
    assert list(clusters.columns) == endpoints.CLUSTER_COLUMNS


def test_run_writes_mart_and_skips_small_inputs(tmp_path: Path) -> None:
    assert endpoints.run(tmp_path).status == "skipped"
    write_parquet_atomic(make_endpoints(), tmp_path / "ml_unmatched_endpoints.parquet")
    result = endpoints.run(tmp_path, eps_km=2, airports=AIRPORTS)
    assert result.status == "clustered"
    assert (result.n_clusters, result.n_noise) == (2, 5)
    mart = read_mart("ml_endpoint_clusters", tmp_path)
    assert mart is not None
    assert len(mart) == 2


def test_only_noise_gives_an_empty_typed_mart(tmp_path: Path) -> None:
    data = make_endpoints()
    data = data[data["flight_id"].str.startswith("noise")]
    clusters, labels = endpoints.cluster(data, eps_km=2, airports=AIRPORTS)
    assert clusters.empty
    assert list(clusters.columns) == endpoints.CLUSTER_COLUMNS
    assert (labels < 0).all()
