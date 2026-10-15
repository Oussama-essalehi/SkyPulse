SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE ?= docker compose
UV ?= uv
RUN := $(UV) run --no-sync
TEST_LAKE := data/test_lake

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.env:
	@cp .env.example .env && echo "Created .env from .env.example"

# ------------------------------------------------------------------ stack
.PHONY: up up-demo down logs ps build replay record restart clean
up: .env seed ## Start the full stack (live map, lake, Dagster, Superset, MLflow, monitoring)
	$(COMPOSE) --profile full up -d --build --wait
	@$(MAKE) --no-print-directory urls

up-demo: .env ## Start the light demo (Redpanda, ingestion, anomaly detector, API, map)
	$(COMPOSE) --profile demo up -d --build --wait
	@echo "Map: http://localhost:3000  |  API: http://localhost:8000/docs"

replay: .env ## Demo in replay mode (no OpenSky call; uses data/replay/replay.parquet or synthetic data)
	SOURCE=replay $(COMPOSE) --profile demo up -d --build --wait
	@echo "Replay running -> http://localhost:3000"

record: .env ## Record 30 min of real OpenSky data into data/replay/replay.parquet
	set -a; source .env; set +a; $(RUN) python scripts/record_replay.py --minutes $${MINUTES:-30}

down: ## Stop every service (data volumes are kept)
	$(COMPOSE) --profile full --profile demo down

clean: ## Stop and delete all volumes (lake, warehouse, metadata)
	$(COMPOSE) --profile full --profile demo down -v

logs: ## Follow logs (S=service to filter)
	$(COMPOSE) --profile full --profile demo logs -f --tail=100 $(S)

ps: ## Service status
	$(COMPOSE) --profile full --profile demo ps

build: ## Build all images
	$(COMPOSE) --profile full build

urls:
	@echo ""
	@echo "  Map ............. http://localhost:3000"
	@echo "  API docs ........ http://localhost:8000/docs"
	@echo "  Dagster ......... http://localhost:3001"
	@echo "  Superset ........ http://localhost:8088  (admin / see .env)"
	@echo "  MLflow .......... http://localhost:5000"
	@echo "  Grafana ......... http://localhost:3002  (supervision ; admin / see .env)"
	@echo "  Prometheus ...... http://localhost:9090"
	@echo "  Redpanda console  http://localhost:8081"
	@echo "  Lake (filer) .... http://localhost:8888/buckets/"

# ------------------------------------------------------------------- data
.PHONY: seed synthetic test-lake
seed: ## Download reference data (airports, OpenSky aircraft DB, airlines) into data/reference
	@python3 scripts/seed_reference_data.py

synthetic: ## Generate a synthetic replay file (offline demo)
	$(RUN) python scripts/generate_synthetic_replay.py --output data/replay/replay.parquet

test-lake: ## Build a deterministic local lake for dbt tests
	$(RUN) python scripts/build_test_lake.py --root $(TEST_LAKE)

# ------------------------------------------------------------------- dev
.PHONY: install test lint format typecheck dbt-parse dbt-build smoke test-ui test-monitoring front-dev
install: ## Install Python (uv) and frontend (npm) dependencies + pre-commit hooks
	$(UV) sync --all-packages
	cd frontend && npm ci
	$(RUN) pre-commit install || true

test: ## Unit tests (Python + frontend)
	$(RUN) pytest
	cd frontend && npm test

lint: ## Linters and type checks
	$(RUN) ruff check .
	$(RUN) ruff format --check .
	$(RUN) mypy libs/common/src ingestion/src api/src streaming/src ml/src
	cd frontend && npm run lint && npm run typecheck

MONITORING = docker run --rm -v $(CURDIR)/infra/monitoring:/etc/prometheus:ro -w /etc/prometheus
test-monitoring: ## Check the Prometheus / Alertmanager configs and unit-test the alert rules
	$(MONITORING) --entrypoint promtool prom/prometheus:v3.5.0 check config prometheus.yml
	$(MONITORING) --entrypoint promtool prom/prometheus:v3.5.0 test rules rules.test.yml
	$(MONITORING) --entrypoint amtool prom/alertmanager:v0.28.1 check-config alertmanager.yml

format: ## Auto-format code
	$(RUN) ruff check --fix .
	$(RUN) ruff format .
	cd frontend && npm run format

DBT_ENV = cd pipeline/dbt && DBT_PROFILES_DIR=. SKYRADAR_LAKE_ROOT=$(CURDIR)/$(TEST_LAKE) \
	SKYRADAR_REFERENCE_ROOT=$(CURDIR)/$(TEST_LAKE)/reference DBT_DUCKDB_PATH=target/test.duckdb \
	SKYRADAR_MARTS_ROOT=target/marts

dbt-parse: ## Compile the dbt manifest (needed by Dagster)
	cd pipeline/dbt && DBT_PROFILES_DIR=. $(UV) run --no-sync dbt parse

dbt-build: test-lake ## dbt build (models + tests) on the test lake
	mkdir -p pipeline/dbt/target/marts
	$(DBT_ENV) $(UV) run --no-sync dbt build

smoke: ## End-to-end smoke test against a running stack (make up-demo first)
	$(RUN) pytest -m integration tests -v

test-ui: ## Browser tests (Playwright) against a running stack (make up-demo first)
	cd frontend && SKYRADAR_URL=$${SKYRADAR_URL:-http://localhost:3000} npm run test:e2e

front-dev: ## Frontend dev server (proxies /api to localhost:8000)
	cd frontend && npm run dev
