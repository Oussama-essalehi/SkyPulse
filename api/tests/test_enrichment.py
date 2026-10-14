import json
import time
from pathlib import Path

import duckdb
import lightgbm as lgb
import numpy as np
import pytest

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
    assert flight["departure"]["icao"] == "LFPG"
    assert flight["departure_source"] == "reconstructed"
    assert flight["arrival"]["icao"] == "LFLL"
    assert flight["arrival"]["name"] == "Lyon"
    assert flight["arrival_source"] == "history"
    assert not flight["departure_time_estimated"]
    assert 1150 <= flight["elapsed_s"] <= 1250  # took off 20 min ago
    assert 0 < flight["progress"] < 1
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
    on_ground = {"arrival": {"icao": "LFLL", "latitude": 45.7, "longitude": 5.1}}
    assert Enrichment(root=tmp_path).eta(state(on_ground=True), on_ground) is None


def registry(root: Path) -> None:
    write(
        root,
        "dim_aircraft_registry",
        "select * from (values "
        "('abc123', 'F-HBNA', 'A320', 'Airbus', 'A320-214', 'A320', 'M', 'Jet', 2, "
        "'Air France', 'AFR', 2012), "
        "('def456', 'N12345', 'B738', 'Boeing', '737-824', 'B737', 'M', 'Jet', 2, "
        "null, 'UAL', 2005)) t(icao24, registration, typecode, manufacturer, model, "
        "type_name, wake_category, engine_type, engine_count, operator, operator_icao, "
        "built_year)",
    )
    write(
        root,
        "dim_airlines",
        "select * from (values ('AFR', 'Air France'), ('UAL', 'United Airlines'), "
        "('DLH', 'Lufthansa')) t(airline_icao, airline_name)",
    )


def test_aircraft_type_and_airline_from_callsign(tmp_path: Path) -> None:
    marts(tmp_path)
    registry(tmp_path)
    enrichment = Enrichment(root=tmp_path)
    info = enrichment.aircraft(state(callsign="DLH42"))
    assert info is not None
    assert info["registration"] == "F-HBNA"
    assert info["typecode"] == "A320"
    assert info["in_registry"]
    # The callsign wins over the registered operator (wet lease, codeshare...).
    assert (info["airline_icao"], info["airline_name"]) == ("DLH", "Lufthansa")


def test_aircraft_falls_back_to_operator_and_handles_unknowns(tmp_path: Path) -> None:
    marts(tmp_path)
    registry(tmp_path)
    enrichment = Enrichment(root=tmp_path)
    # Private callsign (registration): airline = registered operator.
    info = enrichment.aircraft(state(icao24="def456", callsign="N12345"))
    assert info is not None
    assert (info["airline_icao"], info["airline_name"]) == ("UAL", "United Airlines")
    # Unknown aircraft with an airline callsign: airline only.
    info = enrichment.aircraft(state(icao24="ffffff", callsign="AFR7"))
    assert info is not None
    assert not info["in_registry"]
    assert info["airline_name"] == "Air France"
    assert "typecode" not in info
    # Unknown aircraft, no airline: nothing to show.
    assert enrichment.aircraft(state(icao24="eeeeee", callsign="FHBXY")) is None


def test_aircraft_without_reference_marts(tmp_path: Path) -> None:
    assert Enrichment(root=tmp_path).aircraft(state(callsign="AFR7")) == {
        "airline_icao": "AFR",
        "airline_name": None,
        "in_registry": False,
    }


def test_wind_cell_mirrors_the_dbt_macros() -> None:
    from skyradar_api.enrichment import wind_cell

    points = [(48.6, 2.3), (45.0, 5.0), (-45.0, 175.0), (75.0, -179.9), (35.0, -15.0), (0.0, 0.0)]
    for lat, lon in points:
        expected = duckdb.sql(
            f"select cast(least(70, greatest(-40, round({lat} / 10) * 10)) as smallint), "
            f"cast(case when round({lon} / 10) * 10 >= 180 then -180 "
            f"else round({lon} / 10) * 10 end as smallint)"
        ).fetchone()
        assert wind_cell(lat, lon) == expected, (lat, lon)


