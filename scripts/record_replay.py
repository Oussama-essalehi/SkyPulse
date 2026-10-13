"""Record N minutes of real OpenSky data into a Parquet replay file.

Usage (credentials are read from the environment / .env):

    uv run python scripts/record_replay.py --minutes 30 --output data/replay/replay.parquet

The polling interval defaults to the quota-safe value; ``--interval`` lets you
record a denser short sample (the script prints the credit cost up front).
"""

from __future__ import annotations

import argparse
import asyncio
import math
import time
from pathlib import Path

import pyarrow.parquet as pq

from skyradar_common.logging import configure_logging
from skyradar_common.models import StateVector
from skyradar_common.parquet import states_to_table
from skyradar_ingestion.config import IngestionSettings
from skyradar_ingestion.quota import AccountTier, BoundingBox, credits_per_call, safe_poll_interval
from skyradar_ingestion.sources.opensky import OpenSkySource


async def record(minutes: float, output: Path, interval: float | None) -> None:
    settings = IngestionSettings()
    bbox = BoundingBox.parse(settings.opensky_bbox) if settings.opensky_bbox else None
    tier = AccountTier.STANDARD if settings.has_credentials else AccountTier.ANONYMOUS
    interval = interval or safe_poll_interval(tier, bbox)
    calls = max(1, math.floor(minutes * 60 / interval) + 1)
    cost = calls * credits_per_call(bbox)
    print(f"Recording {calls} snapshots every {interval}s ({tier.value}) -> {cost} credits")

    source = OpenSkySource(
        interval,
        client_id=settings.opensky_client_id,
        client_secret=settings.opensky_client_secret,
        bbox=bbox,
    )
    states: list[StateVector] = []
    try:
        for i in range(calls):
            started = time.monotonic()
            snapshot = await source.fetch()
            states.extend(snapshot.states)
            print(
                f"[{i + 1}/{calls}] {len(snapshot.states)} aircraft, "
                f"credits remaining: {snapshot.credits_remaining}"
            )
            if i + 1 < calls:
                await asyncio.sleep(max(0.0, interval - (time.monotonic() - started)))
    finally:
        await source.aclose()
    output.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(states_to_table(states), output, compression="zstd")
    print(f"Wrote {len(states)} rows to {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--interval", type=float, default=None, help="seconds between calls")
    parser.add_argument("--output", type=Path, default=Path("data/replay/replay.parquet"))
    args = parser.parse_args()
    configure_logging("record-replay", fmt="console")
    asyncio.run(record(args.minutes, args.output, args.interval))


if __name__ == "__main__":
    main()
