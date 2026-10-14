"""End-to-end smoke tests against a running stack.

    make up-demo   (or: make replay / make up)
    make smoke

Source -> ingestion -> Redpanda -> API -> WebSocket (direct and through the
nginx proxy of the frontend). With SMOKE_FULL=1, also checks the lakehouse
services of the "full" profile.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import httpx
import pytest
import websockets

pytestmark = pytest.mark.integration

API = os.getenv("SMOKE_API_URL", "http://localhost:8000")
FRONT = os.getenv("SMOKE_FRONT_URL", "http://localhost:3000")
TIMEOUT_S = float(os.getenv("SMOKE_TIMEOUT_SECONDS", "180"))
FULL = os.getenv("SMOKE_FULL") == "1"


def wait_for_aircraft() -> dict[str, object]:
    deadline = time.time() + TIMEOUT_S
    last: dict[str, object] = {}
    while time.time() < deadline:
        try:
            last = httpx.get(f"{API}/health", timeout=5).json()
            if int(last.get("aircraft", 0)) > 0:  # type: ignore[call-overload]
                return last
        except httpx.HTTPError:
            pass
        time.sleep(3)
    pytest.fail(f"no aircraft received within {TIMEOUT_S}s (last health: {last})")


def test_api_receives_aircraft() -> None:
    health = wait_for_aircraft()
    assert health["kafka_connected"] is True
    stats = httpx.get(f"{API}/stats/live", timeout=5).json()
    assert stats["aircraft_total"] > 0
    assert stats["airborne"] + stats["on_ground"] == stats["aircraft_total"]
    assert stats["by_origin_country"]


async def _first_messages(url: str) -> list[dict[str, object]]:
    async with websockets.connect(url, max_size=None, open_timeout=10) as ws:
        return [json.loads(await asyncio.wait_for(ws.recv(), 10)) for _ in range(2)]


@pytest.mark.parametrize(
    "url", [f"{API.replace('http', 'ws')}/ws/live", f"{FRONT.replace('http', 'ws')}/api/ws/live"]
)
def test_websocket_snapshot(url: str) -> None:
    wait_for_aircraft()
    snapshot, alerts = asyncio.run(_first_messages(url))
    assert snapshot["type"] == "snapshot"
    upserts = snapshot["upserts"]
    assert isinstance(upserts, dict)
    assert len(upserts["icao24"]) == snapshot["count"] > 0
    assert len(upserts["lat"]) == len(upserts["icao24"])
    assert alerts["type"] == "alerts"


def test_aircraft_detail_and_frontend() -> None:
    wait_for_aircraft()
    snapshot, _ = asyncio.run(_first_messages(f"{API.replace('http', 'ws')}/ws/live"))
    icao24 = snapshot["upserts"]["icao24"][0]  # type: ignore[index]
    detail = httpx.get(f"{API}/aircraft/{icao24}", timeout=5).json()
    assert detail["state"]["icao24"] == icao24
    assert detail["trail"]
    page = httpx.get(FRONT, timeout=5)
    assert page.status_code == 200
    assert "SkyRadar" in page.text
    assert httpx.get(f"{FRONT}/api/health", timeout=5).json()["status"] == "ok"


def test_airport_board() -> None:
    """LFPG is in the replay's reference (synthetic traffic) and in the real one."""
    wait_for_aircraft()
    airports = httpx.get(f"{FRONT}/api/airports", timeout=10).json()
    assert "LFPG" in airports["icao"]
    board = httpx.get(f"{API}/airports/LFPG/board", timeout=15).json()
    assert board["airport"]["icao"] == "LFPG"
    assert set(board["counts"]) == {"arrivals", "departures", "ground"}


@pytest.mark.skipif(not FULL, reason="SMOKE_FULL=1 to check the full profile")
@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:3001/server_info",  # Dagster
        "http://localhost:8088/health",  # Superset
        "http://localhost:5000/health",  # MLflow
        "http://localhost:8081/",  # Redpanda console
    ],
)
def test_full_profile_services(url: str) -> None:
    assert httpx.get(url, timeout=10).status_code == 200
