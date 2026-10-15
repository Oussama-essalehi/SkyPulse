# syntax=docker/dockerfile:1.7
# Build context: repository root (uv workspace). Multi-stage: deps are cached
# independently from the source code, the runtime image only holds the venv.
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY libs/common/pyproject.toml libs/common/
COPY ingestion/pyproject.toml ingestion/
COPY api/pyproject.toml api/
COPY streaming/pyproject.toml streaming/
COPY ml/pyproject.toml ml/
COPY pipeline/dagster/pyproject.toml pipeline/dagster/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package skyradar-streaming
COPY libs/common libs/common
COPY streaming streaming
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package skyradar-streaming

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 skyradar
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
USER skyradar
WORKDIR /home/skyradar
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/metrics' % os.getenv('METRICS_PORT', '9101'), timeout=4)" || exit 1
CMD ["python", "-m", "skyradar_streaming.parquet_sink.main"]
