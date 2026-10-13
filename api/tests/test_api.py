import time

import pytest
from fastapi.testclient import TestClient

from skyradar_api.config import ApiSettings
from skyradar_api.main import create_app
from skyradar_api.state import LiveState
from skyradar_common.models import (
    Alert,
    AlertStatus,
    AlertType,
    Severity,
    SnapshotMeta,
    StateVector,
)


def make_state(
    icao24: str, t: int, lon: float = 2.0, lat: float = 48.0, **kw: object
) -> StateVector:
    data: dict[str, object] = {
        "icao24": icao24,
        "callsign": f"TST{icao24[-3:]}",
        "origin_country": "France",
        "time_position": t,
        "last_contact": t,
        "longitude": lon,
        "latitude": lat,
        "baro_altitude": 10_000.0,
        "velocity": 230.0,
        "true_track": 90.0,
        "snapshot_time": t,
        "ingested_at": float(t),
    }
    data.update(kw)
    return StateVector.model_validate(data)


def meta(t: int, interval: float = 10) -> SnapshotMeta:
    return SnapshotMeta(
        snapshot_time=t,
        ingested_at=float(t),
        aircraft_count=0,
        source="test",
        poll_interval_seconds=interval,
    )


def test_trail_and_delta() -> None:
    state = LiveState(trail_length=3)
    for i in range(5):
        state.apply_state(make_state("aaaaaa", 100 + i, lon=2.0 + i))
    entry = state.aircraft["aaaaaa"]
    assert len(entry.trail) == 3
    assert entry.trail[-1][0] == 6.0
    delta = state.take_delta()
    assert delta is not None
    assert delta["upserts"]["icao24"] == ["aaaaaa"]
    assert state.take_delta() is None  # nothing new


def test_out_of_order_states_are_ignored() -> None:
    state = LiveState()
    state.apply_state(make_state("aaaaaa", 200, lon=5.0))
    state.apply_state(make_state("aaaaaa", 150, lon=1.0))
    assert state.aircraft["aaaaaa"].state.longitude == 5.0


def test_eviction_uses_poll_interval() -> None:
    state = LiveState(aircraft_ttl_seconds=60)
    state.apply_state(make_state("aaaaaa", 1000))
    state.apply_state(make_state("bbbbbb", 1950))
    state.take_delta()
    # Anonymous OpenSky polls every 960 s: TTL is stretched to 2.5 x interval.
    state.apply_snapshot(meta(2000, interval=960))
    assert set(state.aircraft) == {"aaaaaa", "bbbbbb"}
    state.apply_snapshot(meta(2000, interval=10))
    assert set(state.aircraft) == {"bbbbbb"}
    delta = state.take_delta()
    assert delta is not None
    assert delta["removed"] == ["aaaaaa"]


def test_alert_lifecycle() -> None:
    state = LiveState()
    alert = Alert(
        alert_id="a1",
        icao24="aaaaaa",
        type=AlertType.SQUAWK_EMERGENCY,
        severity=Severity.CRITICAL,
        message="7700",
        event_time=1,
        detected_at=time.time(),
    )
    state.apply_alert(alert)
    assert [a.alert_id for a in state.active_alerts()] == ["a1"]
    state.apply_alert(alert.model_copy(update={"status": AlertStatus.RESOLVED}))
    assert state.active_alerts() == []


@pytest.fixture
def client() -> TestClient:
    app = create_app(ApiSettings(API_CONSUME=False, API_WS_PUSH_INTERVAL_SECONDS=0.05))
    live: LiveState = app.state.live
    now = int(time.time())
    live.apply_state(make_state("abc123", now, squawk="7700"))
    live.apply_state(make_state("def456", now, on_ground=True, baro_altitude=None))
    live.apply_snapshot(meta(now))
    return TestClient(app)


def test_rest_endpoints(client: TestClient) -> None:
    assert client.get("/health").json()["aircraft"] == 2
    stats = client.get("/stats/live").json()
    assert stats["airborne"] == 1
    assert stats["on_ground"] == 1
    assert stats["by_origin_country"][0] == {"country": "France", "count": 2}
    detail = client.get("/aircraft/ABC123").json()
    assert detail["state"]["icao24"] == "abc123"
    assert len(detail["trail"]) == 1
    assert client.get("/aircraft/ffffff").status_code == 404
    assert client.get("/aircraft", params={"q": "tst"}).json()[0]["icao24"] in {"abc123", "def456"}
    assert client.post("/assistant/ask", json={"question": "combien ?"}).status_code == 503


def test_websocket_snapshot_then_delta(client: TestClient) -> None:
    with client, client.websocket_connect("/ws/live") as ws:
        first = ws.receive_json()
        assert first["type"] == "snapshot"
        assert sorted(first["upserts"]["icao24"]) == ["abc123", "def456"]
        assert ws.receive_json()["type"] == "alerts"
        live: LiveState = client.app.state.live  # type: ignore[attr-defined]
        live.apply_state(make_state("abc123", int(time.time()) + 10, lon=3.0))
        message = ws.receive_json()
        while message["type"] != "delta" or "abc123" not in message["upserts"]["icao24"]:
            message = ws.receive_json()
        idx = message["upserts"]["icao24"].index("abc123")
        assert message["upserts"]["lon"][idx] == 3.0
