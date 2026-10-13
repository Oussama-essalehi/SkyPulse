import httpx
import pytest
import respx

from skyradar_ingestion.auth import DEFAULT_TOKEN_URL
from skyradar_ingestion.sources.opensky import (
    DEFAULT_API_URL,
    OpenSkySource,
    RateLimitedError,
    parse_states,
)

# fmt: off
PAYLOAD = {
    "time": 1_700_000_000,
    "states": [
        # Complete row (18 fields, extended=1)
        ["3c6444", "DLH9LF  ", "Germany", 1_699_999_998, 1_699_999_999, 8.55, 50.03, 11_277.6,
         False, 236.5, 268.4, 0.0, None, 11_521.4, "1000", False, 0, 4],
        # No position -> skipped
        ["abcdef", "", "France", None, 1_699_999_999, None, None, None, True, 0.0, None, None,
         None, None, None, False, 0, 0],
        # Legacy 17-field row (no category)
        ["4b1814", "SWR123", "Switzerland", 1_699_999_990, 1_699_999_995, 8.6, 47.4, None,
         True, 5.0, 90.0, None, None, None, "7700", False, 0],
        # Invalid latitude -> skipped by validation
        ["a00001", "BAD1", "United States", 1, 1, 10.0, 123.0, 100.0, False, 1.0, 1.0, 0.0,
         None, 100.0, None, False, 0, 0],
    ],
}
# fmt: on


def test_parse_states() -> None:
    snapshot_time, states, skipped = parse_states(PAYLOAD, ingested_at=1_700_000_002.0)
    assert snapshot_time == 1_700_000_000
    assert skipped == 2
    assert [s.icao24 for s in states] == ["3c6444", "4b1814"]
    first, second = states
    assert first.callsign == "DLH9LF"
    assert first.category == 4
    assert first.altitude == 11_277.6
    assert second.category is None
    assert second.on_ground is True
    assert second.squawk == "7700"
    assert all(s.snapshot_time == 1_700_000_000 for s in states)


def test_parse_empty_payload() -> None:
    assert parse_states({"time": 5, "states": None}, ingested_at=6.0) == (5, [], 0)


@respx.mock
async def test_fetch_authenticated_logs_credits() -> None:
    respx.post(DEFAULT_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "abc", "expires_in": 1800})
    )
    route = respx.get(f"{DEFAULT_API_URL}/states/all").mock(
        return_value=httpx.Response(200, json=PAYLOAD, headers={"X-Rate-Limit-Remaining": "3996"})
    )
    source = OpenSkySource(96, client_id="id", client_secret="secret")
    snapshot = await source.fetch()
    await source.aclose()
    assert route.calls[0].request.headers["Authorization"] == "Bearer abc"
    assert route.calls[0].request.url.params["extended"] == "1"
    assert snapshot.credits_remaining == 3996
    assert len(snapshot.states) == 2
    assert source.tier.value == "standard"


@respx.mock
async def test_fetch_retries_once_on_401() -> None:
    respx.post(DEFAULT_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "abc", "expires_in": 1800})
    )
    respx.get(f"{DEFAULT_API_URL}/states/all").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json=PAYLOAD)]
    )
    source = OpenSkySource(96, client_id="id", client_secret="secret")
    snapshot = await source.fetch()
    await source.aclose()
    assert len(snapshot.states) == 2


@respx.mock
async def test_rate_limited_raises_with_retry_after() -> None:
    respx.get(f"{DEFAULT_API_URL}/states/all").mock(
        return_value=httpx.Response(429, headers={"X-Rate-Limit-Retry-After-Seconds": "1234"})
    )
    source = OpenSkySource(960)  # anonymous
    with pytest.raises(RateLimitedError) as info:
        await source.fetch()
    await source.aclose()
    assert info.value.retry_after == 1234
    assert "Authorization" not in respx.calls[0].request.headers


def test_backoff_is_exponential_and_honours_hint() -> None:
    source = OpenSkySource(96, max_backoff_seconds=3600)
    d1, d2, d3 = (source._backoff(n) for n in (1, 2, 3))
    assert 96 <= d1 < 96 * 1.11
    assert 192 <= d2 < 192 * 1.11
    assert 384 <= d3 < 384 * 1.11
    assert source._backoff(1, hint=1000) >= 1000
    assert source._backoff(20) <= 3600 * 1.1


def test_transient_backoff_is_short_and_bounded() -> None:
    source = OpenSkySource(960)
    assert 30 <= source._transient_backoff(1) < 34
    assert 60 <= source._transient_backoff(2) < 67
    # After MAX_TRANSIENT_RETRIES quick retries, fall back to the polling interval.
    assert source._transient_backoff(10) >= 960
