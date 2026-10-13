"""Assistant service.

``/ask`` runs the agent (``agent.py``: Claude with the Anthropic Tool Runner, or a local
model through Ollama) whose SQL tool goes through ``run_safe_query``: guardrails
(``guardrails.py``) then a sandboxed, read-only DuckDB connection with a timeout.
Without ANTHROPIC_API_KEY / OLLAMA_BASE_URL, ``/ask`` answers 503 and the frontend shows
"assistant disabled".
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import duckdb
import structlog
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from skyradar_assistant.agent import Toolbox, ask_claude
from skyradar_assistant.guardrails import UnsafeQueryError, validate_sql
from skyradar_assistant.ollama import ask_ollama
from skyradar_common.logging import configure_logging

configure_logging("assistant")
log = structlog.get_logger(__name__)

MARTS_ROOT = Path(os.getenv("SKYRADAR_MARTS_ROOT", "/data/marts"))
ALLOWED_TABLES = (
    "fct_flights",
    "fct_traffic_hourly",
    "fct_airport_activity",
    "fct_routes",
    "fct_alerts_history",
    "fct_altitude_profile",
    "fct_callsign_routes",
    "fct_diversions",
    "dim_airports",
    "ml_traffic_forecast",
    "ml_flight_anomaly_scores",
    "ml_model_metrics",
)
QUERY_TIMEOUT_S = float(os.getenv("ASSISTANT_QUERY_TIMEOUT_SECONDS", "10"))


def llm_provider() -> str | None:
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OLLAMA_BASE_URL"):
        return "ollama"
    return None


def read_only_connection() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB exposing allow-listed marts as views, external access off."""
    con = duckdb.connect(":memory:")
    for table in ALLOWED_TABLES:
        path = MARTS_ROOT / f"{table}.parquet"
        if path.exists():
            con.execute(f"create view {table} as select * from read_parquet('{path}')")
    # From here on the connection can only read the marts directory.
    con.execute("set allowed_directories = $dirs", {"dirs": [str(MARTS_ROOT)]})
    con.execute("set enable_external_access = false")
    con.execute("set lock_configuration = true")
    return con


def run_safe_query(sql: str, max_rows: int = 200) -> tuple[str, list[str], list[list[Any]]]:
    safe_sql = validate_sql(sql, ALLOWED_TABLES, max_limit=max_rows)
    con = read_only_connection()
    # Timeout: interrupt the query from a timer thread.
    timer = threading.Timer(QUERY_TIMEOUT_S, con.interrupt)
    timer.start()
    try:
        cursor = con.execute(safe_sql)
        columns = [d[0] for d in cursor.description or []]
        rows = [list(r) for r in cursor.fetchmany(max_rows)]
    finally:
        timer.cancel()
        con.close()
    return safe_sql, columns, rows


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


app = FastAPI(title="SkyRadar assistant", version="0.1.0")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "llm": llm_provider(), "marts_root": str(MARTS_ROOT)}


@app.get("/schema")
def schema() -> dict[str, list[dict[str, str]]]:
    con = read_only_connection()
    try:
        tables = {r[0] for r in con.execute("show tables").fetchall()}
        return {
            table: [
                {"name": c[0], "type": c[1]} for c in con.execute(f"describe {table}").fetchall()
            ]
            for table in ALLOWED_TABLES
            if table in tables
        }
    finally:
        con.close()


@app.post("/ask")
def ask(request: AskRequest) -> dict[str, Any]:
    provider = llm_provider()
    if provider is None:
        raise HTTPException(503, "assistant disabled: set ANTHROPIC_API_KEY or OLLAMA_BASE_URL")
    toolbox = Toolbox(run_safe_query)
    try:
        if provider == "anthropic":
            return ask_claude(request.question, toolbox, schema())
        return ask_ollama(request.question, toolbox, schema())
    except Exception as exc:
        log.warning("assistant_failed", provider=provider, error=str(exc))
        raise HTTPException(502, f"assistant error ({provider}): {exc}") from exc


@app.post("/sql")
def sql(body: dict[str, str]) -> dict[str, Any]:
    """Run a guarded query directly (used by tests and the evaluation harness)."""
    try:
        safe_sql, columns, rows = run_safe_query(body.get("sql", ""))
    except UnsafeQueryError as exc:
        raise HTTPException(400, str(exc)) from exc
    except duckdb.Error as exc:
        raise HTTPException(400, f"query failed: {exc}") from exc
    return {"sql": safe_sql, "columns": columns, "rows": rows}
