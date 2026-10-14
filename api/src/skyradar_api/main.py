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
from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from prometheus_client import make_asgi_app
from pydantic import BaseModel, Field

from skyradar_api import metrics
from skyradar_api.airports import AirportBoard
from skyradar_api.config import ApiSettings
from skyradar_api.consumer import LiveConsumer
from skyradar_api.enrichment import Enrichment
from skyradar_api.ratelimit import RateLimiter, client_ip, parse_rules
from skyradar_api.routes import RouteLookup
from skyradar_api.state import LiveState
from skyradar_api.weather import AirportWeather
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
    routes = RouteLookup()
    assistant_limit = RateLimiter(
        "assistant",
        parse_rules(settings.rate_assistant),
        "Trop de questions : réessayez dans un moment.",
    )
    assistant_budget = RateLimiter(
        "assistant-budget",
        parse_rules(settings.assistant_daily_budget),
        "L'assistant a atteint son quota du jour sur cette démo : revenez demain.",
    )
    aircraft_limit = RateLimiter(
        "aircraft", parse_rules(settings.rate_aircraft), "Trop de requêtes : ralentissez."
    )
    airport_limit = RateLimiter(
        "airport", parse_rules(settings.rate_airport), "Trop de requêtes : ralentissez."
    )
    boards = AirportBoard(state, enrichment, routes)
    weather = AirportWeather()
    ws_clients: dict[str, int] = {}
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
        await routes.aclose()
        await weather.aclose()

    app = FastAPI(
        title="SkyRadar API",
        version="0.1.0",
        lifespan=lifespan,
    )
    # Prometheus: live gauges read at scrape time, request counter per route template.
    metrics.AIRCRAFT.set_function(lambda: len(state.aircraft))
    metrics.WS_CLIENTS.set_function(lambda: broadcaster.client_count)
    metrics.KAFKA_CONNECTED.set_function(lambda: float(consumer.connected))
    metrics.SNAPSHOT_AGE.set_function(
        lambda: time.time() - state.last_meta.snapshot_time if state.last_meta else float("nan")
    )
    # GZipMiddleware below compresses large responses: the metrics app must not as well.
    app.mount("/metrics", make_asgi_app(disable_compression=True))

    @app.middleware("http")
    async def count_requests(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        route = request.scope.get("route")
        template = getattr(route, "path", "other")
        if template != "/metrics":
            metrics.REQUESTS.labels(request.method, template, response.status_code).inc()
        return response

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
    async def aircraft(icao24: str, request: Request) -> dict[str, Any]:
        aircraft_limit.hit(client_ip(request, settings.client_ip_header))
        detail = state.aircraft_detail(icao24)
        if detail is None:
            raise HTTPException(404, f"aircraft {icao24} is not currently tracked")
        # Analytics layer (optional): probable route from the marts, ETA model.
        entry = state.aircraft[icao24.lower()]
        info, published = await asyncio.gather(
            asyncio.to_thread(enrichment.aircraft, entry.state),
            routes.route(entry.state.callsign),
        )
        if info is None or not info.get("in_registry"):
            # Not in the OpenSky database (2024 snapshot): ask adsbdb.
            extra = await routes.aircraft(entry.state.icao24)
            if extra is not None:
                known = {k: v for k, v in (info or {}).items() if v is not None}
                info = {"airline_icao": None, "airline_name": None, **extra, **known}
                info["in_registry"] = False
                info["source"] = "adsbdb"
        if info is not None and not info.get("airline_name") and published is not None:
            info["airline_name"] = published.airline
        detail["aircraft"] = info
        flight = enrichment.flight(entry.state, published)
        detail["flight"] = flight
        detail["eta"] = enrichment.eta(entry.state, flight)
        return detail

    @app.get("/airports")
    async def airports(response: Response) -> dict[str, list[Any]]:
        """Airports of the reference (column-oriented), for the map layer and the search."""
        response.headers["Cache-Control"] = "public, max-age=600"
        return await asyncio.to_thread(boards.airports)

    @app.get("/airports/{icao}/board")
    async def airport_board(
        icao: str, request: Request, limit: int = Query(40, ge=1, le=500)
    ) -> dict[str, Any]:
        """Live arrivals, departures and aircraft on the ground, and the last 24 hours.

        ``limit`` caps each list (the airport filter of the map asks for all of them).
        """
        airport_limit.hit(client_ip(request, settings.client_ip_header))
        board = await boards.board(icao, limit)
        if board is None:
            raise HTTPException(404, f"unknown airport {icao}")
        return board

    @app.get("/airports/{icao}/weather")
    async def airport_weather(icao: str, request: Request) -> dict[str, Any]:
        """Latest METAR of the airport, decoded (temperature, wind, visibility, clouds)."""
        airport_limit.hit(client_ip(request, settings.client_ip_header))
        enrichment.refresh()
        if icao.upper() not in enrichment.airports:
            raise HTTPException(404, f"unknown airport {icao}")
        return {
            "airport": icao.upper(),
            "enabled": weather.enabled,
            "weather": await weather.current(icao),
        }

    @app.get("/alerts")
    async def alerts() -> list[dict[str, Any]]:
        return [a.model_dump(mode="json") for a in state.active_alerts()]

    @app.post("/assistant/ask")
    async def ask(request: AskRequest, http: Request) -> Any:
        if not settings.assistant_url:
            raise HTTPException(503, "assistant service is not enabled in this deployment")
        assistant_limit.hit(client_ip(http, settings.client_ip_header))
        assistant_budget.hit("all")
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
        ip = client_ip(ws, settings.client_ip_header)
        if ws_clients.get(ip, 0) >= settings.max_ws_per_client:
            await ws.close(code=1013, reason="too many connections from this client")
            return
        ws_clients[ip] = ws_clients.get(ip, 0) + 1
        try:
            await broadcaster.connect(ws)
            while True:
                await ws.receive_text()  # keep-alive / ignore client messages
        except WebSocketDisconnect:
            pass
        finally:
            broadcaster.disconnect(ws)
            ws_clients[ip] -= 1
            if ws_clients[ip] <= 0:
                del ws_clients[ip]

    return app


app = create_app()
