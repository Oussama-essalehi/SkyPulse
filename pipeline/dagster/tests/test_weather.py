from pathlib import Path

import httpx
import pyarrow.parquet as pq
import pytest

from skyradar_orchestration import weather


def fake_api(request: httpx.Request) -> httpx.Response:
    lats = request.url.params["latitude"].split(",")
    location = {
        "hourly": {
            "time": ["2026-10-13T00:00", "2026-10-13T01:00"],
            "wind_speed_250hPa": [30.5, None],
            "wind_direction_250hPa": [270, 265],
        }
    }
    return httpx.Response(200, json=[location] * len(lats) if len(lats) > 1 else location)


@pytest.fixture
def lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SKYRADAR_LAKE_ROOT", str(tmp_path))
    return tmp_path


def test_fetch_whole_grid_in_chunks() -> None:
    client = httpx.Client(transport=httpx.MockTransport(fake_api))
    table = weather.fetch(weather.grid(), client, fetched_at=1.0)
    assert len(weather.grid()) == 12 * 36
    # One valid hour per point (the null value is dropped).
    assert table.num_rows == len(weather.grid())
    assert table.schema == weather.WINDS_SCHEMA
    assert "hour" not in table.column_names  # would clash with the hour= partition key


def test_update_writes_once_per_day(lake: Path) -> None:
    client = httpx.Client(transport=httpx.MockTransport(fake_api))
    now = 1_791_936_000.0  # 2026-10-14
    first = weather.update(now=now, client=client)
    assert first.status == "fetched"
    files = list((lake / "raw" / "upper_winds" / "date=2026-10-14" / "hour=00").glob("*.parquet"))
    assert len(files) == 1
    assert pq.read_table(files[0]).num_rows == first.rows
    assert weather.update(now=now + 3600, client=client).status == "skipped"


def test_api_errors_do_not_raise(lake: Path) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    report = weather.update(now=1_791_936_000.0, client=client)
    assert report.status == "failed"
    assert "Open-Meteo" in report.reason
