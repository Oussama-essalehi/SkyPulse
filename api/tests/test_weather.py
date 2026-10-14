from pathlib import Path

import duckdb
import httpx
import pytest
from fastapi.testclient import TestClient

from skyradar_api.weather import AirportWeather, describe_weather, from_metar, humidity

# Shapes returned by https://aviationweather.gov/api/data/metar?ids=...&format=json
LFPG = {
    "icaoId": "LFPG", "obsTime": 1791999000, "temp": 18, "dewp": 10, "wdir": 110,
    "wspd": 6, "visib": "6+", "altim": 1028, "clouds": [],
    "rawOb": "METAR LFPG 141730Z 11006KT CAVOK 18/10 Q1028 NOSIG", "fltCat": "VFR",
}  # fmt: skip
KJFK = {
    "icaoId": "KJFK", "obsTime": 1791996660, "temp": 22.8, "dewp": 18.9, "wdir": 270,
    "wspd": 6, "wgst": 18, "visib": "10+", "altim": 1011.6, "wxString": "-RA",
    "clouds": [{"cover": "FEW", "base": 3600}, {"cover": "BKN", "base": 7500},
               {"cover": "BKN", "base": 12000}],
    "rawOb": "METAR KJFK 141651Z 27006G18KT 10SM -RA FEW036 BKN075 BKN120 23/19 A2987",
    "fltCat": "VFR",
}  # fmt: skip


def test_decode_cavok_metar() -> None:
    weather = from_metar(LFPG)
    assert weather["temperature_c"] == 18
    assert weather["wind_direction_deg"] == 110
    assert weather["wind_kmh"] == 11  # 6 kt
    assert weather["visibility_km"] == 9.7
    assert weather["visibility_plus"] is True
    assert weather["pressure_hpa"] == 1028
    assert weather["sky"] == "ciel dégagé"
    assert weather["conditions"] is None
    assert weather["humidity_pct"] == 60


def test_decode_rain_gusts_and_ceiling() -> None:
    weather = from_metar(KJFK)
    assert weather["gust_kmh"] == 33
    assert weather["conditions"] == "pluie faible"
    assert weather["sky"] == "nuageux"  # lowest broken layer is the ceiling...
    assert weather["cloud_base_m"] == 2290  # ...at 7 500 ft
    assert weather["visibility_km"] == 16.1


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("-SHRA BR", "averses de pluie faible, brume"),
        ("+TSRA", "orage pluie forte"),
        ("FG", "brouillard"),
        ("VCSH", "averses à proximité"),
        (None, None),
    ],
)
def test_describe_weather(code: str | None, expected: str | None) -> None:
    assert describe_weather(code) == expected


def test_humidity() -> None:
    assert humidity(20, 20) == 100
    assert humidity(None, 10) is None


async def test_lookup_is_cached_and_handles_stations_without_metar() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["ids"])
        return httpx.Response(200, json=[LFPG] if request.url.params["ids"] == "LFPG" else [])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    weather = AirportWeather(base_url="https://metar.test/api", enabled=True, client=client)
    first = await weather.current("lfpg")
    assert first is not None
    assert first["station"] == "LFPG"
    assert await weather.current("LFPG") == first
    assert await weather.current("LFXX") is None  # no station: cached as such
    assert await weather.current("LFXX") is None
    assert calls == ["LFPG", "LFXX"]


async def test_errors_are_not_cached() -> None:
    attempts = iter([httpx.Response(503), httpx.Response(200, json=[LFPG])])
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: next(attempts)))
    weather = AirportWeather(base_url="https://metar.test/api", enabled=True, client=client)
    assert await weather.current("LFPG") is None
    assert (await weather.current("LFPG") or {}).get("temperature_c") == 18


async def test_disabled_never_calls_out() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no call expected")

    client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    assert await AirportWeather(enabled=False, client=client).current("LFPG") is None


def test_weather_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    duckdb.sql(
        "copy (select 'LFPG' airport_icao, 'CDG' airport_iata, 'Paris CDG' airport_name, "
        "'large_airport' airport_type, 'Paris' municipality, 'FR' iso_country, "
        f"49.0 latitude, 2.5 longitude) to '{tmp_path / 'dim_airports.parquet'}' (format parquet)"
    )
    monkeypatch.setenv("SKYRADAR_MARTS_ROOT", str(tmp_path))
    monkeypatch.setenv("API_CONSUME", "false")
    from skyradar_api.main import create_app

    with TestClient(create_app()) as client:
        body = client.get("/airports/lfpg/weather").json()
        assert body == {"airport": "LFPG", "enabled": False, "weather": None}
        assert client.get("/airports/ZZZZ/weather").status_code == 404
