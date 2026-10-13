import pytest

from skyradar_common.models import Alert, AlertStatus, AlertType, SnapshotMeta, StateVector
from skyradar_ingestion.synthetic import destination_point
from skyradar_streaming.anomaly.detector import AnomalyDetector
from skyradar_streaming.anomaly.rules import (
    RuleConfig,
    evaluate,
    heading_delta,
    holding_rule,
    rapid_descent_rule,
)

CFG = RuleConfig()


def sv(t: int, lat: float = 48.0, lon: float = 2.0, **kw: object) -> StateVector:
    data: dict[str, object] = {
        "icao24": "abc123",
        "callsign": "TEST1",
        "time_position": t,
        "last_contact": t,
        "latitude": lat,
        "longitude": lon,
        "baro_altitude": 10_000.0,
        "velocity": 230.0,
        "true_track": 90.0,
        "vertical_rate": 0.0,
        "snapshot_time": t,
        "ingested_at": float(t),
    }
    data.update(kw)
    return StateVector.model_validate(data)


def circling_track(interval: int, minutes: int) -> list[StateVector]:
    """Aircraft circling at 3 deg/s around a fixed point (radius 7 km)."""
    track = []
    for t in range(0, minutes * 60 + 1, interval):
        angle = (t * 3.0) % 360
        lat, lon = destination_point(51.6, -0.2, angle, 7_000)
        track.append(
            sv(
                1000 + t,
                lat,
                lon,
                true_track=(angle + 90) % 360,
                velocity=110.0,
                baro_altitude=2_400.0,
            )
        )
    return track


def straight_track(interval: int, minutes: int) -> list[StateVector]:
    track = []
    for t in range(0, minutes * 60 + 1, interval):
        lat, lon = destination_point(48.0, 2.0, 90.0, 230.0 * t)
        track.append(sv(1000 + t, lat, lon))
    return track


@pytest.mark.parametrize(("a", "b", "expected"), [(350, 10, 20), (10, 350, -20), (0, 180, -180)])
def test_heading_delta(a: float, b: float, expected: float) -> None:
    assert heading_delta(a, b) == expected


@pytest.mark.parametrize(
    ("squawk", "alert_type"),
    [
        ("7500", AlertType.SQUAWK_HIJACK),
        ("7600", AlertType.SQUAWK_RADIO_FAILURE),
        ("7700", AlertType.SQUAWK_EMERGENCY),
    ],
)
def test_squawk_rules(squawk: str, alert_type: AlertType) -> None:
    findings = evaluate([sv(1, squawk=squawk)], CFG)
    assert [f.type for f in findings] == [alert_type]


def test_normal_squawk_no_alert() -> None:
    assert evaluate([sv(1, squawk="1000")], CFG) == []


def test_rapid_descent() -> None:
    assert rapid_descent_rule(sv(1, vertical_rate=-40.0), CFG) is not None
    assert rapid_descent_rule(sv(1, vertical_rate=-12.0), CFG) is None  # normal descent
    assert rapid_descent_rule(sv(1, vertical_rate=-40.0, baro_altitude=800.0), CFG) is None


def test_holding_detected_by_heading_with_dense_sampling() -> None:
    finding = holding_rule(circling_track(interval=10, minutes=5), CFG)
    assert finding is not None
    assert finding.details["method"] == "heading"


def test_holding_detected_by_confinement_with_sparse_sampling() -> None:
    # OpenSky standard-tier cadence: 96 s between positions, heading deltas alias.
    finding = holding_rule(circling_track(interval=96, minutes=14), CFG)
    assert finding is not None
    assert finding.details["method"] == "confinement"


@pytest.mark.parametrize("interval", [10, 96])
def test_straight_flight_is_not_holding(interval: int) -> None:
    assert holding_rule(straight_track(interval, minutes=14), CFG) is None


def test_detector_lifecycle() -> None:
    detector = AnomalyDetector(heartbeat_seconds=300)
    events = detector.process(sv(100, squawk="7700"), now=100)
    assert [(e.type, e.status) for e in events] == [
        (AlertType.SQUAWK_EMERGENCY, AlertStatus.ACTIVE)
    ]
    alert_id = events[0].alert_id
    assert detector.process(sv(110, squawk="7700"), now=110) == []  # still active, no heartbeat yet
    heartbeat = detector.process(sv(500, squawk="7700"), now=500)
    assert [e.alert_id for e in heartbeat] == [alert_id]
    resolved = detector.process(sv(510, squawk="1000"), now=510)
    assert [(e.alert_id, e.status) for e in resolved] == [(alert_id, AlertStatus.RESOLVED)]
    assert detector.active == []


