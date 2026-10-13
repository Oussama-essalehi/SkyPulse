import time
from pathlib import Path

import pyarrow.parquet as pq

from skyradar_ingestion.sources.replay import ReplaySource
from skyradar_ingestion.synthetic import generate


def test_synthetic_generator_schema_and_scenarios(tmp_path: Path) -> None:
    path = generate(tmp_path / "synthetic.parquet", flights=50, duration_s=600, interval_s=30)
    table = pq.read_table(path)
    assert table.num_rows > 50
    squawks = set(table.column("squawk").to_pylist())
    assert {"7700", "7600"} <= squawks
    assert table.column("snapshot_time").unique().to_pylist() == sorted(
        table.column("snapshot_time").unique().to_pylist()
    )


async def test_replay_source_rebases_time(tmp_path: Path) -> None:
    path = generate(
        tmp_path / "r.parquet", flights=20, duration_s=60, interval_s=10, start_time=1_000
    )
    source = ReplaySource(path, speed=1000, loop=False)
    assert source.poll_interval_seconds == 10 / 1000
    snapshots = [s async for s in source.stream()]
    assert len(snapshots) == 7
    now = time.time()
    for snap in snapshots:
        assert abs(snap.snapshot_time - now) < 60
        assert snap.states
        assert all(s.source == "replay" for s in snap.states)
        assert all(s.snapshot_time == snap.snapshot_time for s in snap.states)
        assert all(abs(s.ingested_at - now) < 60 for s in snap.states)
