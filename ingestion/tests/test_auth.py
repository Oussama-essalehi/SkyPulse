import httpx
import pytest
import respx

from skyradar_ingestion.auth import DEFAULT_TOKEN_URL, OAuth2TokenManager, TokenError


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@respx.mock
async def test_token_is_cached_until_close_to_expiry() -> None:
    route = respx.post(DEFAULT_TOKEN_URL).mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "t1", "expires_in": 1800}),
            httpx.Response(200, json={"access_token": "t2", "expires_in": 1800}),
        ]
    )
    clock = FakeClock()
    async with httpx.AsyncClient() as client:
        manager = OAuth2TokenManager(client, "id", "secret", clock=clock)
        assert await manager.get_token() == "t1"
        clock.now += 1000
        assert await manager.get_token() == "t1"  # still valid
        clock.now += 750  # 1750s elapsed: within the 60s refresh margin
        assert await manager.get_token() == "t2"
    assert route.call_count == 2
    body = route.calls[0].request.content.decode()
    assert "grant_type=client_credentials" in body
    assert "client_id=id" in body


@respx.mock
async def test_invalidate_forces_refresh() -> None:
    route = respx.post(DEFAULT_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 1800})
    )
    async with httpx.AsyncClient() as client:
        manager = OAuth2TokenManager(client, "id", "secret")
        await manager.get_token()
        manager.invalidate()
        await manager.get_token()
    assert route.call_count == 2


@respx.mock
async def test_token_error_on_bad_credentials() -> None:
    respx.post(DEFAULT_TOKEN_URL).mock(return_value=httpx.Response(401, json={"error": "nope"}))
    async with httpx.AsyncClient() as client:
        manager = OAuth2TokenManager(client, "id", "bad")
        with pytest.raises(TokenError, match="401"):
            await manager.get_token()
