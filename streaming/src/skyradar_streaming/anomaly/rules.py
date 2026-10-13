"""Pure, unit-testable anomaly rules evaluated on one aircraft's recent track.

Each rule returns a :class:`Finding` (condition currently true) or ``None``.
The detector turns findings into alert lifecycle events (active -> resolved).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from skyradar_common.models import AlertType, Severity, StateVector

EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class RuleConfig:
    rapid_descent_rate_ms: float = -25.0  # ~ -4900 ft/min
    rapid_descent_min_altitude_m: float = 2_500.0
    holding_window_s: int = 900
    holding_min_turn_deg: float = 540.0  # 1.5 full turns
    holding_max_sample_gap_s: int = 45  # beyond this, heading deltas alias
    holding_min_points: int = 6
    confinement_min_duration_s: int = 480
    confinement_max_radius_m: float = 15_000.0
    confinement_min_path_ratio: float = 3.0
    min_airborne_altitude_m: float = 300.0


@dataclass(frozen=True)
class Finding:
    type: AlertType
    severity: Severity
    message: str
    details: dict[str, Any] = field(default_factory=dict)


SQUAWK_RULES: dict[str, tuple[AlertType, Severity, str]] = {
    "7500": (
        AlertType.SQUAWK_HIJACK,
        Severity.CRITICAL,
        "Squawk 7500 : intervention illicite (détournement) déclarée",
    ),
    "7600": (
        AlertType.SQUAWK_RADIO_FAILURE,
        Severity.WARNING,
        "Squawk 7600 : panne radio déclarée",
    ),
    "7700": (
        AlertType.SQUAWK_EMERGENCY,
        Severity.CRITICAL,
        "Squawk 7700 : situation d'urgence générale déclarée",
    ),
}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def heading_delta(a: float, b: float) -> float:
    """Signed smallest rotation from heading a to heading b, in [-180, 180)."""
    return (b - a + 180.0) % 360.0 - 180.0


def _t(state: StateVector) -> int:
    return state.time_position or state.last_contact


def squawk_rule(state: StateVector) -> Finding | None:
    if state.squawk in SQUAWK_RULES:
        alert_type, severity, message = SQUAWK_RULES[state.squawk]
        return Finding(alert_type, severity, message, {"squawk": state.squawk})
    return None


def rapid_descent_rule(state: StateVector, cfg: RuleConfig) -> Finding | None:
    alt, vrate = state.altitude, state.vertical_rate
    if state.on_ground or alt is None or vrate is None:
        return None
    if vrate <= cfg.rapid_descent_rate_ms and alt >= cfg.rapid_descent_min_altitude_m:
        fpm = round(vrate * 196.85)
        return Finding(
            AlertType.RAPID_DESCENT,
            Severity.WARNING,
            f"Descente rapide : {fpm} ft/min à {round(alt)} m d'altitude",
            {"vertical_rate_ms": vrate, "altitude_m": alt},
        )
    return None


def _airborne_window(
    track: Sequence[StateVector], cfg: RuleConfig, window_s: int
) -> list[StateVector]:
    if not track:
        return []
    end = _t(track[-1])
    return [
        s
        for s in track
        if end - _t(s) <= window_s
        and not s.on_ground
        and (s.altitude or 0) >= cfg.min_airborne_altitude_m
    ]


def holding_rule(track: Sequence[StateVector], cfg: RuleConfig) -> Finding | None:
    """Circling / holding detection on a sliding window.

    1. Cumulative heading change: sum of |delta track| over the window. Only valid
       when samples are dense enough (a 3 deg/s turn sampled every 96 s aliases).
    2. Confinement (sampling-robust complement): the aircraft flew a path several
       times longer than the radius of the area it stayed in.
    """
    window = _airborne_window(track, cfg, cfg.holding_window_s)
    if len(window) < cfg.holding_min_points:
        return None
    if track and window[-1] is not track[-1]:
        return None  # currently on the ground / too low

    dense = all(
        _t(b) - _t(a) <= cfg.holding_max_sample_gap_s for a, b in itertools.pairwise(window)
    )
    if dense:
        turn = 0.0
        for a, b in itertools.pairwise(window):
            if a.true_track is not None and b.true_track is not None:
                turn += abs(heading_delta(a.true_track, b.true_track))
        if turn >= cfg.holding_min_turn_deg:
            return Finding(
                AlertType.HOLDING_PATTERN,
                Severity.INFO,
                f"Attente / tourne en rond : {round(turn)}° de virage cumulé en "
                f"{round((_t(window[-1]) - _t(window[0])) / 60)} min",
                {"cumulative_turn_deg": round(turn), "method": "heading"},
            )

    duration = _t(window[-1]) - _t(window[0])
    if duration < cfg.confinement_min_duration_s:
        return None
    lat_c = sum(s.latitude for s in window) / len(window)
    lon_c = sum(s.longitude for s in window) / len(window)
    radius = max(haversine_m(lat_c, lon_c, s.latitude, s.longitude) for s in window)
    speeds = [s.velocity for s in window if s.velocity]
    if not speeds:
        return None
    path = (sum(speeds) / len(speeds)) * duration
    if (
        radius <= cfg.confinement_max_radius_m
        and path >= cfg.confinement_min_path_ratio * 2 * radius
    ):
        return Finding(
            AlertType.HOLDING_PATTERN,
            Severity.INFO,
            f"Attente / tourne en rond : {round(duration / 60)} min dans un rayon de "
            f"{round(radius / 1000, 1)} km",
            {"radius_km": round(radius / 1000, 1), "duration_s": duration, "method": "confinement"},
        )
    return None


def evaluate(track: Sequence[StateVector], cfg: RuleConfig) -> list[Finding]:
    """Run every rule on an aircraft track (oldest first, latest state last)."""
    if not track:
        return []
    latest = track[-1]
    findings = [squawk_rule(latest), rapid_descent_rule(latest, cfg), holding_rule(track, cfg)]
    return [f for f in findings if f is not None]
