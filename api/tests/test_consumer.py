import asyncio

import pytest

from skyradar_api.consumer import LiveConsumer
from skyradar_api.state import LiveState
from skyradar_common.kafka import KafkaSettings


async def test_consumer_is_restarted_after_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kafka down at start-up (retries exhausted), then up: the API must recover."""
    consumer = LiveConsumer(
        LiveState(), KafkaSettings(), lookback_seconds=0, alerts_lookback_seconds=0
    )
    calls = 0
    started = asyncio.Event()

    async def run() -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("Kafka unavailable after 30 attempts")
        started.set()
        await asyncio.Event().wait()  # consuming forever

    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def fast_sleep(delay: float) -> None:
        delays.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(consumer, "run", run)
    monkeypatch.setattr("skyradar_api.consumer.asyncio.sleep", fast_sleep)
    task = asyncio.create_task(consumer.supervise())
    await asyncio.wait_for(started.wait(), timeout=2)
    assert calls == 3
    assert delays == [2.0, 4.0]  # exponential backoff
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
