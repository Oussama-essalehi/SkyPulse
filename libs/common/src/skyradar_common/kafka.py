"""Thin helpers around aiokafka (Redpanda speaks the Kafka protocol)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import structlog
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import KafkaConnectionError, TopicAlreadyExistsError
from pydantic_settings import BaseSettings, SettingsConfigDict

from skyradar_common.topics import AIRCRAFT_ALERTS, AIRCRAFT_SNAPSHOTS, AIRCRAFT_STATES

log = structlog.get_logger(__name__)


class KafkaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAFKA_", extra="ignore")

    bootstrap_servers: str = "localhost:19092"
    states_partitions: int = 6
    # Raw positions are also in the lake; keep 24h in the broker for replays/catch-up.
    states_retention_ms: int = 24 * 3600 * 1000
    alerts_retention_ms: int = 7 * 24 * 3600 * 1000
    # Managed Kafka (Redpanda Serverless, Confluent Cloud...): SASL_SSL + SCRAM/PLAIN.
    security_protocol: str = "PLAINTEXT"
    sasl_mechanism: str | None = None
    sasl_username: str | None = None
    sasl_password: str | None = None

    def client_options(self) -> dict[str, Any]:
        """Connection options shared by producers, consumers and the admin client."""
        options: dict[str, Any] = {
            "bootstrap_servers": self.bootstrap_servers,
            "security_protocol": self.security_protocol,
        }
        if self.security_protocol in {"SSL", "SASL_SSL"}:
            from aiokafka.helpers import create_ssl_context

            options["ssl_context"] = create_ssl_context()
        if self.sasl_mechanism:
            options.update(
                sasl_mechanism=self.sasl_mechanism,
                sasl_plain_username=self.sasl_username,
                sasl_plain_password=self.sasl_password,
            )
        return options


async def _retry(
    what: str, start: Callable[[], Awaitable[None]], attempts: int = 30, delay: float = 2.0
) -> None:
    """Call ``start`` until the broker accepts the connection."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            await start()
            return
        except (KafkaConnectionError, OSError) as exc:
            last = exc
            log.warning("kafka_unavailable", what=what, attempt=attempt, error=str(exc))
            await asyncio.sleep(delay)
    raise RuntimeError(f"Kafka unavailable after {attempts} attempts") from last


async def ensure_topics(settings: KafkaSettings) -> None:
    """Create the SkyRadar topics if they do not exist (idempotent)."""
    admin = AIOKafkaAdminClient(**settings.client_options())
    await _retry("admin", admin.start)
    try:
        existing = set(await admin.list_topics())
        wanted = [
            NewTopic(
                AIRCRAFT_STATES,
                num_partitions=settings.states_partitions,
                replication_factor=1,
                topic_configs={"retention.ms": str(settings.states_retention_ms)},
            ),
            NewTopic(
                AIRCRAFT_SNAPSHOTS,
                num_partitions=1,
                replication_factor=1,
                topic_configs={"retention.ms": str(settings.states_retention_ms)},
            ),
            NewTopic(
                AIRCRAFT_ALERTS,
                num_partitions=1,
                replication_factor=1,
                topic_configs={"retention.ms": str(settings.alerts_retention_ms)},
            ),
        ]
        missing = [t for t in wanted if t.name not in existing]
        if missing:
            try:
                await admin.create_topics(missing)
                log.info("topics_created", topics=[t.name for t in missing])
            except TopicAlreadyExistsError:
                pass
    finally:
        await admin.close()


async def start_producer(settings: KafkaSettings, **kwargs: Any) -> AIOKafkaProducer:
    producer = AIOKafkaProducer(
        **settings.client_options(),
        linger_ms=50,
        compression_type="lz4",
        max_batch_size=256 * 1024,
        **kwargs,
    )
    await _retry("producer", producer.start)
    return producer


async def start_consumer(
    settings: KafkaSettings,
    topics: Iterable[str],
    group_id: str | None,
    **kwargs: Any,
) -> AIOKafkaConsumer:
    consumer = AIOKafkaConsumer(
        *topics,
        **settings.client_options(),
        group_id=group_id,
        **kwargs,
    )
    await _retry("consumer", consumer.start)
    return consumer
