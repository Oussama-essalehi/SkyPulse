"""Read-only access to the dbt marts (Parquet files) through in-memory DuckDB."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pandas as pd

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


def read_mart(name: str, root: Path | None = None) -> pd.DataFrame | None:
    """Load one mart as a DataFrame, or None when it has not been built yet."""
    path = (root or marts_root()) / f"{name}.parquet"
    if not path.exists():
        return None
    return duckdb.sql(f"select * from read_parquet('{path}')").df()


def write_parquet_atomic(df: pd.DataFrame, path: Path) -> Path:
    """Write then rename: readers (Superset, API) never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    con = duckdb.connect(":memory:")
    con.register("frame", df)
    con.execute(f"copy frame to '{tmp}' (format parquet)")
    os.replace(tmp, path)
    return path


def append_model_metrics(rows: list[dict[str, object]], root: Path | None = None) -> None:
    """Append evaluation results to the ``ml_model_metrics`` history (dashboard input)."""
    if not rows:
        return
    root = root or marts_root()
    new = pd.DataFrame(rows)
    existing = read_mart("ml_model_metrics", root)
    frame = new if existing is None else pd.concat([existing, new], ignore_index=True)
    write_parquet_atomic(frame, root / "ml_model_metrics.parquet")
