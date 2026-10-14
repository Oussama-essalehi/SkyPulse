"""DBSCAN clustering of take-offs and landings that match no known airport.

``int_flight_endpoints`` attaches a flight end to the nearest large / medium (or
scheduled) airport within 15 km. Low endpoints left without an airport are clustered
here (DBSCAN, haversine metric): a dense cluster is a place where aircraft really take
off and land - an airfield, a heliport, a military base - while isolated points are
mostly coverage artefacts (an aircraft whose signal is lost at low altitude).

Each cluster is labelled with the nearest entry of the *full* OurAirports file (every
type: small airfields, heliports, seaplane bases), which says whether the airport filter
or the data is the cause. Output: ``ml_endpoint_clusters`` (one row per cluster).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from skyradar_ml.marts import marts_root, read_mart, write_parquet_atomic
from skyradar_ml.tracking import log_run

EARTH_RADIUS_KM = 6371.0
CLUSTER_COLUMNS = [
    "cluster_id", "latitude", "longitude", "n_endpoints", "n_aircraft", "n_departures",
    "n_arrivals", "radius_km", "first_seen_ts", "last_seen_ts", "example_callsigns",
    "nearest_ident", "nearest_type", "nearest_name", "nearest_km",
]  # fmt: skip


@dataclass
class ClusterResult:
    status: str
    n_endpoints: int = 0
    n_clusters: int = 0
    n_noise: int = 0
    reason: str = ""
    clusters: pd.DataFrame | None = field(default=None, repr=False)


def reference_airports(path: Path | None = None) -> pd.DataFrame | None:
    """Every OurAirports entry (all types), or None when the file is not available."""
    path = path or Path(os.getenv("SKYRADAR_REFERENCE_ROOT", "/data/reference")) / "airports.csv"
    if not path.exists():
        return None
    import duckdb

    frame = (
        duckdb.connect()
        .execute(
            "select ident, type, name, cast(latitude_deg as double) as latitude, "
            "cast(longitude_deg as double) as longitude "
            f"from read_csv('{path}', header = true, all_varchar = true) "
            "where latitude_deg is not null and longitude_deg is not null "
            "and type <> 'closed'"
        )
        .df()
    )
    return pd.DataFrame(frame)


def cluster(
    endpoints: pd.DataFrame,
    eps_km: float = 3.0,
    min_samples: int = 3,
    airports: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, np.ndarray]:
    """Cluster endpoints; returns (one row per cluster, label per endpoint; -1 = noise)."""
    from sklearn.cluster import DBSCAN

    coords = np.radians(endpoints[["latitude", "longitude"]].to_numpy(dtype=float))
    labels = DBSCAN(
        eps=eps_km / EARTH_RADIUS_KM,
        min_samples=min_samples,
        metric="haversine",
        algorithm="ball_tree",
    ).fit_predict(coords)
    data = endpoints.assign(cluster_id=labels)
    data = data[data["cluster_id"] >= 0]
    if data.empty:
        return pd.DataFrame(columns=CLUSTER_COLUMNS), labels

    rows = []
    for cluster_id, group in data.groupby("cluster_id"):
        lat, lon = float(group["latitude"].mean()), float(group["longitude"].mean())
        distances = _haversine_km(lat, lon, group["latitude"], group["longitude"])
        callsigns = group["callsign"].dropna().value_counts().index[:3]
        rows.append(
            {
                "cluster_id": int(str(cluster_id)),
                "latitude": round(lat, 5),
                "longitude": round(lon, 5),
                "n_endpoints": len(group),
                "n_aircraft": int(group["icao24"].nunique()),
                "n_departures": int((group["endpoint"] == "departure").sum()),
                "n_arrivals": int((group["endpoint"] == "arrival").sum()),
                "radius_km": round(float(np.max(distances)), 2),
                "first_seen_ts": group["endpoint_ts"].min(),
                "last_seen_ts": group["endpoint_ts"].max(),
                "example_callsigns": ", ".join(callsigns),
            }
        )
    clusters = pd.DataFrame(rows)
    clusters = _label(clusters, airports)[CLUSTER_COLUMNS]
    return clusters.sort_values("n_endpoints", ascending=False), labels


def _haversine_km(lat: float, lon: float, lats: pd.Series, lons: pd.Series) -> np.ndarray:
    p1, p2 = np.radians(lat), np.radians(lats.to_numpy(dtype=float))
    dp, dl = p2 - p1, np.radians(lons.to_numpy(dtype=float) - lon)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return np.asarray(2 * EARTH_RADIUS_KM * np.arcsin(np.minimum(1.0, np.sqrt(a))))


def _label(clusters: pd.DataFrame, airports: pd.DataFrame | None) -> pd.DataFrame:
    """Nearest OurAirports entry of any type, within 10 km."""
    empty = {"nearest_ident": None, "nearest_type": None, "nearest_name": None}
    if airports is None or airports.empty:
        return clusters.assign(**empty, nearest_km=np.nan)
    from sklearn.neighbors import BallTree

    tree = BallTree(np.radians(airports[["latitude", "longitude"]].to_numpy()), metric="haversine")
    dist, idx = tree.query(np.radians(clusters[["latitude", "longitude"]].to_numpy()), k=1)
    km = dist[:, 0] * EARTH_RADIUS_KM
    nearest = airports.iloc[idx[:, 0]].reset_index(drop=True)
    close = km <= 10
    return clusters.assign(
        nearest_ident=np.where(close, nearest["ident"], None),
        nearest_type=np.where(close, nearest["type"], None),
        nearest_name=np.where(close, nearest["name"], None),
        nearest_km=np.where(close, np.round(km, 2), np.nan),
    )


def run(
    root: Path | None = None,
    eps_km: float | None = None,
    min_samples: int | None = None,
    min_endpoints: int = 20,
    airports: pd.DataFrame | None = None,
) -> ClusterResult:
    root = root or marts_root()
    eps_km = eps_km or float(os.getenv("ML_DBSCAN_EPS_KM", "3"))
    min_samples = min_samples or int(os.getenv("ML_DBSCAN_MIN_SAMPLES", "3"))
    endpoints = read_mart("ml_unmatched_endpoints", root)
    n = 0 if endpoints is None else len(endpoints)
    if endpoints is None or n < min_endpoints:
        return ClusterResult("skipped", n_endpoints=n, reason=f"{n} endpoints < {min_endpoints}")

    clusters, labels = cluster(
        endpoints,
        eps_km=eps_km,
        min_samples=min_samples,
        airports=airports if airports is not None else reference_airports(),
    )
    write_parquet_atomic(clusters, root / "ml_endpoint_clusters.parquet")
    n_noise = int((labels < 0).sum())
    labelled = 0 if clusters.empty else int(clusters["nearest_ident"].notna().sum())
    metrics = {
        "n_endpoints": float(n),
        "n_clusters": float(len(clusters)),
        "clustered_share": float(1 - n_noise / n),
        "clusters_near_known_airfield": float(labelled),
    }
    log_run(
        "endpoint-clusters",
        "dbscan",
        params={"eps_km": eps_km, "min_samples": min_samples, "metric": "haversine"},
        metrics=metrics,
    )
    return ClusterResult(
        "clustered",
        n_endpoints=n,
        n_clusters=len(clusters),
        n_noise=n_noise,
        clusters=clusters,
    )
