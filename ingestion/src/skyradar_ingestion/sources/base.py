"""Source abstraction (adapter pattern).

Every ADS-B provider (OpenSky, a recorded replay, later airplanes.live or a
local dump1090 receiver) implements :class:`FlightDataSource`. The ingestion
loop only depends on this interface; each source owns its own pacing because
the right cadence depends on the provider (credit quota, replay speed, ...).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from skyradar_common.models import StateVector


@dataclass(frozen=True)
class Snapshot:
    """All aircraft states returned by one poll of a source."""

    snapshot_time: int
    states: list[StateVector]
    source: str
    poll_interval_seconds: float
    latency_ms: float | None = None
    credits_remaining: int | None = None
    skipped_rows: int = 0
    extra: dict[str, object] = field(default_factory=dict)


class FlightDataSource(ABC):
    """Interface implemented by every flight data provider."""

    name: str

    @property
    @abstractmethod
    def poll_interval_seconds(self) -> float:
        """Nominal time between two snapshots."""

    @abstractmethod
    def stream(self) -> AsyncIterator[Snapshot]:
        """Yield snapshots forever (or until the source is exhausted)."""

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        """Release network clients / file handles."""
