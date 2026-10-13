"""Deterministic synthetic traffic generator.

Produces a replay file with the exact lake schema: great-circle flights between
major airports with climb / cruise / descent profiles, aircraft parked on the
ground, plus scripted anomaly scenarios (squawk 7700 / 7600, holding pattern,
rapid descent) so that the whole pipeline can be demonstrated and tested
without any network access. Real recordings (``make record``) are preferred
for demos; this is the offline fallback and the CI fixture.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq

from skyradar_common.models import StateVector
from skyradar_common.parquet import states_to_table

EARTH_RADIUS_M = 6_371_000.0

# (ICAO, latitude, longitude)
AIRPORTS: list[tuple[str, float, float]] = [
    ("LFPG", 49.0097, 2.5479),
    ("EGLL", 51.4700, -0.4543),
    ("EDDF", 50.0379, 8.5622),
    ("EHAM", 52.3105, 4.7683),
    ("LEMD", 40.4983, -3.5676),
    ("LIRF", 41.8003, 12.2389),
    ("LSZH", 47.4647, 8.5492),
    ("LOWW", 48.1103, 16.5697),
    ("EKCH", 55.6180, 12.6508),
    ("ESSA", 59.6519, 17.9186),
    ("LTFM", 41.2753, 28.7519),
    ("OMDB", 25.2532, 55.3657),
    ("OTHH", 25.2731, 51.6081),
    ("KJFK", 40.6413, -73.7781),
    ("KLAX", 33.9416, -118.4085),
    ("KORD", 41.9742, -87.9073),
    ("KATL", 33.6407, -84.4277),
    ("KDFW", 32.8998, -97.0403),
    ("KSFO", 37.6213, -122.3790),
    ("KSEA", 47.4502, -122.3088),
    ("CYYZ", 43.6777, -79.6248),
    ("MMMX", 19.4361, -99.0719),
    ("SBGR", -23.4356, -46.4731),
    ("SAEZ", -34.8222, -58.5358),
    ("RJTT", 35.5494, 139.7798),
    ("RKSI", 37.4602, 126.4407),
    ("ZBAA", 40.0799, 116.6031),
    ("VHHH", 22.3080, 113.9185),
    ("WSSS", 1.3644, 103.9915),
    ("VTBS", 13.6900, 100.7501),
    ("VIDP", 28.5562, 77.1000),
    ("YSSY", -33.9399, 151.1753),
    ("FAOR", -26.1392, 28.2460),
    ("HECA", 30.1219, 31.4056),
    ("GMMN", 33.3675, -7.5898),
    ("LFMN", 43.6584, 7.2159),
    ("LFLL", 45.7256, 5.0811),
    ("LFBO", 43.6291, 1.3638),
    ("LEBL", 41.2974, 2.0833),
    ("LPPT", 38.7742, -9.1342),
]

AIRLINES: list[tuple[str, str]] = [
    ("AFR", "France"),
    ("BAW", "United Kingdom"),
    ("DLH", "Germany"),
    ("KLM", "Kingdom of the Netherlands"),
    ("IBE", "Spain"),
    ("UAE", "United Arab Emirates"),
    ("QTR", "Qatar"),
    ("DAL", "United States"),
    ("UAL", "United States"),
    ("AAL", "United States"),
    ("ACA", "Canada"),
    ("JAL", "Japan"),
    ("SIA", "Singapore"),
    ("CPA", "China"),
    ("EZY", "United Kingdom"),
    ("RYR", "Ireland"),
    ("THY", "Turkey"),
    ("SWR", "Switzerland"),
    ("RAM", "Morocco"),
    ("QFA", "Australia"),
]


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def destination_point(
    lat: float, lon: float, bearing: float, distance_m: float
) -> tuple[float, float]:
    d = distance_m / EARTH_RADIUS_M
    b = math.radians(bearing)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(
        math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2)
    )
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


@dataclass
class _Flight:
    icao24: str
    callsign: str
    country: str
    origin: tuple[str, float, float]
    dest: tuple[str, float, float]
    distance: float
    cruise_alt: float
    cruise_speed: float
    start_offset: float  # seconds; negative = already airborne at t0
    squawk: str
    scenario: str = "normal"

    def state_at(self, t: float) -> dict[str, float | bool | str | None] | None:
        """Kinematics at ``t`` seconds after the recording start (None = not flying)."""
        elapsed = t - self.start_offset
        if elapsed < 0:
            return None
        climb, descent = 150_000.0, 200_000.0
        # Distance flown: slower in climb/descent phases.
        dist = elapsed * self.cruise_speed
        if dist >= self.distance:
            return None
        lat1, lon1 = self.origin[1], self.origin[2]
        lat2, lon2 = self.dest[1], self.dest[2]
        heading_to_dest = initial_bearing(lat1, lon1, lat2, lon2)
        lat, lon = destination_point(lat1, lon1, heading_to_dest, dist)
        track = initial_bearing(lat, lon, lat2, lon2)
        remaining = self.distance - dist
        if dist < climb:
            frac = dist / climb
            alt, vrate, speed = self.cruise_alt * frac, 12.0, self.cruise_speed * (0.6 + 0.4 * frac)
        elif remaining < descent:
            frac = remaining / descent
            alt, vrate, speed = (
                self.cruise_alt * frac,
                -9.0,
                self.cruise_speed * (0.55 + 0.45 * frac),
            )
        else:
            alt, vrate, speed = self.cruise_alt, 0.0, self.cruise_speed
        # Scripted scenarios are timed on the recording clock (t), not the flight's.
        squawk = self.squawk
        if self.scenario in {"emergency", "radio_failure"} and t > 300:
            squawk = "7700" if self.scenario == "emergency" else "7600"
            if self.scenario == "emergency":
                alt = max(3_000.0, alt - (t - 300) * 8.0)
                vrate = -8.0 if alt > 3_000 else 0.0
        if self.scenario == "rapid_descent" and t > 240:
            alt = max(3_000.0, self.cruise_alt - (t - 240) * 45.0)
            vrate = -45.0 if alt > 3_000 else 0.0
        return {
            "latitude": lat,
            "longitude": lon,
            "baro_altitude": round(alt, 1),
            "geo_altitude": round(alt + 120, 1),
            "velocity": round(speed, 1),
            "true_track": round(track, 1),
            "vertical_rate": vrate,
            "on_ground": False,
            "squawk": squawk,
        }


def _holding_state(
    center: tuple[float, float], t: float, alt: float
) -> dict[str, float | bool | str | None]:
    """Racetrack approximated by a circle (radius 7 km, ~3 deg/s standard rate turn)."""
    angle = (t * 3.0) % 360  # degrees around the circle
    lat, lon = destination_point(center[0], center[1], angle, 7_000)
    return {
        "latitude": lat,
        "longitude": lon,
        "baro_altitude": alt,
        "geo_altitude": alt + 100,
        "velocity": 110.0,
        "true_track": round((angle + 90) % 360, 1),
        "vertical_rate": 0.0,
        "on_ground": False,
        "squawk": "4521",
    }


def generate(
    output: str | Path,
    flights: int = 600,
    duration_s: int = 1800,
    interval_s: int = 10,
    seed: int = 42,
    start_time: int | None = None,
) -> Path:
    """Write a synthetic replay Parquet file and return its path."""
    rng = random.Random(seed)
    t0 = start_time or int(time.time()) - duration_s
    plan: list[_Flight] = []
    used_icao: set[str] = set()

    def new_icao() -> str:
        while True:
            code = f"{rng.randrange(0x100000, 0xFFFFFF):06x}"
            if code not in used_icao:
                used_icao.add(code)
                return code

    while len(plan) < flights:
        origin, dest = rng.sample(AIRPORTS, 2)
        distance = haversine_m(origin[1], origin[2], dest[1], dest[2])
        if not 300_000 <= distance <= 9_000_000:
            continue
        airline, country = rng.choice(AIRLINES)
        speed = rng.uniform(210, 250)
        flight_time = distance / speed
        plan.append(
            _Flight(
                icao24=new_icao(),
                callsign=f"{airline}{rng.randint(10, 9999)}",
                country=country,
                origin=origin,
                dest=dest,
                distance=distance,
                cruise_alt=float(rng.choice(range(9_000, 12_500, 300))),
                cruise_speed=speed,
                start_offset=rng.uniform(-flight_time, duration_s * 0.8),
                squawk=f"{rng.randint(1000, 6777):04d}",
            )
        )
    # Scripted anomalies on long-haul flights that are airborne for the whole recording.
    for flight, scenario in zip(
        plan[:3], ("emergency", "radio_failure", "rapid_descent"), strict=True
    ):
        flight.scenario = scenario
        flight.start_offset = -1200.0
        flight.distance = max(flight.distance, 3_000_000.0)

    holding = [
        (new_icao(), "BAW286", "United Kingdom", (51.62, -0.18), 2_400.0),  # Bovingdon stack (EGLL)
        (new_icao(), "AFR1734", "France", (49.20, 2.80), 3_000.0),  # East of LFPG
    ]
    ground = [
        (new_icao(), f"{rng.choice(AIRLINES)[0]}{rng.randint(100, 999)}", ap)
        for ap in AIRPORTS[:20]
    ]

    states: list[StateVector] = []
    for step in range(0, duration_s + 1, interval_s):
        snapshot = t0 + step
        ingested = snapshot + 1.5
        rows: list[tuple[str, str, str, dict[str, float | bool | str | None]]] = []
        for flight in plan:
            kin = flight.state_at(step)
            if kin is not None:
                rows.append((flight.icao24, flight.callsign, flight.country, kin))
        for icao, callsign, country, center, alt in holding:
            rows.append((icao, callsign, country, _holding_state(center, step, alt)))
        for icao, callsign, airport in ground:
            rows.append(
                (
                    icao,
                    callsign,
                    "France",
                    {
                        "latitude": airport[1] + 0.002,
                        "longitude": airport[2] + 0.002,
                        "baro_altitude": None,
                        "geo_altitude": None,
                        "velocity": 0.0,
                        "true_track": 90.0,
                        "vertical_rate": None,
                        "on_ground": True,
                        "squawk": "2000",
                    },
                )
            )
        for icao, callsign, country, kin in rows:
            states.append(
                StateVector.model_validate(
                    {
                        "icao24": icao,
                        "callsign": callsign,
                        "origin_country": country,
                        "time_position": snapshot - rng.randint(0, 4),
                        "last_contact": snapshot - rng.randint(0, 2),
                        "snapshot_time": snapshot,
                        "ingested_at": ingested,
                        "source": "synthetic",
                        "position_source": 0,
                        "category": 4,
                        **kin,
                    }
                )
            )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(states_to_table(states), output, compression="zstd")
    return output
