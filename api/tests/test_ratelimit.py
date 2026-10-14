import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from skyradar_api.config import ApiSettings
from skyradar_api.main import create_app
from skyradar_api.ratelimit import RateLimiter, Rule, parse_rules


def test_parse_rules() -> None:
    assert parse_rules("5/minute, 40/day") == [Rule(5, 60), Rule(40, 86400)]
    assert parse_rules("off") == []
    assert parse_rules("") == []
    with pytest.raises(ValueError, match="unit"):
        parse_rules("5/week")


def test_sliding_windows() -> None:
    limiter = RateLimiter("t", parse_rules("2/minute,3/hour"), "slow down")
    assert limiter.check("a", now=0) is None
    assert limiter.check("a", now=1) is None
    assert limiter.check("a", now=2) == pytest.approx(58)  # 2/minute reached
    assert limiter.check("b", now=2) is None  # per key
    assert limiter.check("a", now=61) is None  # the minute window slid
    wait = limiter.check("a", now=62)  # 3/hour reached
    assert wait is not None
    assert wait > 3000


def app_client(**settings: object) -> TestClient:
    return TestClient(create_app(ApiSettings(API_CONSUME=False, **settings)))  # type: ignore[arg-type]


def test_assistant_is_rate_limited_per_client_and_globally() -> None:
    # Unreachable assistant: allowed calls answer 503, refused ones 429.
    client = app_client(
        ASSISTANT_URL="http://127.0.0.1:9",
        API_RATE_ASSISTANT="2/minute",
        API_ASSISTANT_BUDGET="3/day",
    )
    ask = {"question": "Combien d'avions ?"}
    alice = {"X-Real-IP": "203.0.113.1"}
    assert client.post("/assistant/ask", json=ask, headers=alice).status_code == 503
    assert client.post("/assistant/ask", json=ask, headers=alice).status_code == 503
    refused = client.post("/assistant/ask", json=ask, headers=alice)
    assert refused.status_code == 429
    assert "Retry-After" in refused.headers
    assert "Trop de questions" in refused.json()["detail"]
    # Another client still gets through... until the global daily budget is spent.
    bob = {"X-Real-IP": "203.0.113.2"}
    assert client.post("/assistant/ask", json=ask, headers=bob).status_code == 503
    budget = client.post("/assistant/ask", json=ask, headers=bob)
    assert budget.status_code == 429
    assert "quota du jour" in budget.json()["detail"]


def test_aircraft_detail_is_rate_limited() -> None:
    client = app_client(API_RATE_AIRCRAFT="2/minute")
    assert client.get("/aircraft/abc123").status_code == 404
    assert client.get("/aircraft/abc123").status_code == 404
    assert client.get("/aircraft/abc123").status_code == 429


def test_websocket_connections_per_client() -> None:
    client = app_client(API_MAX_WS_PER_CLIENT=1)
    with client.websocket_connect("/ws/live") as first:
        first.receive_text()
        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/live") as second:
            second.receive_text()
    # The slot is released when the first connection closes.
    with client.websocket_connect("/ws/live") as again:
        again.receive_text()


def test_metrics_endpoint() -> None:
    client = app_client(API_RATE_AIRCRAFT="1/minute")
    client.get("/aircraft/abc123")
    client.get("/aircraft/abc123")  # refused
    response = client.get("/metrics/", headers={"Accept-Encoding": "gzip"})
    # Compressed once (Prometheus cannot read a double gzip; httpx can).
    assert response.headers.get_list("content-encoding") == ["gzip"]
    body = response.text
    assert "skyradar_api_aircraft " in body
    assert "skyradar_api_kafka_connected 0.0" in body
    assert 'skyradar_api_rate_limited_total{limiter="aircraft"}' in body
    assert 'route="/aircraft/{icao24}",status="429"' in body
