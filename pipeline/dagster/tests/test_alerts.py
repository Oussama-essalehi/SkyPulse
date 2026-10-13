import pandas as pd

from skyradar_common.models import AlertType
from skyradar_orchestration import alerts

NOW = pd.Timestamp("2026-10-13 12:00:00").timestamp()


def test_diversion_alerts_only_recent() -> None:
    diversions = pd.DataFrame(
        {
            "flight_id": ["a-1", "b-2"],
            "icao24": ["aaaaaa", "bbbbbb"],
            "callsign": ["SKR100", "X1"],
            "departure_airport": ["LFPG", "LFPG"],
            "arrival_airport": ["LFMN", "EGLL"],
            "usual_arrival_airport": ["LFLL", "EDDF"],
            "usual_arrival_flights": [3, 5],
            "usual_arrival_share": [0.75, 0.8],
            "departure_time": pd.to_datetime(["2026-10-13 10:40", "2026-10-12 08:00"]),
            "arrival_time": pd.to_datetime(["2026-10-13 11:45", "2026-10-12 09:00"]),
            "arrival_latitude": [43.66, 51.47],
            "arrival_longitude": [7.21, -0.45],
        }
    )
    out = alerts.diversion_alerts(diversions, now=NOW)
    assert [a.icao24 for a in out] == ["aaaaaa"]
    alert = out[0]
    assert alert.type is AlertType.DIVERSION
    assert "LFMN" in alert.message
    assert "LFLL" in alert.message
    assert alert.alert_id == alerts.diversion_alerts(diversions, now=NOW)[0].alert_id


def test_outlier_alerts_only_flagged_and_recent() -> None:
    scores = pd.DataFrame(
        {
            "flight_id": ["a-100", "b-200", "c-300"],
            "icao24": ["aaaaaa", "bbbbbb", "cccccc"],
            "callsign": ["A", "B", "C"],
            "is_outlier": [True, False, True],
            "last_seen_ts": pd.to_datetime(
                ["2026-10-13 11:50", "2026-10-13 11:50", "2026-10-13 06:00"]
            ),
            "last_latitude": [48.0, 48.0, 48.0],
            "last_longitude": [2.0, 2.0, 2.0],
            "anomaly_score": [0.8, 0.4, 0.9],
            "explanation": ["taux de virage élevé(e)", None, "x"],
        }
    )
    out = alerts.outlier_alerts(scores, now=NOW)
    assert [a.icao24 for a in out] == ["aaaaaa"]
    assert out[0].type is AlertType.TRAJECTORY_OUTLIER
    assert alerts.publish([]) == 0
