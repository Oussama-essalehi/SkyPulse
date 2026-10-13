"""Read-only access to the dbt marts (Parquet files) through in-memory DuckDB."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb

DEFAULT_MARTS_ROOT = "/data/marts"


def marts_root() -> Path:
    return Path(os.getenv("SKYRADAR_MARTS_ROOT", DEFAULT_MARTS_ROOT))


def connect(root: Path | None = None) -> duckdb.DuckDBPyConnection:
    """In-memory connection exposing every mart as a view (never opens dbt's database)."""
    root = root or marts_root()
    con = duckdb.connect(":memory:")
    for path in sorted(root.glob("*.parquet")):
        con.execute(f"create view {path.stem} as select * from read_parquet('{path}')")
    return con
