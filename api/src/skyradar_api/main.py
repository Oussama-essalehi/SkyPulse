"""SkyRadar live API.

REST + WebSocket on top of the in-memory aircraft state built from Redpanda.
No secret is ever sent to the browser: the assistant is reached through a
server-side proxy.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field

from skyradar_api.config import ApiSettings
from skyradar_api.consumer import LiveConsumer
from skyradar_api.enrichment import Enrichment
from skyradar_api.state import LiveState
from skyradar_api.ws import Broadcaster
from skyradar_common.kafka import KafkaSettings
from skyradar_common.logging import configure_logging


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or ApiSettings()
    state = LiveState(
        trail_length=settings.trail_length,
        aircraft_ttl_seconds=settings.aircraft_ttl_seconds,
        alert_ttl_seconds=settings.alert_ttl_seconds,
    )
    broadcaster = Broadcaster(state, interval=settings.ws_push_interval_seconds)
    enrichment = Enrichment(root=Path(settings.marts_root))
    consumer = LiveConsumer(
        state,
        KafkaSettings(),
        lookback_seconds=settings.bootstrap_lookback_seconds,
        alerts_lookback_seconds=settings.alerts_lookback_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        configure_logging("api")
        tasks = [asyncio.create_task(broadcaster.run())]
        if settings.consume:
            tasks.append(asyncio.create_task(consumer.supervise()))
        yield
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    app = FastAPI(
        title="SkyRadar API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.live = state
    app.state.broadcaster = broadcaster
    app.state.enrichment = enrichment
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        meta = state.last_meta
        return {
            "status": "ok",
            "kafka_connected": consumer.connected,
            "aircraft": len(state.aircraft),
            "ws_clients": broadcaster.client_count,
            "last_snapshot_age_s": round(time.time() - meta.snapshot_time, 1) if meta else None,
        }

    @app.get("/stats/live")
    async def live_stats() -> dict[str, Any]:
        return state.stats()

    @app.get("/live/region")
    async def live_region(
        lamin: float = Query(..., ge=-90, le=90),
        lomin: float = Query(..., ge=-180, le=180),
        lamax: float = Query(..., ge=-90, le=90),
        lomax: float = Query(..., ge=-180, le=180),
    ) -> dict[str, Any]:
        """Aircraft currently inside a bounding box (used by the assistant's live tool)."""
        if lamin > lamax:
            raise HTTPException(422, "lamin must be <= lamax")
        return state.region(lamin, lomin, lamax, lomax)

    @app.get("/aircraft")
    async def search(q: str = Query(..., min_length=2, max_length=10)) -> list[dict[str, Any]]:
        return state.search(q)

    @app.get("/aircraft/{icao24}")
    async def aircraft(icao24: str) -> dict[str, Any]:
        detail = state.aircraft_detail(icao24)
        if detail is None:
            raise HTTPException(404, f"aircraft {icao24} is not currently tracked")
        # Analytics layer (optional): probable route from the marts, ETA model.
        entry = state.aircraft[icao24.lower()]
        detail["aircraft"] = await asyncio.to_thread(enrichment.aircraft, entry.state)
        flight = enrichment.flight(entry.state)
        detail["flight"] = flight
        detail["eta"] = enrichment.eta(entry.state, flight)
        return detail

    @app.get("/alerts")
    async def alerts() -> list[dict[str, Any]]:
        return [a.model_dump(mode="json") for a in state.active_alerts()]

    @app.post("/assistant/ask")
    async def ask(request: AskRequest) -> Any:
        if not settings.assistant_url:
            raise HTTPException(503, "assistant service is not enabled in this deployment")
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post(
                    f"{settings.assistant_url.rstrip('/')}/ask", json=request.model_dump()
                )
        except httpx.ConnectError as exc:
            # The assistant only runs in the "full" profile.
            raise HTTPException(503, "assistant service is not running") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"assistant unreachable: {exc}") from exc
        if response.status_code >= 400:
            detail = response.json().get("detail", response.text) if response.content else ""
            raise HTTPException(response.status_code, detail)
        return response.json()

    @app.websocket("/ws/live")
    async def live(ws: WebSocket) -> None:
        await broadcaster.connect(ws)
        try:
            while True:
                await ws.receive_text()  # keep-alive / ignore client messages
        except WebSocketDisconnect:
            pass
        finally:
            broadcaster.disconnect(ws)

    return app


app = create_app()
