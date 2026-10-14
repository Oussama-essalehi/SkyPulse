import httpx
import pytest

from skyradar_api.routes import FlightRoute, RouteAirport, RouteLookup, check_route

ADSBDB_BAW131 = {
    "response": {
        "flightroute": {
            "callsign": "BAW131",
            "airline": {"name": "British Airways", "icao": "BAW"},
            "origin": {
                "icao_code": "EGLL", "iata_code": "LHR", "name": "London Heathrow Airport",
                "municipality": "London", "country_name": "United Kingdom",
                "latitude": 51.4706, "longitude": -0.46194,
            },
            "destination": {
                "icao_code": "VOBL", "iata_code": "BLR", "name": "Kempegowda International Airport",
                "municipality": "Bangalore", "country_name": "India",
                "latitude": 13.1979, "longitude": 77.7063,
            },
        }
    }
}  # fmt: skip


def lookup(handler) -> tuple[RouteLookup, list[str]]:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return handler(request)  # type: ignore[no-any-return]

    client = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    return RouteLookup(base_url="https://adsbdb.test/v0", enabled=True, client=client), calls


async def test_route_lookup_parses_and_caches() -> None:
    routes, calls = lookup(lambda r: httpx.Response(200, json=ADSBDB_BAW131))
    route = await routes.route("baw131 ")
    assert route is not None
    assert (route.origin.iata, route.destination.city) == ("LHR", "Bangalore")
    assert route.airline == "British Airways"
    await routes.route("BAW131")
    assert calls == ["/v0/callsign/BAW131"]  # second call served from the cache


async def test_unknown_callsign_is_cached_but_errors_are_not() -> None:
    routes, calls = lookup(lambda r: httpx.Response(404, json={"response": "unknown callsign"}))
    assert await routes.route("XYZ123") is None
    assert await routes.route("XYZ123") is None
    assert len(calls) == 1
    failing, calls = lookup(lambda r: httpx.Response(503))
    assert await failing.route("XYZ123") is None
    assert await failing.route("XYZ123") is None
    assert len(calls) == 2  # a transient failure is retried on the next click


async def test_disabled_lookup_never_calls_out() -> None:
    routes, calls = lookup(lambda r: httpx.Response(200, json=ADSBDB_BAW131))
    routes.enabled = False
    assert await routes.route("BAW131") is None
    assert await routes.aircraft("40688f") is None
    assert calls == []


async def test_aircraft_lookup() -> None:
    payload = {
        "response": {
            "aircraft": {
                "type": "787 8", "icao_type": "B788", "manufacturer": "Boeing",
                "registration": "G-ZBJB", "registered_owner": "British Airways",
                "registered_owner_operator_flag_code": "BAW",
            }
        }
    }  # fmt: skip
    routes, calls = lookup(lambda r: httpx.Response(200, json=payload))
    info = await routes.aircraft("40688F")
    assert info is not None
    assert (info["typecode"], info["registration"]) == ("B788", "G-ZBJB")
    assert calls == ["/v0/aircraft/40688f"]


def route(origin: tuple[float, float], destination: tuple[float, float]) -> FlightRoute:
    def airport(code: str, lat: float, lon: float) -> RouteAirport:
        return RouteAirport(code, None, code, None, None, lat, lon)

    return FlightRoute("X1", airport("ORIG", *origin), airport("DEST", *destination), None)


LHR_BLR = route((51.4706, -0.46194), (13.1979, 77.7063))


@pytest.mark.parametrize(
    ("lat", "lon", "track", "on_ground", "expected"),
    [
        (48.2, 16.0, 112.0, False, True),  # over Austria, heading south-east: BAW131
        (48.2, 16.0, 292.0, False, False),  # same place, flying back to London
        (-15.0, -47.0, 112.0, False, False),  # over Brazil: stale or reused callsign
        (51.47, -0.45, 270.0, True, True),  # still at the gate: heading irrelevant
        (51.6, -0.2, 300.0, False, True),  # just took off, turning: near origin
    ],
)
def test_check_route(lat: float, lon: float, track: float, on_ground: bool, expected: bool) -> None:
    assert check_route(LHR_BLR, lat, lon, track, on_ground) is expected
