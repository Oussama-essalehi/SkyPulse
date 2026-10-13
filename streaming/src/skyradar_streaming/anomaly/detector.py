"""Stateful detector: per-aircraft sliding windows -> alert lifecycle events."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from skyradar_common.models import Alert, AlertStatus, AlertType, StateVector
from skyradar_streaming.anomaly.rules import Finding, RuleConfig, evaluate


@dataclass
class _Active:
    alert: Alert
    last_emitted: float


class AnomalyDetector:
    def __init__(
        self,
        config: RuleConfig | None = None,
        history_seconds: int = 1800,
        heartbeat_seconds: float = 300,
    ) -> None:
        self.config = config or RuleConfig()
        self._history_s = history_seconds
        self._heartbeat = heartbeat_seconds
        self._tracks: dict[str, deque[StateVector]] = {}
        self._active: dict[tuple[str, AlertType], _Active] = {}

    @property
    def tracked(self) -> int:
        return len(self._tracks)

    @property
    def active(self) -> list[Alert]:
        return [a.alert for a in self._active.values()]

    def process(self, state: StateVector, now: float | None = None) -> list[Alert]:
        """Ingest one state; return alert events to publish (new, heartbeat, resolved)."""
        now = now or time.time()
        track = self._tracks.setdefault(state.icao24, deque())
        if track and (state.time_position or state.last_contact) <= (
            track[-1].time_position or track[-1].last_contact
        ):
            return []  # duplicate or out-of-order position
        track.append(state)
        horizon = (state.time_position or state.last_contact) - self._history_s
        while track and (track[0].time_position or track[0].last_contact) < horizon:
            track.popleft()

        findings = {f.type: f for f in evaluate(track, self.config)}
        events: list[Alert] = []
        for alert_type, finding in findings.items():
            key = (state.icao24, alert_type)
            current = self._active.get(key)
            if current is None:
                alert = self._make_alert(state, finding, now)
                self._active[key] = _Active(alert, now)
                events.append(alert)
            elif now - current.last_emitted >= self._heartbeat:
                # Heartbeat keeps the alert alive in the API and refreshes its position.
                alert = self._make_alert(state, finding, now, alert_id=current.alert.alert_id)
                self._active[key] = _Active(alert, now)
                events.append(alert)
        for key in [k for k in self._active if k[0] == state.icao24 and k[1] not in findings]:
            resolved = self._active.pop(key).alert.model_copy(
                update={"status": AlertStatus.RESOLVED, "detected_at": now}
            )
            events.append(resolved)
        return events

    def evict_stale(self, now_epoch: float, max_age_s: float = 1800) -> list[Alert]:
        """Forget aircraft not seen for ``max_age_s``; resolve their alerts."""
        events: list[Alert] = []
        for icao24 in [
            k for k, t in self._tracks.items() if now_epoch - t[-1].last_contact > max_age_s
        ]:
            del self._tracks[icao24]
            for key in [k for k in self._active if k[0] == icao24]:
                events.append(
                    self._active.pop(key).alert.model_copy(
                        update={"status": AlertStatus.RESOLVED, "detected_at": now_epoch}
                    )
                )
        return events

    @staticmethod
    def _make_alert(
        state: StateVector, finding: Finding, now: float, alert_id: str | None = None
    ) -> Alert:
        event_time = state.time_position or state.last_contact
        return Alert(
            alert_id=alert_id or f"{finding.type.value}:{state.icao24}:{event_time}",
            icao24=state.icao24,
            callsign=state.callsign,
            type=finding.type,
            severity=finding.severity,
            message=finding.message,
            latitude=state.latitude,
            longitude=state.longitude,
            altitude=state.altitude,
            event_time=event_time,
            detected_at=now,
            details=finding.details,
        )
