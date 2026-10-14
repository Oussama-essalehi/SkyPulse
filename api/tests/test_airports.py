import time
from pathlib import Path

import duckdb
import httpx
import pytest
from fastapi.testclient import TestClient

from skyradar_api.airports import AirportBoard, terminal_phase
from skyradar_api.enrichment import Airport, Enrichment, haversine_km
from skyradar_api.routes import RouteLookup
from skyradar_api.state import LiveState
from skyradar_common.models import SnapshotMeta, StateVector

LFPG = (49.0097, 2.5479)


def write(root: Path, name: str, sql: str) -> None:
    duckdb.sql(f"copy ({sql}) to '{root / name}.parquet' (format parquet)")


def marts(root: Path) -> None:
    write(
        root,
        "dim_airports",
        "select * from (values"
        " ('LFPG', 'CDG', 'Paris CDG', 'large_airport', 'Paris', 'FR', 49.0097, 2.5479),"
        " ('LFLL', 'LYS', 'Lyon Saint-Exupéry', 'large_airport', 'Lyon', 'FR', 45.7256, 5.0811),"
        " ('EGLL', 'LHR', 'London Heathrow', 'large_airport', 'London', 'GB', 51.4706, -0.4619)"
        ") t(airport_icao, airport_iata, airport_name, airport_type, municipality, iso_country,"
        " latitude, longitude)",
    )
    write(
        root,
        "fct_callsign_routes",
        "select 'SKR100' callsign, 'LFPG' departure_airport, "
        "'LFLL' usual_arrival_airport, 5 route_flights, 0.8 usual_arrival_share",
    )
    columns = (
        "icao24, callsign, airline_name, aircraft_typecode, departure_airport,"
        " departure_airport_name, arrival_airport, arrival_airport_name, departure_time,"
        " arrival_time, duration_min, has_departure, has_arrival"
    )
    now = "now()::timestamp"
    write(
        root,
        "fct_flights",
        f"select * from (values"
        f" ('abc123', 'SKR100', 'SkyRadar Air', 'A320', 'LFPG', 'Paris CDG', null, null,"
        f"  {now} - interval 20 minute, null, null, true, false),"
        f" ('def456', 'SKR101', 'SkyRadar Air', 'A320', 'LFPG', 'Paris CDG', 'LFLL', 'Lyon',"
        f"  {now} - interval 3 hour, {now} - interval 2 hour, 60.0, true, true),"
        f" ('ghi789', 'BAW300', 'British Airways', 'A319', 'EGLL', 'Heathrow', 'LFPG', 'Paris CDG',"
        f"  {now} - interval 2 hour, {now} - interval 1 hour, 55.0, true, true),"
        f" ('jkl012', 'SKR102', 'SkyRadar Air', 'A320', 'LFPG', 'Paris CDG', 'LFLL', 'Lyon',"
        f"  {now} - interval 3 day, {now} - interval 70 hour, 60.0, true, true)"
        f") t({columns})",
    )


def sv(icao24: str, callsign: str | None, lat: float, lon: float, **kw: object) -> StateVector:
    now = int(time.time())
    data: dict[str, object] = {
        "icao24": icao24,
        "callsign": callsign,
        "latitude": lat,
        "longitude": lon,
        "baro_altitude": 9000.0,
        "velocity": 220.0,
        "true_track": 0.0,
        "vertical_rate": 0.0,
        "on_ground": False,
        "time_position": now,
        "last_contact": now,
        "snapshot_time": now,
        "ingested_at": now,
    }
    data.update(kw)
    return StateVector.model_validate(data)


ADSBDB_AFR200 = {
    "response": {
        "flightroute": {
            "callsign": "AFR200",
            "airline": {"name": "Air France"},
            "origin": {
                "icao_code": "EGLL", "iata_code": "LHR", "name": "London Heathrow Airport",
                "municipality": "London", "country_name": "United Kingdom",
                "latitude": 51.4706, "longitude": -0.46194,
            },
            "destination": {
                "icao_code": "LFPG", "iata_code": "CDG", "name": "Charles de Gaulle",
                "municipality": "Paris", "country_name": "France",
                "latitude": 49.0097, "longitude": 2.5479,
            },
        }
    }
}  # fmt: skip


