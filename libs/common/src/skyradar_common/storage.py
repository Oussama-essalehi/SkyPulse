"""S3-compatible object storage access (SeaweedFS locally, any S3 in the cloud).

Requires the ``storage`` extra (pyarrow).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from pyarrow import fs as pafs

log = structlog.get_logger(__name__)


class S3Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="S3_", extra="ignore")

    endpoint_url: str = "http://localhost:8333"
    access_key: str = "skyradar"
    secret_key: str = "skyradar-secret"
    region: str = "us-east-1"
    bucket: str = "skyradar-lake"

    @property
    def scheme(self) -> str:
        return urlparse(self.endpoint_url).scheme or "http"

    @property
    def host_port(self) -> str:
        parsed = urlparse(self.endpoint_url)
        return parsed.netloc or parsed.path


def s3_filesystem(settings: S3Settings) -> pafs.S3FileSystem:
    from pyarrow import fs as pafs

    return pafs.S3FileSystem(
        access_key=settings.access_key,
        secret_key=settings.secret_key,
        endpoint_override=settings.host_port,
        scheme=settings.scheme,
        region=settings.region,
        allow_bucket_creation=True,
    )


def ensure_bucket(settings: S3Settings, filesystem: pafs.S3FileSystem | None = None) -> None:
    from pyarrow import fs as pafs

    filesystem = filesystem or s3_filesystem(settings)
    info = filesystem.get_file_info(settings.bucket)
    if info.type == pafs.FileType.NotFound:
        filesystem.create_dir(settings.bucket)
        log.info("bucket_created", bucket=settings.bucket)


def main() -> None:
    """``python -m skyradar_common.storage bucket [bucket ...]``: create buckets (idempotent)."""
    import sys

    settings = S3Settings()
    filesystem = s3_filesystem(settings)
    for bucket in sys.argv[1:] or [settings.bucket]:
        ensure_bucket(settings.model_copy(update={"bucket": bucket}), filesystem)
        print(f"bucket ready: {bucket}")


if __name__ == "__main__":
    main()
