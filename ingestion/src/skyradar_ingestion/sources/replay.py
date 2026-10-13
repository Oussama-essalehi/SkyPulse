"""Replay a recorded Parquet file as if it were live.

Used for development, tests and offline demos (no OpenSky quota consumed).
Timestamps are rebased so every replayed snapshot happens "now"; the cadence
between snapshots is preserved (optionally accelerated with ``speed``).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import structlog

from skyradar_common.parquet import STATES_SCHEMA, table_to_states
from skyradar_ingestion.sources.base import FlightDataSource, Snapshot

log = structlog.get_logger(__name__)

_TIME_COLUMNS = ("time_position", "last_contact", "snapshot_time")


def load_replay_table(path: str | Path) -> pa.Table:
    """Load a Parquet file or a directory of Parquet files (hive partitions ok)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"replay file not found: {path}")
    dataset = ds.dataset(str(path), format="parquet", partitioning="hive")
    columns = [c for c in STATES_SCHEMA.names if c in dataset.schema.names]
    table = dataset.to_table(columns=columns)
    if table.num_rows == 0:
        raise ValueError(f"replay file is empty: {path}")
    return table.sort_by("snapshot_time")


class ReplaySource(FlightDataSource):
    name = "replay"

    def __init__(
        self,
        path: str | Path,
        speed: float = 1.0,
        loop: bool = True,
        rebase_time: bool = True,
        max_gap_seconds: float = 120.0,
    ) -> None:
        if speed <= 0:
            raise ValueError("speed must be > 0")
        self._table = load_replay_table(path)
        self._speed = speed
        self._loop = loop
        self._rebase = rebase_time
        self._max_gap = max_gap_seconds
        times = self._table.column("snapshot_time").unique().to_pylist()
        self._snapshot_times: list[int] = sorted(int(t) for t in times)
        gaps = [b - a for a, b in zip(self._snapshot_times, self._snapshot_times[1:], strict=False)]
        self._nominal_interval = (sorted(gaps)[len(gaps) // 2] if gaps else 10.0) / speed
        log.info(
            "replay_loaded",
            path=str(path),
            rows=self._table.num_rows,
            snapshots=len(self._snapshot_times),
            duration_s=self._snapshot_times[-1] - self._snapshot_times[0],
            speed=speed,
        )

    @property
    def poll_interval_seconds(self) -> float:
        return self._nominal_interval

    def _snapshot(self, snapshot_time: int, offset: int) -> Snapshot:
        mask = pc.equal(self._table.column("snapshot_time"), snapshot_time)
        table = self._table.filter(mask)
        if offset:
            for name in _TIME_COLUMNS:
                idx = table.schema.get_field_index(name)
                if idx >= 0:
                    table = table.set_column(idx, name, pc.add(table.column(name), offset))
        now = time.time()
        idx = table.schema.get_field_index("ingested_at")
        ingested = pa.array([now] * table.num_rows, pa.float64())
        table = (
            table.set_column(idx, "ingested_at", ingested)
            if idx >= 0
            else table.append_column("ingested_at", ingested)
        )
        states = [s.model_copy(update={"source": self.name}) for s in table_to_states(table)]
        return Snapshot(
            snapshot_time=snapshot_time + offset,
            states=states,
            source=self.name,
            poll_interval_seconds=self._nominal_interval,
        )

    async def stream(self) -> AsyncIterator[Snapshot]:
        iteration = 0
        while True:
            for i, snapshot_time in enumerate(self._snapshot_times):
                # Rebase each snapshot to "now": the map shows fresh data and the
                # Parquet sink partitions replayed rows by the current hour.
                offset = int(time.time()) - snapshot_time if self._rebase else 0
                yield self._snapshot(snapshot_time, offset)
                if i + 1 < len(self._snapshot_times):
                    gap = min(self._snapshot_times[i + 1] - snapshot_time, self._max_gap)
                    await asyncio.sleep(gap / self._speed)
            iteration += 1
            if not self._loop:
                return
            log.info("replay_looping", iteration=iteration)
            await asyncio.sleep(self._nominal_interval)