def adsbdb() -> tuple[RouteLookup, list[str]]:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path.rsplit("/", 1)[-1])
        if request.url.path.endswith("/AFR200"):
            return httpx.Response(200, json=ADSBDB_AFR200)
        return httpx.Response(404, json={"response": "unknown callsign"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RouteLookup(base_url="https://adsbdb.test/v0", enabled=True, client=client), calls


def live() -> LiveState:
    state = LiveState()
    for vector in (
        # departed from LFPG (observed take-off), usual destination Lyon
        sv("abc123", "SKR100", 46.6, 4.3, true_track=150.0),
        # inbound from Heathrow (published route), ~118 km north-west, heading south-east
        sv("aaa200", "AFR200", 49.6, 1.2, true_track=125.0),
        # unknown route, low and descending towards LFPG: approach
        sv("aaa300", "N123AB", 49.15, 2.35, baro_altitude=1500.0, vertical_rate=-4.0,
           true_track=135.0, velocity=80.0),
        # unknown route, climbing away from LFPG: departure
        sv("aaa400", "N456CD", 48.90, 2.70, baro_altitude=1200.0, vertical_rate=8.0,
           true_track=150.0, velocity=90.0),
        # parked at LFPG
        sv("aaa500", "AFR300", 49.01, 2.55, on_ground=True, baro_altitude=None, velocity=0.0),
        # cruising over Madrid: nothing to do with Paris
        sv("aaa600", "IBE1", 40.4, -3.7, baro_altitude=10000.0),
    ):  # fmt: skip
        state.apply_state(vector)
    # Last seen one snapshot ago (60 s polling): landed or out of coverage, still on the
    # map for a while but not on the board.
    now = int(time.time())
    state.apply_state(
        sv("aaa700", "AFR999", 49.05, 2.45, baro_altitude=500.0, vertical_rate=-4.0,
           true_track=90.0, snapshot_time=now - 60)
    )  # fmt: skip
    state.apply_snapshot(
        SnapshotMeta(
            snapshot_time=now, ingested_at=now, aircraft_count=6, source="test",
            poll_interval_seconds=60,
        )
    )  # fmt: skip
    assert "aaa700" in state.aircraft
    return state


async def test_board_combines_routes_history_and_geometry(tmp_path: Path) -> None:
    marts(tmp_path)
    routes, calls = adsbdb()
    board = AirportBoard(live(), Enrichment(root=tmp_path), routes)

    result = await board.board("lfpg")
    assert result is not None
    assert result["airport"]["iata"] == "CDG"
    arrivals = {r["callsign"]: r for r in result["arrivals"]}
    departures = {r["callsign"]: r for r in result["departures"]}
    ground = {r["callsign"]: r for r in result["ground"]}
    assert set(arrivals) == {"AFR200", "N123AB"}
    assert set(departures) == {"SKR100", "N456CD"}
    assert set(ground) == {"AFR300"}

    assert arrivals["AFR200"]["source"] == "route_db"
    assert arrivals["AFR200"]["airport"]["icao"] == "EGLL"
    assert 8 * 60 < arrivals["AFR200"]["eta"]["seconds"] < 10 * 60  # ~118 km at 220 m/s
    assert arrivals["N123AB"]["source"] == "geometry"
    assert arrivals["N123AB"]["status"] == "approach"
    # sorted by ETA: the aircraft on approach lands first
    assert [r["callsign"] for r in result["arrivals"]] == ["N123AB", "AFR200"]

    assert arrivals["AFR200"]["status"] == "en_route"  # ~118 km out
    assert departures["SKR100"]["source"] == "reconstructed"
    assert departures["SKR100"]["airport"]["icao"] == "LFLL"
    assert departures["SKR100"]["departure_time"] is not None
    assert departures["N456CD"]["status"] == "climb"
    assert ground["AFR300"]["status"] == "parked"
    assert result["counts"] == {"arrivals": 2, "departures": 2, "ground": 1}

    # Lookups only near the airport (SKR100 is ~290 km away, IBE1 over Madrid).
    assert sorted(calls) == ["AFR200", "AFR300", "N123AB", "N456CD"]
    assert result["pending_lookups"] == 0
    await board.board("LFPG")
    assert len(calls) == 4  # all cached, unknown callsigns included

    history = result["history"]
    assert history["departures_24h"] == 2  # SKR100 (in flight) and SKR101
    assert history["arrivals_24h"] == 1
    assert history["recent"][0]["callsign"] == "SKR100"
    assert {a["icao"] for a in history["top_airports"]} == {"LFLL", "EGLL"}


async def test_board_without_route_lookup_or_marts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Demo profile: airports from the OurAirports file, geometry only, no history."""
    reference = tmp_path / "reference"
    reference.mkdir()
    (reference / "airports.csv").write_text(
        "id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,continent,iso_country,"
        "iso_region,municipality,scheduled_service,icao_code,iata_code,gps_code,local_code\n"
        "1,LFPG,large_airport,Paris Charles de Gaulle,49.0097,2.5479,392,EU,FR,FR-IDF,Paris,"
        "yes,LFPG,CDG,LFPG,\n"
        "2,FR-0001,small_airport,Some field,48.5,2.0,300,EU,FR,FR-IDF,,no,,,,\n"
    )
    enrichment = Enrichment(root=tmp_path / "missing", reference_root=reference)
    board = AirportBoard(live(), enrichment, RouteLookup(enabled=False))

    assert board.airports()["icao"] == ["LFPG"]  # unscheduled small fields left out
    result = await board.board("LFPG")
    assert result is not None
    assert result["airport"]["city"] == "Paris"
    assert [r["callsign"] for r in result["arrivals"]] == ["N123AB"]
    assert [r["callsign"] for r in result["departures"]] == ["N456CD"]
    assert result["history"] is None
    assert result["route_lookup"] is False
    assert await board.board("XXXX") is None


@pytest.mark.parametrize(
    ("lat", "lon", "altitude", "rate", "track", "expected"),
    [
        (49.15, 2.35, 1500.0, -4.0, 135.0, "approach"),
        (49.15, 2.35, 1500.0, -4.0, 315.0, None),  # descending, but flying away
        (48.90, 2.70, 1200.0, 8.0, 150.0, "climb"),
        (48.90, 2.70, 1200.0, 8.0, 330.0, None),  # climbing towards it: overflight
        (49.15, 2.35, 6000.0, -4.0, 135.0, None),  # too high
        (50.5, 2.5, 1500.0, -4.0, 180.0, None),  # too far
    ],
)
def test_terminal_phase(
    lat: float, lon: float, altitude: float, rate: float, track: float, expected: str | None
) -> None:
    airport = Airport("LFPG", "Paris CDG", *LFPG)
    state = sv(
        "abcdef", None, lat, lon, baro_altitude=altitude, vertical_rate=rate, true_track=track
    )
    distance = haversine_km(lat, lon, *LFPG)
    assert terminal_phase(state, airport, distance) == expected


def test_airport_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marts(tmp_path)
    monkeypatch.setenv("SKYRADAR_MARTS_ROOT", str(tmp_path))
    monkeypatch.setenv("API_CONSUME", "false")
    monkeypatch.setenv("API_RATE_AIRPORT", "2/minute")
    from skyradar_api.main import create_app

    with TestClient(create_app()) as client:
        listing = client.get("/airports")
        assert listing.status_code == 200
        assert listing.json()["iata"] == ["LHR", "LYS", "CDG"]
        assert listing.json()["kind"] == ["L", "L", "L"]
        board = client.get("/airports/LFPG/board")
        assert board.status_code == 200
        assert board.json()["history"]["arrivals_24h"] == 1
        assert client.get("/airports/ZZZZ/board").status_code == 404
        assert client.get("/airports/LFPG/board").status_code == 429
