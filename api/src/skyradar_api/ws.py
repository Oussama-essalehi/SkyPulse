"""WebSocket fan-out: one encoded delta per tick, sent to every client."""

from __future__ import annotations

import asyncio
import contextlib

import orjson
import structlog
from fastapi import WebSocket

from skyradar_api.state import LiveState

log = structlog.get_logger(__name__)


class Broadcaster:
    def __init__(self, state: LiveState, interval: float = 1.0, send_timeout: float = 10.0) -> None:
        self._state = state
        self._interval = interval
        self._send_timeout = send_timeout
        self._clients: set[WebSocket] = set()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        # A new client first receives the full picture, then incremental deltas.
        await ws.send_text(orjson.dumps(self._state.full_message()).decode())
        await ws.send_text(orjson.dumps(self._state.alerts_message()).decode())
        self._clients.add(ws)
        log.info("ws_connected", clients=len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def _send_all(self, payload: str) -> None:
        async def send(ws: WebSocket) -> None:
            try:
                await asyncio.wait_for(ws.send_text(payload), self._send_timeout)
            except Exception:  # slow or gone client: drop it
                self._clients.discard(ws)
                with contextlib.suppress(Exception):
                    await ws.close()

        await asyncio.gather(*(send(ws) for ws in list(self._clients)))

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            delta = self._state.take_delta()
            alerts_changed = self._state.alerts_dirty
            self._state.alerts_dirty = False
            if not self._clients:
                continue
            if delta is not None:
                await self._send_all(orjson.dumps(delta).decode())
            if alerts_changed:
                await self._send_all(orjson.dumps(self._state.alerts_message()).decode())
