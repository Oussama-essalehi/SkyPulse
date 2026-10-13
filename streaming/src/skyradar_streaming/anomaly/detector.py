"""Stateful detector: per-aircraft sliding windows -> alert lifecycle events."""

from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass

from skyradar_common.models import Alert, AlertStatus, AlertType, SnapshotMeta, StateVector
from skyradar_streaming.anomaly.rules import (
    Finding,
    RuleConfig,
    coverage_cell,
    evaluate,
    signal_loss_rule,
)


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
        # Signal-loss bookkeeping: snapshots in which each aircraft was seen, and the
        # receiver coverage (aircraft per grid cell) of the most recent snapshots.
        self._sightings: dict[str, int] = {}
        self._last_snapshot: dict[str, int] = {}
        self._coverage: dict[int, Counter[tuple[int, int]]] = {}
        self._snapshots: deque[int] = deque(maxlen=self.config.signal_loss_confirm_snapshots + 1)

    @property
    def tracked(self) -> int:
        return len(self._tracks)

    @property
    def active(self) -> list[Alert]:
        return [a.alert for a in self._active.values()]

    def process(self, state: StateVector, now: float | None = None) -> list[Alert]:
        """Ingest one state; return alert events to publish (new, heartbeat, resolved)."""
        now = now or time.time()
        self._record_sighting(state)
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

    def _record_sighting(self, state: StateVector) -> None:
        if self._last_snapshot.get(state.icao24) != state.snapshot_time:
            self._last_snapshot[state.icao24] = state.snapshot_time
            self._sightings[state.icao24] = self._sightings.get(state.icao24, 0) + 1
            cell = coverage_cell(state.latitude, state.longitude, self.config.signal_loss_cell_deg)
            self._coverage.setdefault(state.snapshot_time, Counter())[cell] += 1

    def signal_loss_enabled(self, poll_interval_seconds: float) -> bool:
        """Whether polling is frequent enough for the signal-loss rule to ever fire."""
        cfg = self.config
        return (
            poll_interval_seconds * cfg.signal_loss_confirm_snapshots
            <= cfg.signal_loss_max_extrapolation_s
        )

    def on_snapshot(self, meta: SnapshotMeta, now: float | None = None) -> list[Alert]:
        """Called once all states of a snapshot were processed: detects signal losses.

        Only aircraft last seen exactly ``signal_loss_confirm_snapshots`` snapshots ago
        (and missing from every snapshot since) are evaluated, so each disappearance is
        reported once and transient dropouts are ignored. Replays are skipped: when a
        replay loops, every aircraft of the last snapshot "disappears".
        """
        now = now or time.time()
        events: list[Alert] = []
        cells = self._coverage.get(meta.snapshot_time)
        if self._snapshots and self._snapshots[-1] >= meta.snapshot_time:
            return events  # duplicate (at-least-once delivery) or out of order
        self._snapshots.append(meta.snapshot_time)
        for old in [t for t in self._coverage if t < meta.snapshot_time - 3600]:
            del self._coverage[old]
        complete = len(self._snapshots) > self.config.signal_loss_confirm_snapshots
        if not cells or not complete or meta.source == "replay":
            return events
        reference = self._snapshots[0]
        for icao24, seen in self._last_snapshot.items():
            if seen != reference or (icao24, AlertType.SIGNAL_LOSS) in self._active:
                continue
            track = self._tracks.get(icao24)
            if not track:
                continue
            finding = signal_loss_rule(
                track[-1], self._sightings.get(icao24, 0), meta.snapshot_time, cells, self.config
            )
            if finding is not None:
                alert = self._make_alert(track[-1], finding, now)
                self._active[(icao24, AlertType.SIGNAL_LOSS)] = _Active(alert, now)
                events.append(alert)
        return events

    def evict_stale(self, now_epoch: float, max_age_s: float = 1800) -> list[Alert]:
        """Forget aircraft not seen for ``max_age_s``; resolve their alerts."""
        events: list[Alert] = []
        for icao24 in [
            k for k, t in self._tracks.items() if now_epoch - t[-1].last_contact > max_age_s
        ]:
            del self._tracks[icao24]
            self._sightings.pop(icao24, None)
            self._last_snapshot.pop(icao24, None)
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
