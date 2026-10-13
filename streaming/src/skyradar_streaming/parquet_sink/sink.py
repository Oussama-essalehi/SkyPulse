"""Buffer Kafka records and write them as hive-partitioned Parquet files.

Delivery semantics: **at-least-once**.

1. Records are buffered in memory (offsets are *not* committed yet).
2. Every ``flush_interval`` seconds (or ``max_rows``), one Parquet file per
   (dataset, date, hour) partition is written to object storage.
3. Only after every file of the batch is durably written are the consumer
   offsets committed.

A crash between 2 and 3 replays the batch on restart. File names are derived
from the Kafka offset ranges of the batch, so an identical replay overwrites the
same object; a replay with different batch boundaries can duplicate at most one
batch, which ``stg_aircraft_states`` removes (dedup on icao24 + time_position).
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import structlog
from pyarrow import fs as pafs

from skyradar_common.models import Alert, StateVector
from skyradar_common.parquet import alerts_to_table, states_to_table
from skyradar_common.topics import AIRCRAFT_ALERTS, AIRCRAFT_STATES

log = structlog.get_logger(__name__)

DATASETS = {AIRCRAFT_STATES: "raw/aircraft_states", AIRCRAFT_ALERTS: "raw/alerts"}


def partition_of(epoch_seconds: int) -> tuple[str, str]:
    moment = datetime.fromtimestamp(epoch_seconds, tz=UTC)
    return moment.strftime("%Y-%m-%d"), moment.strftime("%H")


@dataclass
class _Buffer:
    rows: dict[tuple[str, str, str], list[Any]] = field(default_factory=lambda: defaultdict(list))
    offsets: dict[tuple[str, int], tuple[int, int]] = field(default_factory=dict)
    count: int = 0

    def track_offset(self, topic: str, partition: int, offset: int) -> None:
        key = (topic, partition)
        first, _ = self.offsets.get(key, (offset, offset))
        self.offsets[key] = (first, offset)


class ParquetSink:
    def __init__(self, filesystem: pafs.FileSystem, base_path: str) -> None:
        self._fs = filesystem
        self._base = base_path.rstrip("/")
        self._buffer = _Buffer()

    @property
    def buffered(self) -> int:
        return self._buffer.count

    def add(self, topic: str, partition: int, offset: int, value: bytes) -> None:
        """Parse and buffer one record. Invalid records are logged and skipped."""
        self._buffer.track_offset(topic, partition, offset)
        try:
            if topic == AIRCRAFT_STATES:
                state = StateVector.from_bytes(value)
                date, hour = partition_of(state.snapshot_time)
                self._buffer.rows[(topic, date, hour)].append(state)
            elif topic == AIRCRAFT_ALERTS:
                alert = Alert.from_bytes(value)
                date, hour = partition_of(alert.event_time)
                self._buffer.rows[(topic, date, hour)].append(alert)
            else:
                return
        except ValueError as exc:
            log.warning("sink_invalid_record", topic=topic, offset=offset, error=str(exc)[:200])
            return
        self._buffer.count += 1

    def _batch_id(self) -> str:
        ranges = sorted(f"{t}:{p}:{a}-{b}" for (t, p), (a, b) in self._buffer.offsets.items())
        return hashlib.sha1("|".join(ranges).encode()).hexdigest()[:16]

    def flush(self) -> list[str]:
        """Write all buffered rows; returns the written paths. Raises on failure
        (the caller must then *not* commit offsets)."""
        if not self._buffer.rows:
            self._buffer = _Buffer()
            return []
        batch_id = self._batch_id()
        written: list[str] = []
        for (topic, date, hour), rows in sorted(self._buffer.rows.items()):
            table: pa.Table = (
                states_to_table(rows) if topic == AIRCRAFT_STATES else alerts_to_table(rows)
            )
            directory = f"{self._base}/{DATASETS[topic]}/date={date}/hour={hour}"
            path = f"{directory}/part-{batch_id}.parquet"
            self._fs.create_dir(directory, recursive=True)
            pq.write_table(table, path, filesystem=self._fs, compression="zstd")
            written.append(path)
            log.info("parquet_written", path=path, rows=table.num_rows)
        self._buffer = _Buffer()
        return written
