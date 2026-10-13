"""OAuth2 client-credentials token management for the OpenSky API.

Since March 2026 OpenSky only accepts OAuth2 bearer tokens (basic auth with
username/password was removed). Tokens expire after ~30 minutes: we cache the
token and refresh it shortly before expiry, or immediately after a 401.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import httpx
import structlog

log = structlog.get_logger(__name__)

DEFAULT_TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
)


class TokenError(RuntimeError):
    pass


class OAuth2TokenManager:
    def __init__(
        self,
        client: httpx.AsyncClient,
        client_id: str,
        client_secret: str,
        token_url: str = DEFAULT_TOKEN_URL,
        refresh_margin_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._refresh_margin = refresh_margin_seconds
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()
        self._monotonic = clock

    @property
    def expires_in(self) -> float:
        return max(0.0, self._expires_at - self._monotonic())

    def invalidate(self) -> None:
        self._token = None
        self._expires_at = 0.0

    async def get_token(self) -> str:
        async with self._lock:
            if self._token and self._monotonic() < self._expires_at - self._refresh_margin:
                return self._token
            return await self._fetch()

    async def _fetch(self) -> str:
        try:
            response = await self._client.post(
                self._token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            raise TokenError(f"token request failed: {exc}") from exc
        if response.status_code != 200:
            raise TokenError(
                f"token endpoint returned {response.status_code}: {response.text[:200]}"
            )
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise TokenError("token endpoint response has no access_token")
        expires_in = float(payload.get("expires_in", 1800))
        self._token = token
        self._expires_at = self._monotonic() + expires_in
        log.info("opensky_token_refreshed", expires_in=expires_in)
        return str(token)
