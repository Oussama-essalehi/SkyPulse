"""Enrich live aircraft with the analytics layer: aircraft, airline, route and ETA.

Everything here is read-only and optional. The marts (Parquet written by dbt and the
ML layer) are mounted read-only; when they are absent (``demo`` profile) the API simply
returns no route and no ETA.

* Departure: the in-progress reconstructed flight of the aircraft (``fct_flights``).
* Probable arrival: the usual destination of the callsign from that departure
  (``fct_callsign_routes``), or its dominant route when the departure is unknown.
* Aircraft: registration, type and operator from the OpenSky aircraft database
  (``dim_aircraft_registry``, point lookups, cached); airline from the callsign prefix
  (``dim_airlines``).
* ETA: the LightGBM model exported by Dagster (``models/eta_model.txt``), which corrects
  the naive estimate (remaining great-circle distance / ground speed). Without a model
  (not enough history yet) the naive estimate is returned, labelled as such.

The feature computation mirrors ``pipeline/dbt/models/ml/ml_eta_features.sql``.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import structlog

from skyradar_api.routes import FlightRoute, RouteAirport, check_route
from skyradar_common.models import StateVector

log = structlog.get_logger(__name__)

EARTH_RADIUS_KM = 6371.0
FLIGHT_MAX_AGE_S = 20 * 3600
AIRLINE_CALLSIGN = re.compile(r"^([A-Z]{3})\d")
REGISTRY_CACHE_SIZE = 20_000
REGISTRY_COLUMNS = (
    "registration",
    "typecode",
    "manufacturer",
    "model",
    "type_name",
    "wake_category",
    "engine_type",
    "engine_count",
    "operator",
    "operator_icao",
    "built_year",
)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def _round_half_away(x: float) -> float:
    """SQL ``round()`` (half away from zero), unlike Python's banker's rounding."""
    return math.copysign(math.floor(abs(x) + 0.5), x)


def wind_cell(lat: float, lon: float) -> tuple[int, int]:
    """10 degree wind grid cell; mirrors the wind_grid_lat / wind_grid_lon dbt macros."""
    grid_lat = int(min(70, max(-40, _round_half_away(lat / 10) * 10)))
    grid_lon = int(_round_half_away(lon / 10) * 10)
    return grid_lat, -180 if grid_lon >= 180 else grid_lon


def tailwind_ms(speed: float, direction: float, track: float) -> float:
    """Tail-wind component of a wind blowing FROM ``direction``; mirrors the dbt macro."""
    return -speed * math.cos(math.radians(track - direction))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _route_airport(airport: RouteAirport) -> dict[str, Any]:
    return {
        "icao": airport.icao,
        "iata": airport.iata,
        "name": airport.name,
        "city": airport.city,
        "country": airport.country,
        "latitude": airport.latitude,
        "longitude": airport.longitude,
    }


@dataclass
class Airport:
    icao: str
    name: str
    latitude: float
    longitude: float
    iata: str | None = None
    city: str | None = None
    country: str | None = None


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
    airlines: dict[str, str] = field(default_factory=dict)
    # (grid_lat, grid_lon, hour epoch) -> (speed m/s, direction from, degrees)
    winds: dict[tuple[int, int, int], tuple[float, float]] = field(default_factory=dict)
    _registry_cache: dict[str, dict[str, Any] | None] = field(default_factory=dict)
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
            self.root / "dim_airlines.parquet",
            self.root / "dim_aircraft_registry.parquet",
            self.root / "fct_upper_winds.parquet",
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

    def _records(self, sql: str) -> list[dict[str, Any]]:
        cursor = duckdb.connect(":memory:").execute(sql)
        names = [d[0] for d in cursor.description or []]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def _load(self) -> None:
        (
            airports_file,
            routes_file,
            flights_file,
            model_file,
            meta_file,
            airlines_file,
            _registry_file,
            winds_file,
        ) = self._files()
        self._registry_cache = {}
        if winds_file.exists():
            self.winds = {
                (int(lat), int(lon), int(epoch)): (float(speed), float(direction))
                for lat, lon, epoch, speed, direction in self._query(
                    "select grid_lat, grid_lon, epoch(hour), wind_speed_ms, wind_direction_deg "
                    f"from read_parquet('{winds_file}')"
                )
            }
        if airlines_file.exists():
            self.airlines = dict(
                self._query(
                    f"select airline_icao, airline_name from read_parquet('{airlines_file}')"
                )
            )
        if airports_file.exists():
            self.airports = {}
            for row in self._records(f"select * from read_parquet('{airports_file}')"):
                icao = row["airport_icao"]
                self.airports[icao] = Airport(
                    icao,
                    row["airport_name"],
                    float(row["latitude"]),
                    float(row["longitude"]),
                    row.get("airport_iata"),
                    row.get("municipality"),
                    row.get("iso_country"),
                )
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
            airlines=len(self.airlines),
            wind_cells=len(self.winds),
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
    def aircraft(self, state: StateVector) -> dict[str, Any] | None:
        """Type and operator of the aircraft, airline of its current callsign.

        Blocking (a DuckDB point query on a ~16 MB Parquet file, ~15 ms warm): call it
        from a worker thread.
        """
        self.refresh()
        info = self._registry(state.icao24)
        match = AIRLINE_CALLSIGN.match(state.callsign or "")
        airline = match.group(1) if match else (info or {}).get("operator_icao")
        if info is None and airline is None:
            return None
        return {
            **(info or {}),
            "airline_icao": airline,
            "airline_name": self.airlines.get(airline) if airline else None,
            "in_registry": info is not None,
        }

    def _registry(self, icao24: str) -> dict[str, Any] | None:
        if icao24 in self._registry_cache:
            return self._registry_cache[icao24]
        path = self.root / "dim_aircraft_registry.parquet"
        info: dict[str, Any] | None = None
        if path.exists():
            try:
                rows = (
                    duckdb.connect(":memory:")
                    .execute(
                        f"select {', '.join(REGISTRY_COLUMNS)} "
                        f"from read_parquet('{path}') where icao24 = ?",
                        [icao24],
                    )
                    .fetchall()
                )
            except duckdb.Error as exc:  # file being rewritten by dbt: retry next time
                log.warning("registry_lookup_failed", icao24=icao24, error=str(exc))
                return None
            if rows:
                info = dict(zip(REGISTRY_COLUMNS, rows[0], strict=True))
        if len(self._registry_cache) >= REGISTRY_CACHE_SIZE:
            self._registry_cache.clear()
        self._registry_cache[icao24] = info
        return info

    def flight(
        self, state: StateVector, published: FlightRoute | None = None
    ) -> dict[str, Any] | None:
        """Departure, destination, times and progress of the current flight.

        Sources, most trusted first:

        * departure: the in-progress reconstructed flight (observed take-off);
        * the published route of the callsign (adsbdb), when it is consistent with the
          live position and track (:func:`check_route`) and with the observed departure;
        * the usual destination of the callsign in our own history (``fct_callsign_routes``).
        """
        self.refresh()
        current = self.current_flights.get(state.icao24)
        departure: dict[str, Any] | None = None
        arrival: dict[str, Any] | None = None
        departure_source = arrival_source = None
        history: Route | None = None
        rejected = None

        if current is not None:
            departure, departure_source = self._airport(current[0]), "reconstructed"
        if published is not None:
            consistent = check_route(
                published, state.latitude, state.longitude, state.true_track, state.on_ground
            )
            if consistent and current is not None and current[0] != published.origin.icao:
                consistent = False  # observed take-off elsewhere: stale or reused callsign
            if consistent:
                if departure is None:
                    departure, departure_source = _route_airport(published.origin), "route_db"
                arrival, arrival_source = _route_airport(published.destination), "route_db"
            else:
                rejected = f"{published.origin.icao} → {published.destination.icao}"
        if arrival is None and state.callsign:
            if current is not None:
                history = self.routes.get((state.callsign, current[0]))
            history = history or self.dominant_routes.get(state.callsign)
            if history is not None:
                arrival, arrival_source = self._airport(history.arrival), "history"
                if departure is None:
                    departure, departure_source = self._airport(history.departure), "history"
        if departure is None and arrival is None and rejected is None:
            return None

        position_time = float(state.time_position or state.last_contact)
        flown = remaining = total = progress = None
        if departure and departure.get("latitude") is not None:
            flown = haversine_km(
                departure["latitude"], departure["longitude"], state.latitude, state.longitude
            )
        if arrival and arrival.get("latitude") is not None:
            remaining = haversine_km(
                state.latitude, state.longitude, arrival["latitude"], arrival["longitude"]
            )
        if flown is not None and remaining is not None:
            total = haversine_km(
                departure["latitude"],  # type: ignore[index]
                departure["longitude"],  # type: ignore[index]
                arrival["latitude"],  # type: ignore[index]
                arrival["longitude"],  # type: ignore[index]
            )
            progress = min(1.0, flown / max(flown + remaining, 1.0))

        departure_time: float | None = current[1] if current else None
        estimated = False
        if departure_time is None and flown is not None and not state.on_ground:
            # No observed take-off: distance flown at a typical average ground speed
            # (climb and acceleration make it lower than the current cruise speed).
            average = max(0.8 * (state.velocity or 0.0), 120.0)
            departure_time = position_time - flown * 1000 / average
            estimated = True

        return {
            "departure": departure,
            "arrival": arrival,
            "departure_source": departure_source,
            "arrival_source": arrival_source,
            "departure_time": round(departure_time) if departure_time else None,
            "departure_time_estimated": estimated,
            "elapsed_s": round(position_time - departure_time) if departure_time else None,
            "route_km": round(total) if total is not None else None,
            "flown_km": round(flown) if flown is not None else None,
            "remaining_km": round(remaining) if remaining is not None else None,
            "progress": round(progress, 3) if progress is not None else None,
            "route_flights": history.flights if history else None,
            "route_share": history.share if history else None,
            "rejected_route": rejected,
        }

    def _airport(self, icao: str | None) -> dict[str, Any] | None:
        if not icao:
            return None
        airport = self.airports.get(icao)
        return {
            "icao": icao,
            "iata": airport.iata if airport else None,
            "name": airport.name if airport else None,
            "city": airport.city if airport else None,
            "country": airport.country if airport else None,
            "latitude": airport.latitude if airport else None,
            "longitude": airport.longitude if airport else None,
        }

    def eta(self, state: StateVector, flight: dict[str, Any] | None) -> dict[str, Any] | None:
        arrival = (flight or {}).get("arrival")
        if not arrival or arrival.get("latitude") is None or state.on_ground:
            return None
        airport = Airport(
            arrival["icao"], arrival.get("name") or "", arrival["latitude"], arrival["longitude"]
        )
        velocity = state.velocity or 0.0
        if velocity < 30:
            return None
        remaining = haversine_km(
            state.latitude, state.longitude, airport.latitude, airport.longitude
        )
        naive = remaining * 1000 / max(velocity, 30.0)
        position_time = state.time_position or state.last_contact
        seconds, method = naive, "naive"
        # An estimated take-off time would feed a made-up elapsed time to the model.
        departure_time = (
            None
            if (flight or {}).get("departure_time_estimated")
            else ((flight or {}).get("departure_time"))
        )
        if self.model is not None:
            seconds, method = (
                self._predict(state, airport, remaining, naive, departure_time),
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

    def _tailwind(self, state: StateVector, position_time: float) -> float:
        cell = wind_cell(state.latitude, state.longitude)
        wind = self.winds.get((*cell, int(position_time // 3600 * 3600)))
        if wind is None:
            return np.nan
        return tailwind_ms(wind[0], wind[1], state.true_track or 0.0)

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
            "tailwind_ms": self._tailwind(state, position_time),
            "arrival_airport_code": self.model.airport_codes.get(airport.icao, -1),
        }
        row = np.array([[values[f] for f in self.model.features]], dtype=float)
        ratio = float(np.clip(self.model.booster.predict(row)[0], *self.model.ratio_clip))
        return naive * ratio
