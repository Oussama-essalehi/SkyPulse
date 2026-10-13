"""In-memory live state: latest position + short trail per aircraft, active alerts.

All mutations happen on the event loop (single writer), so no locking is needed.
The broadcaster reads the "dirty" set to build compact column-oriented deltas.
"""

from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any

from skyradar_common.models import Alert, AlertStatus, SnapshotMeta, StateVector

# Column order of the WebSocket payload (documented in docs/websocket-protocol.md).
COLUMNS = (
    "icao24",
    "callsign",
    "country",
    "lon",
    "lat",
    "alt",
    "velocity",
    "track",
    "vrate",
    "on_ground",
    "squawk",
    "time_position",
    "category",
)

ALTITUDE_BANDS: tuple[tuple[str, float, float], ...] = (
    ("0-3000 m", 0, 3_000),
    ("3000-6000 m", 3_000, 6_000),
    ("6000-9000 m", 6_000, 9_000),
    ("9000-12000 m", 9_000, 12_000),
    ("> 12000 m", 12_000, float("inf")),
)


@dataclass
class AircraftEntry:
    state: StateVector
    trail: deque[tuple[float, float, float | None, int]]
    seen_snapshot: int


@dataclass
class LiveState:
    trail_length: int = 120
    aircraft_ttl_seconds: float = 300
    alert_ttl_seconds: float = 900
    aircraft: dict[str, AircraftEntry] = field(default_factory=dict)
    alerts: dict[str, Alert] = field(default_factory=dict)
    last_meta: SnapshotMeta | None = None
    dirty: set[str] = field(default_factory=set)
    removed: set[str] = field(default_factory=set)
    alerts_dirty: bool = False
    messages_consumed: int = 0

    # -- mutations -----------------------------------------------------------------
    def apply_state(self, state: StateVector) -> None:
        self.messages_consumed += 1
        entry = self.aircraft.get(state.icao24)
        point = (
            state.longitude,
            state.latitude,
            state.altitude,
            state.time_position or state.last_contact,
        )
        if entry is None:
            entry = AircraftEntry(
                state, deque([point], maxlen=self.trail_length), state.snapshot_time
            )
            self.aircraft[state.icao24] = entry
        else:
            if state.snapshot_time < entry.seen_snapshot:
                return  # out-of-order replay of an older snapshot
            if point[3] != entry.trail[-1][3]:
                entry.trail.append(point)
            entry.state = state
            entry.seen_snapshot = state.snapshot_time
        self.dirty.add(state.icao24)
        self.removed.discard(state.icao24)

    def apply_snapshot(self, meta: SnapshotMeta) -> None:
        self.last_meta = meta
        ttl = max(self.aircraft_ttl_seconds, 2.5 * meta.poll_interval_seconds)
        cutoff = meta.snapshot_time - ttl
        stale = [k for k, v in self.aircraft.items() if v.seen_snapshot < cutoff]
        for icao24 in stale:
            del self.aircraft[icao24]
            self.dirty.discard(icao24)
            self.removed.add(icao24)
        self.expire_alerts(now=meta.ingested_at)

    def apply_alert(self, alert: Alert) -> None:
        if alert.status == AlertStatus.RESOLVED:
            if self.alerts.pop(alert.alert_id, None) is not None:
                self.alerts_dirty = True
            return
        self.alerts[alert.alert_id] = alert
        self.alerts_dirty = True

    def expire_alerts(self, now: float | None = None) -> None:
        now = now or time.time()
        expired = [
            k for k, a in self.alerts.items() if now - a.detected_at > self.alert_ttl_seconds
        ]
        for key in expired:
            del self.alerts[key]
        if expired:
            self.alerts_dirty = True

    # -- views ---------------------------------------------------------------------
    def columns(self, keys: list[str] | None = None) -> dict[str, list[Any]]:
        """Column-oriented encoding: ~3x smaller than a list of objects for 10k aircraft."""
        keys = list(self.aircraft) if keys is None else keys
        cols: dict[str, list[Any]] = {c: [] for c in COLUMNS}
        for key in keys:
            entry = self.aircraft.get(key)
            if entry is None:
                continue
            s = entry.state
            alt = s.altitude
            cols["icao24"].append(s.icao24)
            cols["callsign"].append(s.callsign)
            cols["country"].append(s.origin_country)
            cols["lon"].append(round(s.longitude, 5))
            cols["lat"].append(round(s.latitude, 5))
            cols["alt"].append(None if alt is None else round(alt))
            cols["velocity"].append(None if s.velocity is None else round(s.velocity, 1))
            cols["track"].append(None if s.true_track is None else round(s.true_track, 1))
            cols["vrate"].append(None if s.vertical_rate is None else round(s.vertical_rate, 1))
            cols["on_ground"].append(1 if s.on_ground else 0)
            cols["squawk"].append(s.squawk)
            cols["time_position"].append(s.time_position or s.last_contact)
            cols["category"].append(s.category)
        return cols

    def full_message(self) -> dict[str, Any]:
        return {
            "type": "snapshot",
            **self._header(),
            "upserts": self.columns(),
            "removed": [],
        }

    def take_delta(self) -> dict[str, Any] | None:
        if not self.dirty and not self.removed:
            return None
        message = {
            "type": "delta",
            **self._header(),
            "upserts": self.columns(sorted(self.dirty)),
            "removed": sorted(self.removed),
        }
        self.dirty.clear()
        self.removed.clear()
        return message

    def alerts_message(self) -> dict[str, Any]:
        return {
            "type": "alerts",
            "alerts": [a.model_dump(mode="json") for a in self.active_alerts()],
        }

    def active_alerts(self) -> list[Alert]:
        order = {"critical": 0, "warning": 1, "info": 2}
        return sorted(self.alerts.values(), key=lambda a: (order[a.severity.value], -a.detected_at))

    def _header(self) -> dict[str, Any]:
        meta = self.last_meta
        return {
            "server_time": time.time(),
            "data_time": meta.snapshot_time if meta else None,
            "source": meta.source if meta else None,
            "poll_interval": meta.poll_interval_seconds if meta else None,
            "count": len(self.aircraft),
        }

    def stats(self) -> dict[str, Any]:
        airborne = [e.state for e in self.aircraft.values() if not e.state.on_ground]
        countries = Counter(e.state.origin_country for e in self.aircraft.values())
        bands = dict.fromkeys((b[0] for b in ALTITUDE_BANDS), 0)
        for s in airborne:
            alt = s.altitude
            if alt is None:
                continue
            for name, low, high in ALTITUDE_BANDS:
                if low <= alt < high:
                    bands[name] += 1
                    break
        meta = self.last_meta
        return {
            "aircraft_total": len(self.aircraft),
            "airborne": len(airborne),
            "on_ground": len(self.aircraft) - len(airborne),
            "by_origin_country": [{"country": c, "count": n} for c, n in countries.most_common(25)],
            "by_altitude_band": [{"band": b, "count": n} for b, n in bands.items()],
            "active_alerts": len(self.alerts),
            "data_time": meta.snapshot_time if meta else None,
            "source": meta.source if meta else None,
            "poll_interval_seconds": meta.poll_interval_seconds if meta else None,
            "credits_remaining": meta.credits_remaining if meta else None,
        }

    def aircraft_detail(self, icao24: str) -> dict[str, Any] | None:
        entry = self.aircraft.get(icao24.lower())
        if entry is None:
            return None
        alerts = [
            a.model_dump(mode="json")
            for a in self.alerts.values()
            if a.icao24 == entry.state.icao24
        ]
        return {
            "state": entry.state.model_dump(mode="json"),
            "trail": [list(p) for p in entry.trail],
            "alerts": alerts,
        }

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        q = query.strip().upper()
        if not q:
            return []
        hits: list[dict[str, Any]] = []
        for entry in self.aircraft.values():
            s = entry.state
            if (s.callsign and s.callsign.upper().startswith(q)) or s.icao24.upper().startswith(q):
                hits.append(
                    {
                        "icao24": s.icao24,
                        "callsign": s.callsign,
                        "origin_country": s.origin_country,
                        "longitude": s.longitude,
                        "latitude": s.latitude,
                    }
                )
                if len(hits) >= limit:
                    break
        return hits
