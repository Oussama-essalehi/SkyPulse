"""Message schemas exchanged on the Redpanda topics.

These pydantic models are the single source of truth for the event contracts:
the ingestion service produces them, every consumer (API, Parquet sink,
anomaly detector) parses them. Bump ``SCHEMA_VERSION`` on breaking changes.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import orjson
from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1


class _Message(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    def to_bytes(self) -> bytes:
        return orjson.dumps(self.model_dump(mode="json"))

    @classmethod
    def from_bytes(cls, raw: bytes) -> Any:
        return cls.model_validate(orjson.loads(raw))


class StateVector(_Message):
    """One aircraft position report (OpenSky state vector + ingestion metadata)."""

    icao24: str = Field(min_length=6, max_length=6, description="ICAO 24-bit address, hex")
    callsign: str | None = None
    origin_country: str = ""
    time_position: int | None = Field(None, description="Unix time of the last position update")
    last_contact: int = Field(description="Unix time of the last message from the transponder")
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    baro_altitude: float | None = Field(None, description="Barometric altitude, meters")
    on_ground: bool = False
    velocity: float | None = Field(None, description="Ground speed, m/s")
    true_track: float | None = Field(None, description="Track angle, degrees clockwise from north")
    vertical_rate: float | None = Field(None, description="Vertical rate, m/s (positive = climb)")
    geo_altitude: float | None = Field(None, description="Geometric altitude, meters")
    squawk: str | None = None
    spi: bool = False
    position_source: int = 0
    category: int | None = None
    snapshot_time: int = Field(description="Unix time of the source snapshot")
    ingested_at: float = Field(description="Unix time (float) when SkyRadar received the data")
    source: str = "opensky"
    schema_version: int = SCHEMA_VERSION

    @field_validator("icao24")
    @classmethod
    def _lower_icao(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("callsign", "squawk")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @property
    def altitude(self) -> float | None:
        """Best available altitude (barometric first, geometric as fallback)."""
        return self.baro_altitude if self.baro_altitude is not None else self.geo_altitude


class SnapshotMeta(_Message):
    """Metadata emitted once per polled snapshot (on ``aircraft.snapshots``)."""

    snapshot_time: int
    ingested_at: float
    aircraft_count: int
    source: str
    poll_interval_seconds: float
    latency_ms: float | None = None
    credits_remaining: int | None = None
    schema_version: int = SCHEMA_VERSION


class AlertType(StrEnum):
    SQUAWK_HIJACK = "squawk_7500"
    SQUAWK_RADIO_FAILURE = "squawk_7600"
    SQUAWK_EMERGENCY = "squawk_7700"
    RAPID_DESCENT = "rapid_descent"
    HOLDING_PATTERN = "holding_pattern"
    SIGNAL_LOSS = "signal_loss"
    DIVERSION = "diversion"
    TRAJECTORY_OUTLIER = "trajectory_outlier"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"


class Alert(_Message):
    """An anomaly raised by the detector (on ``aircraft.alerts``)."""

    alert_id: str = Field(description="Stable id: <type>:<icao24>:<first event time>")
    icao24: str
    callsign: str | None = None
    type: AlertType
    severity: Severity
    status: AlertStatus = AlertStatus.ACTIVE
    message: str = Field(description="Human readable explanation")
    latitude: float | None = None
    longitude: float | None = None
    altitude: float | None = None
    event_time: int = Field(description="Unix time of the triggering position")
    detected_at: float
    details: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION
