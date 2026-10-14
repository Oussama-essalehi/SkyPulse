"""Pure, unit-testable anomaly rules evaluated on one aircraft's recent track.

Each rule returns a :class:`Finding` (condition currently true) or ``None``.
The detector turns findings into alert lifecycle events (active -> resolved).
"""

from __future__ import annotations

import itertools
import math
from collections import Counter
from collections.abc import Iterable, Sequence
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
    # Cruise only: below ~FL250 an aircraft is often descending towards an airport,
    # where low-altitude receiver coverage is patchy.
    signal_loss_min_altitude_m: float = 7_500.0
    signal_loss_min_vertical_rate_ms: float = -5.0  # descending aircraft may simply land
    signal_loss_min_sightings: int = 2
    # Missing from this many consecutive snapshots: ~1 in 5 aircraft absent from one
    # snapshot is back in the next one (measured on real OpenSky data).
    signal_loss_confirm_snapshots: int = 2
    signal_loss_cell_deg: float = 2.0
    signal_loss_min_neighbours: int = 5
    # Beyond this, the projected position is too uncertain (and the aircraft may well
    # have landed): with anonymous polling (16 min) the rule is effectively disabled.
    signal_loss_max_extrapolation_s: float = 600.0


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


def coverage_cell(lat: float, lon: float, size_deg: float) -> tuple[int, int]:
    return (math.floor(lat / size_deg), math.floor(lon / size_deg))


def coverage_map(states: Iterable[StateVector], size_deg: float) -> Counter[tuple[int, int]]:
    """Number of aircraft received per grid cell in one snapshot (receiver coverage proxy)."""
    return Counter(coverage_cell(s.latitude, s.longitude, size_deg) for s in states)


def neighbourhood_count(
    cells: Counter[tuple[int, int]], lat: float, lon: float, size_deg: float
) -> int:
    y, x = coverage_cell(lat, lon, size_deg)
    return sum(cells.get((y + dy, x + dx), 0) for dy in (-1, 0, 1) for dx in (-1, 0, 1))


def project(state: StateVector, elapsed_s: float) -> tuple[float, float]:
    """Dead-reckoned position after ``elapsed_s`` seconds at constant speed and track."""
    if not state.velocity or state.true_track is None or elapsed_s <= 0:
        return state.latitude, state.longitude
    d = state.velocity * elapsed_s / EARTH_RADIUS_M
    b = math.radians(state.true_track)
    p1, l1 = math.radians(state.latitude), math.radians(state.longitude)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(
        math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2)
    )
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def signal_loss_rule(
    last: StateVector,
    sightings: int,
    snapshot_time: int,
    cells: Counter[tuple[int, int]],
    cfg: RuleConfig,
) -> Finding | None:
    """A cruising aircraft disappeared although the area it should now be flying over is
    still covered by receivers (other aircraft there are still received).

    ADS-B coverage has holes (oceans, deserts): an aircraft leaving coverage is normal.
    Projecting the last fix to the snapshot time and requiring live neighbours around
    the projected position keeps those cases out.
    """
    alt = last.altitude
    if last.on_ground or alt is None or alt < cfg.signal_loss_min_altitude_m:
        return None
    if (last.vertical_rate or 0.0) < cfg.signal_loss_min_vertical_rate_ms:
        return None
    if sightings < cfg.signal_loss_min_sightings:
        return None
    elapsed = snapshot_time - _t(last)
    if elapsed <= 0 or elapsed > cfg.signal_loss_max_extrapolation_s:
        return None
    lat, lon = project(last, elapsed)
    neighbours = neighbourhood_count(cells, lat, lon, cfg.signal_loss_cell_deg)
    if neighbours < cfg.signal_loss_min_neighbours:
        return None
    return Finding(
        AlertType.SIGNAL_LOSS,
        Severity.WARNING,
        f"Perte de signal en vol : plus de position depuis {round(elapsed / 60)} min "
        f"à {round(alt)} m, dans une zone couverte ({neighbours} avions reçus autour)",
        {
            "silent_for_s": elapsed,
            "last_altitude_m": alt,
            "neighbours": neighbours,
            "expected_latitude": round(lat, 4),
            "expected_longitude": round(lon, 4),
        },
    )
