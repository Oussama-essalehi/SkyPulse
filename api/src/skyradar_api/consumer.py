"""Redpanda consumer feeding the in-memory :class:`LiveState`.

Each API replica reads every partition (no consumer group: the live state is
a broadcast view, not a work queue). On start-up we seek back by timestamp so
the map is populated immediately instead of waiting for the next poll.
"""

from __future__ import annotations

import asyncio
import time

import structlog
from aiokafka import AIOKafkaConsumer, TopicPartition
from pydantic import ValidationError

from skyradar_api.state import LiveState
from skyradar_common.kafka import KafkaSettings, start_consumer
from skyradar_common.models import Alert, SnapshotMeta, StateVector
from skyradar_common.topics import AIRCRAFT_ALERTS, AIRCRAFT_SNAPSHOTS, AIRCRAFT_STATES

log = structlog.get_logger(__name__)


class LiveConsumer:
    def __init__(
        self,
        state: LiveState,
        kafka: KafkaSettings,
        lookback_seconds: int,
        alerts_lookback_seconds: int,
    ) -> None:
        self._state = state
        self._kafka = kafka
        self._lookback = lookback_seconds
        self._alerts_lookback = alerts_lookback_seconds
        self._consumer: AIOKafkaConsumer | None = None
        self.connected = False
        self.last_message_at: float | None = None

    async def _assign_and_seek(self, consumer: AIOKafkaConsumer) -> None:
        now_ms = int(time.time() * 1000)
        partitions: list[TopicPartition] = []
        seek_ts: dict[TopicPartition, int] = {}
        for topic in (AIRCRAFT_STATES, AIRCRAFT_SNAPSHOTS, AIRCRAFT_ALERTS):
            ids: set[int] | None = None
            for _ in range(30):  # topics may still be being created by ingestion
                ids = consumer.partitions_for_topic(topic)
                if ids:
                    break
                await consumer._client.force_metadata_update()
                await asyncio.sleep(2)
            lookback = self._alerts_lookback if topic == AIRCRAFT_ALERTS else self._lookback
            for pid in sorted(ids or ()):
                tp = TopicPartition(topic, pid)
                partitions.append(tp)
                seek_ts[tp] = now_ms - lookback * 1000
        consumer.assign(partitions)
        offsets = await consumer.offsets_for_times(seek_ts)
        end = await consumer.end_offsets(partitions)
        for tp in partitions:
            found = offsets.get(tp)
            consumer.seek(tp, found.offset if found is not None else end[tp])
        log.info("live_consumer_assigned", partitions=len(partitions))

    async def run(self) -> None:
        consumer = await start_consumer(self._kafka, [], group_id=None, enable_auto_commit=False)
        self._consumer = consumer
        try:
            await self._assign_and_seek(consumer)
            self.connected = True
            while True:
                batches = await consumer.getmany(timeout_ms=500, max_records=10_000)
                for tp, records in batches.items():
                    for record in records:
                        self._dispatch(tp.topic, record.value)
                if batches:
                    self.last_message_at = time.time()
        finally:
            self.connected = False
            await consumer.stop()

    def _dispatch(self, topic: str, value: bytes | None) -> None:
        if value is None:
            return
        try:
            if topic == AIRCRAFT_STATES:
                self._state.apply_state(StateVector.from_bytes(value))
            elif topic == AIRCRAFT_SNAPSHOTS:
                self._state.apply_snapshot(SnapshotMeta.from_bytes(value))
            elif topic == AIRCRAFT_ALERTS:
                self._state.apply_alert(Alert.from_bytes(value))
        except (ValidationError, ValueError) as exc:
            log.warning("invalid_message", topic=topic, error=str(exc)[:200])
