"""Airport view: live arrivals / departures board and the last 24 hours.

ADS-B says where an aircraft is, not where it goes, so the board combines, for every live
aircraft that may concern the airport:

* its route (:meth:`Enrichment.flight`: observed take-off, published route of the
  callsign checked against the position, usual destination in the history);
* for aircraft near the airport with no known route, the geometry: low, close and
  descending towards the runway is an approach, climbing away is a departure.

Published routes are looked up (adsbdb) only for aircraft within ``NEAR_RADIUS_KM``,
nearest first, a bounded number per request and within a time budget; the cache fills
up as the board refreshes. Routes already in the cache are used for every aircraft,
wherever it is. The last 24 hours come from the reconstructed flights (``fct_flights``).
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from dataclasses import dataclass, field
from typing import Any

import duckdb
import structlog

from skyradar_api.enrichment import Airport, Enrichment, bearing_deg, haversine_km
from skyradar_api.routes import RouteLookup
from skyradar_api.state import LiveState
from skyradar_common.models import StateVector

log = structlog.get_logger(__name__)

GROUND_RADIUS_KM = 6.0  # on the ground this close: at the airport
NEAR_RADIUS_KM = 150.0  # route lookups are worth it for airborne aircraft this close
TERMINAL_RADIUS_KM = 60.0  # approach / initial climb...
TERMINAL_ALTITUDE_M = 3500.0  # ...below this altitude
MAX_LOOKUPS = 40  # new adsbdb lookups per board request
LOOKUP_BUDGET_S = 4.0
MAX_ROWS = 40
HISTORY_TTL_S = 60.0
KIND_CODES = {"large_airport": "L", "medium_airport": "M", "small_airport": "S"}


def airport_dict(airport: Airport) -> dict[str, Any]:
    return {
        "icao": airport.icao,
        "iata": airport.iata,
        "name": airport.name,
        "city": airport.city,
        "country": airport.country,
        "latitude": airport.latitude,
        "longitude": airport.longitude,
        "kind": KIND_CODES.get(airport.kind, "M"),
    }


def terminal_phase(state: StateVector, airport: Airport, distance_km: float) -> str | None:
    """``approach`` / ``climb`` from geometry alone, or None when it says nothing."""
    altitude = state.altitude
    if state.on_ground or distance_km > TERMINAL_RADIUS_KM:
        return None
    if altitude is None or altitude > TERMINAL_ALTITUDE_M:
        return None
    rate = state.vertical_rate or 0.0
    to_airport = bearing_deg(state.latitude, state.longitude, airport.latitude, airport.longitude)
    towards = math.cos(math.radians((state.true_track or 0.0) - to_airport)) > 0.5
    if rate < -1.5 and towards:
        return "approach"
    if rate > 1.5 and not towards:
        return "climb"
    return None


@dataclass
class AirportBoard:
    state: LiveState
    enrichment: Enrichment
    routes: RouteLookup
    _history: dict[str, tuple[float, dict[str, Any] | None]] = field(default_factory=dict)

    # ------------------------------------------------------------------ list
    def airports(self) -> dict[str, list[Any]]:
        """All airports, column-oriented (a few thousand rows for the map layer)."""
        self.enrichment.refresh()
        rows = sorted(self.enrichment.airports.values(), key=lambda a: a.icao)
        return {
            "icao": [a.icao for a in rows],
            "iata": [a.iata for a in rows],
            "name": [a.name for a in rows],
            "city": [a.city for a in rows],
            "country": [a.country for a in rows],
            "lat": [round(a.latitude, 4) for a in rows],
            "lon": [round(a.longitude, 4) for a in rows],
            "kind": [KIND_CODES.get(a.kind, "M") for a in rows],
        }

    # ------------------------------------------------------------------ board
    async def board(self, icao: str, limit: int = MAX_ROWS) -> dict[str, Any] | None:
        self.enrichment.refresh()
        airport = self.enrichment.airports.get(icao.upper())
        if airport is None:
            return None
        candidates = self._candidates(airport)
        pending = await self._lookup_routes(candidates)
        arrivals: list[dict[str, Any]] = []
        departures: list[dict[str, Any]] = []
        ground: list[dict[str, Any]] = []
        for state, distance in candidates:
            self._classify(state, distance, airport, arrivals, departures, ground)

        arrivals.sort(key=lambda r: (r["eta"] is None, (r["eta"] or {}).get("arrival_estimate", 0)))
        departures.sort(key=lambda r: (r["departure_time"] is None, -(r["departure_time"] or 0)))
        ground.sort(key=lambda r: (r["callsign"] is None, r["callsign"] or ""))
        history = await asyncio.to_thread(self.history, airport.icao)
        return {
            "airport": airport_dict(airport),
            "generated_at": time.time(),
            "counts": {
                "arrivals": len(arrivals),
                "departures": len(departures),
                "ground": len(ground),
            },
            "arrivals": arrivals[:limit],
            "departures": departures[:limit],
            "ground": ground[:limit],
            "pending_lookups": pending,
            "route_lookup": self.routes.enabled,
            "history": history,
        }

    def _candidates(self, airport: Airport) -> list[tuple[StateVector, float]]:
        departed = self.enrichment.departures_index.get(airport.icao, set())
        usual_arrivals = self.enrichment.arrivals_index.get(airport.icao, set())
        # The map keeps aircraft missing from a snapshot for a while (coverage gaps); the
        # board only lists those of the latest snapshot, or landed flights linger on it.
        meta = self.state.last_meta
        oldest = meta.snapshot_time - 0.5 * meta.poll_interval_seconds if meta else 0.0
        found = []
        for entry in self.state.aircraft.values():
            if entry.seen_snapshot < oldest:
                continue
            state = entry.state
            distance = haversine_km(
                state.latitude, state.longitude, airport.latitude, airport.longitude
            )
            if state.on_ground:
                if distance <= GROUND_RADIUS_KM:
                    found.append((state, distance))
                continue
            published = self.routes.cached(state.callsign)
            linked = (
                distance <= NEAR_RADIUS_KM
                or state.icao24 in departed
                or (state.callsign is not None and state.callsign in usual_arrivals)
                or (
                    published is not None
                    and airport.icao in (published.origin.icao, published.destination.icao)
                )
            )
            if linked:
                found.append((state, distance))
        return found

    async def _lookup_routes(self, candidates: list[tuple[StateVector, float]]) -> int:
        """Look up the published routes of the nearest candidates; return how many remain."""
        if not self.routes.enabled:
            return 0
        missing = sorted(
            (
                (distance, state.callsign)
                for state, distance in candidates
                if state.callsign
                and distance <= NEAR_RADIUS_KM
                and not self.routes.is_cached(state.callsign)
            ),
        )
        callsigns = list(dict.fromkeys(c for _, c in missing))
        batch = callsigns[:MAX_LOOKUPS]
        if batch:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(*(self.routes.route(c) for c in batch)), LOOKUP_BUDGET_S
                )
        return sum(1 for c in callsigns if not self.routes.is_cached(c))

    def _classify(
        self,
        state: StateVector,
        distance: float,
        airport: Airport,
        arrivals: list[dict[str, Any]],
        departures: list[dict[str, Any]],
        ground: list[dict[str, Any]],
    ) -> None:
        flight = self.enrichment.flight(state, self.routes.cached(state.callsign)) or {}
        departure, arrival = flight.get("departure"), flight.get("arrival")
        departure_icao = departure["icao"] if departure else None
        arrival_icao = arrival["icao"] if arrival else None
        row: dict[str, Any] = {
            "icao24": state.icao24,
            "callsign": state.callsign,
            "airline": self.enrichment.airline_name(state.callsign),
            "altitude": state.altitude,
            "velocity": state.velocity,
            "vertical_rate": state.vertical_rate,
            "distance_km": round(distance, 1),
        }
        if state.on_ground:
            status = (
                "arrived"
                if arrival_icao == airport.icao
                else "departing"
                if departure_icao == airport.icao
                else "parked"
            )
            other = departure if status == "arrived" else arrival if status == "departing" else None
            ground.append({**row, "status": status, "airport": other})
            return
        phase = terminal_phase(state, airport, distance)
        # With a known route, low and close is enough (level segments of an approach).
        terminal = distance <= TERMINAL_RADIUS_KM and (state.altitude or 0) <= TERMINAL_ALTITUDE_M
        if arrival_icao == airport.icao or (phase == "approach" and arrival_icao is None):
            eta = self.enrichment.eta(state, flight) if arrival_icao else None
            if eta is None and (state.velocity or 0) > 30:
                seconds = distance * 1000 / (state.velocity or 1)
                position_time = state.time_position or state.last_contact
                eta = {"seconds": round(seconds), "arrival_estimate": position_time + seconds}
            arrivals.append(
                {
                    **row,
                    "airport": departure if departure_icao != airport.icao else None,
                    "source": flight.get("arrival_source") if arrival_icao else "geometry",
                    "status": "approach" if phase == "approach" or terminal else "en_route",
                    "eta": {
                        "seconds": eta["seconds"],
                        "arrival_estimate": round(eta["arrival_estimate"]),
                        "method": eta.get("method", "naive"),
                    }
                    if eta
                    else None,
                }
            )
        elif departure_icao == airport.icao or (phase == "climb" and departure_icao is None):
            departures.append(
                {
                    **row,
                    "airport": arrival if arrival_icao != airport.icao else None,
                    "source": flight.get("departure_source") if departure_icao else "geometry",
                    "status": "climb" if phase == "climb" or terminal else "en_route",
                    "departure_time": flight.get("departure_time"),
                    "departure_time_estimated": bool(flight.get("departure_time_estimated")),
                }
            )

    # ------------------------------------------------------------------ history
    def history(self, icao: str) -> dict[str, Any] | None:
        """Reconstructed movements of the last 24 hours (``fct_flights``), cached 60 s."""
        cached = self._history.get(icao)
        if cached and time.monotonic() - cached[0] < HISTORY_TTL_S:
            return cached[1]
        path = self.enrichment.root / "fct_flights.parquet"
        result = None
        if path.exists():
            try:
                result = self._query_history(path, icao)
            except duckdb.Error as exc:  # file being rewritten by dbt: next refresh
                log.warning("airport_history_failed", airport=icao, error=str(exc))
        if len(self._history) > 500:
            self._history.clear()
        self._history[icao] = (time.monotonic(), result)
        return result

    def _query_history(self, path: Any, icao: str) -> dict[str, Any]:
        con = duckdb.connect(":memory:")
        con.execute(
            f"""
            create temp table movements as
            select 'departure' as direction, callsign, airline_name, aircraft_typecode,
                   arrival_airport as other_icao, arrival_airport_name as other_name,
                   epoch(departure_time) as moved_at, duration_min
            from read_parquet('{path}')
            where departure_airport = $icao
              and departure_time >= now()::timestamp - interval 24 hour
            union all
            select 'arrival', callsign, airline_name, aircraft_typecode,
                   departure_airport, departure_airport_name,
                   epoch(arrival_time), duration_min
            from read_parquet('{path}')
            where arrival_airport = $icao
              and arrival_time >= now()::timestamp - interval 24 hour
            """,
            {"icao": icao},
        )
        counts = dict(
            con.execute("select direction, count(*) from movements group by 1").fetchall()
        )
        recent = con.execute(
            "select direction, callsign, airline_name, aircraft_typecode, other_icao, "
            "other_name, moved_at, duration_min from movements order by moved_at desc limit 20"
        ).fetchall()
        top = con.execute(
            "select other_icao, any_value(other_name), count(*) as n from movements "
            "where other_icao is not null group by 1 order by n desc, 1 limit 5"
        ).fetchall()
        names = (
            "direction",
            "callsign",
            "airline",
            "typecode",
            "airport_icao",
            "airport_name",
            "time",
            "duration_min",
        )
        return {
            "departures_24h": int(counts.get("departure", 0)),
            "arrivals_24h": int(counts.get("arrival", 0)),
            "recent": [dict(zip(names, row, strict=True)) for row in recent],
            "top_airports": [{"icao": i, "name": n, "flights": int(c)} for i, n, c in top],
        }
