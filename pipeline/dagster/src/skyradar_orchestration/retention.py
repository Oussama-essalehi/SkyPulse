"""Retention of the raw lake: hourly partitions older than N days are deleted.

The raw state vectors are by far the largest dataset (~1 GB/day with the world feed).
Once loaded into the warehouse (``stg_aircraft_states`` is incremental and keeps its own
``warehouse_history_days`` of history) they are only needed to rebuild it, so the lake
keeps ``LAKE_RETENTION_DAYS`` (30 by default; 0 keeps everything). Alerts and upper
winds are tiny and are kept for a year: marts read them straight from the lake.

The cut-off is anchored on the *newest* partition of each dataset, not on the clock: a
stack that was stopped for weeks, or a lake filled by the replay of an old capture,
never wakes up to an empty lake.
"""

import os
from dataclasses import dataclass, field
from datetime import timedelta

from pyarrow import fs as pafs

from skyradar_orchestration.compaction import filesystem_and_base, partition_time

LONG_RETENTION_DAYS = 365


def retention_days() -> dict[str, int]:
    return {
        "raw/aircraft_states": int(os.getenv("LAKE_RETENTION_DAYS", "30")),
        "raw/alerts": LONG_RETENTION_DAYS,
        "raw/upper_winds": LONG_RETENTION_DAYS,
    }


@dataclass
class RetentionReport:
    partitions_deleted: int = 0
    files_deleted: int = 0
    bytes_deleted: int = 0
    details: list[str] = field(default_factory=list)


def purge(days: dict[str, int] | None = None) -> RetentionReport:
    days = retention_days() if days is None else days
    filesystem, base = filesystem_and_base()
    report = RetentionReport()
    for dataset, keep_days in days.items():
        if keep_days <= 0:
            continue
        selector = pafs.FileSelector(f"{base}/{dataset}", recursive=True, allow_not_found=True)
        partitions: dict[str, list[pafs.FileInfo]] = {}
        for info in filesystem.get_file_info(selector):
            if info.type == pafs.FileType.File and "/hour=" in info.path:
                partitions.setdefault(info.path.rsplit("/", 1)[0], []).append(info)
        if not partitions:
            continue
        newest = max(partition_time(directory) for directory in partitions)
        cutoff = newest - timedelta(days=keep_days)
        expired = sorted(d for d in partitions if partition_time(d) < cutoff)
        for directory in expired:
            files = partitions[directory]
            filesystem.delete_dir(directory)
            report.partitions_deleted += 1
            report.files_deleted += len(files)
            report.bytes_deleted += sum(f.size or 0 for f in files)
        # Day folders left empty (local lake only: S3 has no empty "directories").
        local = isinstance(filesystem, pafs.LocalFileSystem)
        for day in sorted({d.rsplit("/", 1)[0] for d in expired} if local else set()):
            remaining = filesystem.get_file_info(pafs.FileSelector(day, allow_not_found=True))
            if not remaining:
                filesystem.delete_dir(day)
        if expired:
            report.details.append(
                f"{dataset}: {len(expired)} partitions before {cutoff:%Y-%m-%d %H:00} deleted"
            )
    return report
