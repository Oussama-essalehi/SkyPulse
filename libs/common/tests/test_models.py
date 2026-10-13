from skyradar_common.models import Alert, AlertType, Severity, StateVector


def _state(**overrides: object) -> StateVector:
    base: dict[str, object] = {
        "icao24": "ABC123",
        "callsign": "AFR123  ",
        "origin_country": "France",
        "time_position": 1_700_000_000,
        "last_contact": 1_700_000_001,
        "longitude": 2.35,
        "latitude": 48.85,
        "baro_altitude": None,
        "geo_altitude": 10_000.0,
        "squawk": "  ",
        "snapshot_time": 1_700_000_005,
        "ingested_at": 1_700_000_006.5,
    }
    base.update(overrides)
    return StateVector.model_validate(base)


def test_state_vector_normalisation() -> None:
    state = _state()
    assert state.icao24 == "abc123"
    assert state.callsign == "AFR123"
    assert state.squawk is None
    assert state.altitude == 10_000.0


def test_state_vector_roundtrip_bytes() -> None:
    state = _state(baro_altitude=9_800.0)
    assert StateVector.from_bytes(state.to_bytes()) == state
    assert state.altitude == 9_800.0


def test_alert_roundtrip() -> None:
    alert = Alert(
        alert_id="squawk_7700:abc123:1",
        icao24="abc123",
        type=AlertType.SQUAWK_EMERGENCY,
        severity=Severity.CRITICAL,
        message="General emergency",
        event_time=1,
        detected_at=2.0,
    )
    decoded = Alert.from_bytes(alert.to_bytes())
    assert decoded.type is AlertType.SQUAWK_EMERGENCY
    assert decoded == alert
