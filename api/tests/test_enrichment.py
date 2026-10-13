import json
import time
from pathlib import Path

import duckdb
import lightgbm as lgb
import numpy as np

from skyradar_api.enrichment import Enrichment, haversine_km
from skyradar_common.models import StateVector

FEATURES = [
    "naive_eta_min",
    "remaining_km",
    "velocity_ms",
    "altitude_m",
    "vertical_rate_ms",
    "heading_alignment",
    "hour_utc",
    "day_of_week",
    "elapsed_min",
    "arrival_airport_code",
]


def write(root: Path, name: str, sql: str) -> None:
    duckdb.sql(f"copy ({sql}) to '{root / name}.parquet' (format parquet)")


def marts(root: Path) -> None:
    write(
        root,
        "dim_airports",
        "select * from (values ('LFPG', 'Paris CDG', 49.0097, 2.5479),"
        " ('LFLL', 'Lyon', 45.7256, 5.0811)) t(airport_icao, airport_name, latitude, longitude)",
    )
    write(
        root,
        "fct_callsign_routes",
        "select 'SKR100' callsign, 'LFPG' departure_airport, "
        "'LFLL' usual_arrival_airport, 5 route_flights, 0.8 usual_arrival_share",
    )
    write(
        root,
        "fct_flights",
        "select 'abc123' icao24, 'LFPG' departure_airport, "
        "now()::timestamp - interval 20 minute as departure_time, "
        "true has_departure, false has_arrival",
    )


def state(**kw: object) -> StateVector:
    now = int(time.time())
    data: dict[str, object] = {
        "icao24": "abc123",
        "callsign": "SKR100",
        "latitude": 47.0,
        "longitude": 4.0,
        "baro_altitude": 9000.0,
        "velocity": 220.0,
        "true_track": 150.0,
        "vertical_rate": 0.0,
        "time_position": now,
        "last_contact": now,
        "snapshot_time": now,
        "ingested_at": now,
    }
    data.update(kw)
    return StateVector.model_validate(data)


def test_route_and_naive_eta(tmp_path: Path) -> None:
    marts(tmp_path)
    enrichment = Enrichment(root=tmp_path)
    flight = enrichment.flight(state())
    assert flight is not None
    assert flight["departure"] == "LFPG"
    assert flight["arrival"] == "LFLL"
    assert flight["arrival_name"] == "Lyon"
    eta = enrichment.eta(state(), flight)
    assert eta is not None
    assert eta["method"] == "naive"
    expected = haversine_km(47.0, 4.0, 45.7256, 5.0811) * 1000 / 220
    assert abs(eta["seconds"] - expected) < 2


def test_eta_uses_exported_model(tmp_path: Path) -> None:
    marts(tmp_path)
    # A model that learned "the real ETA is 20 % longer than the naive one".
    rng = np.random.default_rng(0)
    x = rng.uniform(0, 1, (400, len(FEATURES)))
    booster = lgb.train(
        {"objective": "l1", "verbose": -1},
        lgb.Dataset(x, label=np.full(400, 1.2)),
        num_boost_round=5,
    )
    (tmp_path / "models").mkdir()
    booster.save_model(str(tmp_path / "models" / "eta_model.txt"))
    (tmp_path / "models" / "eta_model.json").write_text(
        json.dumps(
            {
                "features": FEATURES,
                "airport_codes": {"LFLL": 0},
                "ratio_clip": [0.2, 5.0],
                "metrics": {"mae_min": 3.2},
                "trained_at": 0,
            }
        )
    )
    enrichment = Enrichment(root=tmp_path)
    eta = enrichment.eta(state(), enrichment.flight(state()))
    assert eta is not None
    assert eta["method"] == "lightgbm"
    assert abs(eta["seconds"] / eta["naive_seconds"] - 1.2) < 0.01
    assert eta["model_mae_min"] == 3.2


def test_no_marts_no_enrichment(tmp_path: Path) -> None:
    enrichment = Enrichment(root=tmp_path / "missing")
    assert enrichment.flight(state()) is None
    assert enrichment.eta(state(), None) is None
    marts(tmp_path)
    assert Enrichment(root=tmp_path).eta(state(on_ground=True), {"arrival": "LFLL"}) is None
