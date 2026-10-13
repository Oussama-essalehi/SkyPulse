"""Enrich live aircraft with the analytics layer: probable route and ETA.

Everything here is read-only and optional. The marts (Parquet written by dbt and the
ML layer) are mounted read-only; when they are absent (``demo`` profile) the API simply
returns no route and no ETA.

* Departure: the in-progress reconstructed flight of the aircraft (``fct_flights``).
* Probable arrival: the usual destination of the callsign from that departure
  (``fct_callsign_routes``), or its dominant route when the departure is unknown.
* ETA: the LightGBM model exported by Dagster (``models/eta_model.txt``), which corrects
  the naive estimate (remaining great-circle distance / ground speed). Without a model
  (not enough history yet) the naive estimate is returned, labelled as such.

The feature computation mirrors ``pipeline/dbt/models/ml/ml_eta_features.sql``.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import structlog

from skyradar_common.models import StateVector

log = structlog.get_logger(__name__)

EARTH_RADIUS_KM = 6371.0
FLIGHT_MAX_AGE_S = 20 * 3600


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


@dataclass
class Airport:
    icao: str
    name: str
    latitude: float
    longitude: float


@dataclass
class Route:
    departure: str
    arrival: str
    flights: int
    share: float


@dataclass
class EtaModel:
    booster: Any
    features: list[str]
    airport_codes: dict[str, int]
    ratio_clip: tuple[float, float]
    metrics: dict[str, float]
    trained_at: float


@dataclass
class Enrichment:
    root: Path = field(
        default_factory=lambda: Path(os.getenv("SKYRADAR_MARTS_ROOT", "/data/marts"))
    )
    reload_every_s: float = 60.0
    airports: dict[str, Airport] = field(default_factory=dict)
    routes: dict[tuple[str, str], Route] = field(default_factory=dict)
    dominant_routes: dict[str, Route] = field(default_factory=dict)
    current_flights: dict[str, tuple[str, float]] = field(default_factory=dict)
    model: EtaModel | None = None
    _signature: tuple[float, ...] = ()
    _checked_at: float = 0.0

    # ------------------------------------------------------------------ loading
    def _files(self) -> list[Path]:
        return [
            self.root / "dim_airports.parquet",
            self.root / "fct_callsign_routes.parquet",
            self.root / "fct_flights.parquet",
            self.root / "models" / "eta_model.txt",
            self.root / "models" / "eta_model.json",
        ]

    def refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._checked_at < self.reload_every_s:
            return
        self._checked_at = now
        signature = tuple(p.stat().st_mtime if p.exists() else 0.0 for p in self._files())
        if signature == self._signature:
            return
        self._signature = signature
        try:
            self._load()
        except Exception as exc:  # enrichment must never break the live API
            log.warning("enrichment_load_failed", error=str(exc))

    def _query(self, sql: str) -> list[tuple[Any, ...]]:
        return duckdb.connect(":memory:").execute(sql).fetchall()

    def _load(self) -> None:
        airports_file, routes_file, flights_file, model_file, meta_file = self._files()
        if airports_file.exists():
            self.airports = {
                icao: Airport(icao, name, float(lat), float(lon))
                for icao, name, lat, lon in self._query(
                    "select airport_icao, airport_name, latitude, longitude "
                    f"from read_parquet('{airports_file}')"
                )
            }
        if routes_file.exists():
            rows = self._query(
                "select callsign, departure_airport, usual_arrival_airport, route_flights, "
                f"usual_arrival_share from read_parquet('{routes_file}')"
            )
            self.routes = {(c, d): Route(d, a, int(n), float(s)) for c, d, a, n, s in rows}
            self.dominant_routes = {}
            for (callsign, _), route in self.routes.items():
                best = self.dominant_routes.get(callsign)
                if best is None or route.flights > best.flights:
                    self.dominant_routes[callsign] = route
        if flights_file.exists():
            # Latest flight of each aircraft that has a departure but no arrival yet.
            rows = self._query(
                f"""
                select icao24, departure_airport, epoch(departure_time)
                from read_parquet('{flights_file}')
                where has_departure and not has_arrival
                  and departure_time >= now()::timestamp - interval {FLIGHT_MAX_AGE_S} second
                qualify row_number() over (partition by icao24 order by departure_time desc) = 1
                """
            )
            self.current_flights = {icao: (dep, float(t)) for icao, dep, t in rows}
        self.model = self._load_model(model_file, meta_file)
        log.info(
            "enrichment_loaded",
            airports=len(self.airports),
            routes=len(self.routes),
            current_flights=len(self.current_flights),
            eta_model=self.model is not None,
        )

    @staticmethod
    def _load_model(model_file: Path, meta_file: Path) -> EtaModel | None:
        if not (model_file.exists() and meta_file.exists()):
            return None
        import lightgbm as lgb

        meta = json.loads(meta_file.read_text())
        clip = meta.get("ratio_clip", [0.2, 5.0])
        return EtaModel(
            booster=lgb.Booster(model_file=str(model_file)),
            features=meta["features"],
            airport_codes=meta["airport_codes"],
            ratio_clip=(float(clip[0]), float(clip[1])),
            metrics=meta.get("metrics", {}),
            trained_at=meta.get("trained_at", 0.0),
        )

    # ------------------------------------------------------------------ queries
    def flight(self, state: StateVector) -> dict[str, Any] | None:
        self.refresh()
        current = self.current_flights.get(state.icao24)
        route: Route | None = None
        source = None
        if current is not None and state.callsign:
            route = self.routes.get((state.callsign, current[0]))
            source = "usual destination of this callsign from its departure airport"
        if route is None and state.callsign and state.callsign in self.dominant_routes:
            route = self.dominant_routes[state.callsign]
            source = "most frequent route of this callsign"
        if current is None and route is None:
            return None
        departure = current[0] if current else (route.departure if route else None)
        return {
            "departure": departure,
            "departure_name": self._name(departure),
            "departure_time": current[1] if current else None,
            "departure_source": "reconstructed" if current else "usual route",
            "arrival": route.arrival if route else None,
            "arrival_name": self._name(route.arrival if route else None),
            "arrival_source": source,
            "route_flights": route.flights if route else None,
            "route_share": route.share if route else None,
        }

    def _name(self, icao: str | None) -> str | None:
        airport = self.airports.get(icao) if icao else None
        return airport.name if airport else None

    def eta(self, state: StateVector, flight: dict[str, Any] | None) -> dict[str, Any] | None:
        if flight is None or not flight.get("arrival") or state.on_ground:
            return None
        airport = self.airports.get(flight["arrival"])
        velocity = state.velocity or 0.0
        if airport is None or velocity < 30:
            return None
        remaining = haversine_km(
            state.latitude, state.longitude, airport.latitude, airport.longitude
        )
        naive = remaining * 1000 / max(velocity, 30.0)
        position_time = state.time_position or state.last_contact
        seconds, method = naive, "naive"
        if self.model is not None:
            seconds, method = (
                self._predict(state, airport, remaining, naive, flight.get("departure_time")),
                "lightgbm",
            )
        return {
            "airport": airport.icao,
            "remaining_km": round(remaining, 1),
            "seconds": round(seconds),
            "arrival_estimate": position_time + round(seconds),
            "naive_seconds": round(naive),
            "method": method,
            "model_mae_min": round(self.model.metrics.get("mae_min", 0), 2) if self.model else None,
        }

    def _predict(
        self,
        state: StateVector,
        airport: Airport,
        remaining: float,
        naive: float,
        departure_time: float | None,
    ) -> float:
        assert self.model is not None
        position_time = state.time_position or state.last_contact
        moment = datetime.fromtimestamp(position_time, tz=UTC)
        track = state.true_track or 0.0
        values = {
            "naive_eta_min": naive / 60,
            "remaining_km": remaining,
            "velocity_ms": state.velocity or 0.0,
            "altitude_m": state.altitude if state.altitude is not None else np.nan,
            "vertical_rate_ms": state.vertical_rate or 0.0,
            "heading_alignment": math.cos(
                math.radians(
                    track
                    - bearing_deg(
                        state.latitude, state.longitude, airport.latitude, airport.longitude
                    )
                )
            ),
            "hour_utc": moment.hour,
            "day_of_week": moment.isoweekday(),
            "elapsed_min": (position_time - departure_time) / 60 if departure_time else np.nan,
            "arrival_airport_code": self.model.airport_codes.get(airport.icao, -1),
        }
        row = np.array([[values[f] for f in self.model.features]], dtype=float)
        ratio = float(np.clip(self.model.booster.predict(row)[0], *self.model.ratio_clip))
        return naive * ratio