def test_detector_ignores_duplicates_and_evicts() -> None:
    detector = AnomalyDetector()
    detector.process(sv(100, squawk="7600"), now=100)
    assert detector.process(sv(100, squawk="7600"), now=101) == []
    events = detector.evict_stale(now_epoch=100 + 3600)
    assert [e.status for e in events] == [AlertStatus.RESOLVED]
    assert detector.tracked == 0


# --------------------------------------------------------------------- signal loss
def snap(t: int, source: str = "opensky") -> SnapshotMeta:
    return SnapshotMeta(
        snapshot_time=t,
        ingested_at=float(t),
        aircraft_count=0,
        source=source,
        poll_interval_seconds=96,
    )


def crowd(t: int, lat: float, lon: float, n: int = 8) -> list[StateVector]:
    """Other aircraft received around (lat, lon): the area is covered."""
    return [sv(t, lat + 0.1 * i, lon, icao24=f"f{i:05x}", snapshot_time=t) for i in range(n)]


def run_snapshots(
    detector: AnomalyDetector, snapshots: list[list[StateVector]], source: str = "opensky"
) -> list[Alert]:
    events: list[Alert] = []
    for states in snapshots:
        t = states[0].snapshot_time
        for s in states:
            events.extend(detector.process(s, now=t))
        events.extend(detector.on_snapshot(snap(t, source), now=t))
    return events


def cruising(t: int, **kw: object) -> StateVector:
    data: dict[str, object] = {
        "snapshot_time": t,
        "baro_altitude": 11_000.0,
        "velocity": 230.0,
        "true_track": 90.0,
    }
    data.update(kw)
    return sv(t, 48.0, 2.0 + (t - 1000) * 0.003, **data)


def test_signal_loss_in_covered_area() -> None:
    detector = AnomalyDetector()
    events = run_snapshots(
        detector,
        [
            [cruising(1000), *crowd(1000, 48.0, 2.0)],
            [cruising(1096), *crowd(1096, 48.0, 2.3)],
            crowd(1192, 48.0, 2.6),  # our aircraft is gone, neighbours still received
        ],
    )
    lost = [e for e in events if e.type is AlertType.SIGNAL_LOSS]
    assert len(lost) == 1
    assert lost[0].icao24 == "abc123"
    assert lost[0].details["neighbours"] >= 5
    # It comes back: the alert is resolved.
    back = detector.process(cruising(1288), now=1288)
    assert [(e.type, e.status) for e in back] == [(AlertType.SIGNAL_LOSS, AlertStatus.RESOLVED)]


def test_no_signal_loss_when_leaving_coverage() -> None:
    detector = AnomalyDetector()
    events = run_snapshots(
        detector,
        [
            [cruising(1000), *crowd(1000, 48.0, 2.0)],
            [cruising(1096), *crowd(1096, 48.0, 2.3)],
            crowd(1192, 10.0, -40.0),  # nobody received where the aircraft should be
        ],
    )
    assert not [e for e in events if e.type is AlertType.SIGNAL_LOSS]


@pytest.mark.parametrize(
    "override",
    [{"baro_altitude": 1_500.0}, {"vertical_rate": -12.0}, {"on_ground": True}],
)
def test_no_signal_loss_for_low_descending_or_ground(override: dict[str, object]) -> None:
    detector = AnomalyDetector()
    events = run_snapshots(
        detector,
        [
            [cruising(1000, **override), *crowd(1000, 48.0, 2.0)],
            [cruising(1096, **override), *crowd(1096, 48.0, 2.3)],
            crowd(1192, 48.0, 2.6),
        ],
    )
    assert not [e for e in events if e.type is AlertType.SIGNAL_LOSS]


def test_no_signal_loss_in_replay_or_single_sighting() -> None:
    snapshots = [
        [cruising(1096), *crowd(1096, 48.0, 2.3)],
        crowd(1192, 48.0, 2.6),
    ]
    # Only one sighting before disappearing: not enough evidence.
    assert not [
        e for e in run_snapshots(AnomalyDetector(), snapshots) if e.type is AlertType.SIGNAL_LOSS
    ]
    replay = [[cruising(1000), *crowd(1000, 48.0, 2.0)], *snapshots]
    assert not [
        e
        for e in run_snapshots(AnomalyDetector(), replay, source="replay")
        if e.type is AlertType.SIGNAL_LOSS
    ]
