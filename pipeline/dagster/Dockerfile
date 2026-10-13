# syntax=docker/dockerfile:1.7
# Dagster webserver + daemon image (same image, different command), with the dbt
# project baked in and its manifest pre-compiled.
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
COPY assistant/pyproject.toml assistant/
COPY pipeline/dagster/pyproject.toml pipeline/dagster/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package skyradar-orchestration
COPY libs/common libs/common
COPY ml ml
COPY pipeline/dagster pipeline/dagster
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package skyradar-orchestration

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 skyradar \
    && mkdir -p /opt/dagster/home /data/warehouse /data/marts \
    && chown -R skyradar /opt/dagster /data
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 \
    DAGSTER_HOME=/opt/dagster/home DBT_PROJECT_DIR=/opt/skyradar/dbt \
    DBT_PROFILES_DIR=/opt/skyradar/dbt DBT_DUCKDB_PATH=/data/warehouse/skyradar.duckdb \
    SKYRADAR_MARTS_ROOT=/data/marts DATA_QUALITY_DIR=/opt/skyradar/data_quality/contracts
COPY --chown=skyradar pipeline/dbt /opt/skyradar/dbt
COPY --chown=skyradar data_quality /opt/skyradar/data_quality
COPY --chown=skyradar pipeline/dagster/dagster.yaml pipeline/dagster/workspace.yaml /opt/dagster/home/
USER skyradar
# Pre-install DuckDB extensions (no download at run time) and compile the manifest.
RUN python -c "import duckdb; duckdb.sql('INSTALL httpfs; INSTALL spatial')" \
    && cd /opt/skyradar/dbt && dbt parse --quiet
WORKDIR /opt/dagster/home
EXPOSE 3000
HEALTHCHECK --interval=20s --timeout=5s --start-period=60s --retries=6 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/server_info', timeout=4)" || exit 1
CMD ["dagster-webserver", "-h", "0.0.0.0", "-p", "3000", "-w", "/opt/dagster/home/workspace.yaml"]