def test_tailwind_from_the_wind_mart(tmp_path: Path) -> None:
    from skyradar_api.enrichment import tailwind_ms

    now = int(time.time())
    hour = now // 3600 * 3600
    write(
        tmp_path,
        "fct_upper_winds",
        f"select 50::smallint grid_lat, 0::smallint grid_lon, "
        f"make_timestamp({hour}::bigint * 1000000) as hour, "
        "40.0 wind_speed_ms, 270.0 wind_direction_deg",
    )
    enrichment = Enrichment(root=tmp_path)
    enrichment.refresh(force=True)
    east = state(latitude=48.8, longitude=2.3, true_track=90.0)
    west = state(latitude=48.8, longitude=2.3, true_track=270.0)
    assert enrichment._tailwind(east, now) == pytest.approx(40.0)  # westerly jet, eastbound
    assert enrichment._tailwind(west, now) == pytest.approx(-40.0)
    assert np.isnan(enrichment._tailwind(state(latitude=10.0, longitude=100.0), now))
    assert tailwind_ms(10, 0, 90) == pytest.approx(0, abs=1e-9)  # pure crosswind


def published(origin: tuple[str, float, float], destination: tuple[str, float, float]):  # type: ignore[no-untyped-def]
    from skyradar_api.routes import FlightRoute, RouteAirport

    def airport(code: str, lat: float, lon: float) -> RouteAirport:
        return RouteAirport(code, code[1:], f"{code} airport", "City", "Country", lat, lon)

    return FlightRoute("SKR100", airport(*origin), airport(*destination), "SkyRadar Air")


def test_published_route_gives_destination_and_estimated_departure(tmp_path: Path) -> None:
    """No history at all (first hours, demo profile): the route database is enough."""
    enrichment = Enrichment(root=tmp_path / "missing")
    route = published(("LFPG", 49.0097, 2.5479), ("LIRF", 41.8003, 12.2389))
    # Over Burgundy, heading south-east towards Rome, cruising at 220 m/s.
    flight = enrichment.flight(state(latitude=47.0, longitude=4.0, true_track=140.0), route)
    assert flight is not None
    assert (flight["departure"]["icao"], flight["arrival"]["icao"]) == ("LFPG", "LIRF")
    assert flight["departure_source"] == flight["arrival_source"] == "route_db"
    assert flight["departure"]["city"] == "City"
    assert flight["departure_time_estimated"]
    # ~260 km flown at 0.8 x 220 m/s: departed ~25 min ago.
    assert 20 * 60 < flight["elapsed_s"] < 30 * 60
    assert flight["route_km"] > flight["remaining_km"] > 0
    eta = enrichment.eta(state(latitude=47.0, longitude=4.0), flight)
    assert eta is not None
    assert eta["airport"] == "LIRF"


def test_inconsistent_published_route_is_rejected(tmp_path: Path) -> None:
    marts(tmp_path)
    enrichment = Enrichment(root=tmp_path)
    # Wrong direction: the aircraft flies away from the published destination.
    route = published(("LFPG", 49.0097, 2.5479), ("LIRF", 41.8003, 12.2389))
    flight = enrichment.flight(state(true_track=320.0), route)
    assert flight is not None
    assert flight["rejected_route"] == "LFPG → LIRF"
    assert flight["arrival"]["icao"] == "LFLL"  # back to our own history
    # Published origin differs from the observed take-off: rejected as well.
    route = published(("EGLL", 51.47, -0.4619), ("LIRF", 41.8003, 12.2389))
    flight = enrichment.flight(state(true_track=140.0), route)
    assert flight is not None
    assert flight["rejected_route"] == "EGLL → LIRF"
    assert flight["departure"]["icao"] == "LFPG"


def test_rejected_route_without_history_is_still_reported(tmp_path: Path) -> None:
    enrichment = Enrichment(root=tmp_path / "missing")
    route = published(("LFPG", 49.0097, 2.5479), ("LIRF", 41.8003, 12.2389))
    flight = enrichment.flight(state(latitude=-15.0, longitude=-47.0), route)  # over Brazil
    assert flight is not None
    assert flight["departure"] is None
    assert flight["arrival"] is None
    assert flight["rejected_route"] == "LFPG → LIRF"
    assert enrichment.eta(state(), flight) is None
