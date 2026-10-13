"""Ingestion settings (environment variables, see ``.env.example``)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestionSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    source: Literal["opensky", "replay"] = Field("opensky", alias="SOURCE")

    # Polling: "auto" derives the interval from the credit quota.
    poll_interval_seconds: str = Field("auto", alias="POLL_INTERVAL_SECONDS")

    opensky_client_id: str | None = Field(None, alias="OPENSKY_CLIENT_ID")
    opensky_client_secret: str | None = Field(None, alias="OPENSKY_CLIENT_SECRET")
    opensky_api_url: str = Field("https://opensky-network.org/api", alias="OPENSKY_API_URL")
    opensky_token_url: str = Field(
        "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token",
        alias="OPENSKY_TOKEN_URL",
    )
    opensky_bbox: str | None = Field(None, alias="OPENSKY_BBOX")
    opensky_daily_credits: int | None = Field(None, alias="OPENSKY_DAILY_CREDITS")
    opensky_feeder: bool = Field(False, alias="OPENSKY_FEEDER")
    opensky_quota_margin: float = Field(0.10, alias="OPENSKY_QUOTA_MARGIN")

    replay_path: str = Field("/data/replay/replay.parquet", alias="REPLAY_PATH")
    replay_speed: float = Field(1.0, alias="REPLAY_SPEED")
    replay_loop: bool = Field(True, alias="REPLAY_LOOP")
    # Generate a synthetic replay when REPLAY_PATH does not exist (offline demo / CI).
    replay_synthetic_fallback: bool = Field(True, alias="REPLAY_SYNTHETIC_FALLBACK")

    metrics_port: int = Field(9100, alias="INGESTION_METRICS_PORT")

    @property
    def has_credentials(self) -> bool:
        return bool(self.opensky_client_id and self.opensky_client_secret)
