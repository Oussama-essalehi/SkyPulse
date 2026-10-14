"""Compaction of the raw lake: many small Parquet files per hour -> one file.

The sink writes one file per partition every few minutes (12+ files per hour), which
slows down every scan. Closed partitions (older than ``min_age_hours``) with more than
one file are rewritten as a single, de-duplicated, zstd file, then the originals are
deleted. The new file is written *before* the originals are removed: a crash in
between leaves duplicates (removed by ``stg_aircraft_states``), never data loss, and
the next run finishes the job.
"""

import hashlib
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pyarrow import fs as pafs

from skyradar_common.storage import S3Settings, s3_filesystem
from skyradar_orchestration.quality import lake_connection, lake_root

DATASETS = ("raw/aircraft_states", "raw/alerts")


@dataclass
class CompactionReport:
    partitions: int = 0
    files_before: int = 0
    files_after: int = 0
    rows: int = 0
    details: list[str] = field(default_factory=list)


def filesystem_and_base() -> tuple[pafs.FileSystem, str]:
    root = lake_root()
    if root.startswith("s3://"):
        return s3_filesystem(S3Settings()), root.removeprefix("s3://")
    return pafs.LocalFileSystem(), root


def partition_time(directory: str) -> datetime:
    date = directory.split("date=")[1].split("/")[0]
    hour = int(directory.split("hour=")[1].split("/")[0])
    return datetime.strptime(date, "%Y-%m-%d").replace(hour=hour, tzinfo=UTC)


def compact(min_age_hours: float = 2.0, now: float | None = None) -> CompactionReport:
    now = now or time.time()
    filesystem, base = filesystem_and_base()
    prefix = lake_root().rstrip("/")
    con = lake_connection()
    report = CompactionReport()
    for dataset in DATASETS:
        selector = pafs.FileSelector(f"{base}/{dataset}", recursive=True, allow_not_found=True)
        groups: dict[str, list[str]] = defaultdict(list)
        for info in filesystem.get_file_info(selector):
            if info.type == pafs.FileType.File and info.path.endswith(".parquet"):
                groups[info.path.rsplit("/", 1)[0]].append(info.path)
        for directory, files in sorted(groups.items()):
            closed = (now - partition_time(directory).timestamp()) / 3600 >= min_age_hours + 1
            if len(files) < 2 or not closed:
                continue
            files.sort()
            digest = hashlib.sha1("|".join(files).encode()).hexdigest()[:16]
            target = f"{directory}/compacted-{digest}.parquet"
            sources = ", ".join(
                "'" + (prefix + f[len(base) :]).replace("'", "''") + "'" for f in files
            )
            out = prefix + target[len(base) :]
            # DISTINCT: identical rows written twice by an at-least-once replay disappear.
            con.execute(
                f"copy (select distinct * from read_parquet([{sources}], union_by_name = true, "
                "hive_partitioning = false)) "
                f"to '{out}' (format parquet, compression zstd)"
            )
            rows = con.execute(f"select count(*) from read_parquet('{out}')").fetchone()
            for path in files:
                filesystem.delete_file(path)
            report.partitions += 1
            report.files_before += len(files)
            report.files_after += 1
            report.rows += int(rows[0]) if rows else 0
            report.details.append(f"{directory}: {len(files)} -> 1")
    return report


def compaction_min_age_hours() -> float:
    return float(os.getenv("LAKE_COMPACTION_MIN_AGE_HOURS", "2"))
