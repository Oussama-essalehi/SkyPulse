"""Structured logging configuration shared by every service."""

from __future__ import annotations

import logging
import os
import sys

import structlog


def configure_logging(service: str, level: str | None = None, fmt: str | None = None) -> None:
    """Configure structlog + stdlib logging.

    ``LOG_FORMAT=json`` (default in containers) emits one JSON object per line,
    ``LOG_FORMAT=console`` renders human friendly colored output.
    """
    level_name = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    fmt = (fmt or os.getenv("LOG_FORMAT") or "json").lower()

    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer() if fmt == "console" else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level_name)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(
        level=level_name, stream=sys.stdout, format="%(levelname)s %(name)s %(message)s"
    )
    # Kafka client libraries are chatty at INFO level.
    logging.getLogger("aiokafka").setLevel(logging.WARNING)
    structlog.contextvars.bind_contextvars(service=service)
