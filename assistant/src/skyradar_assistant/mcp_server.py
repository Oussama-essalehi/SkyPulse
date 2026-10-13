"""MCP server: expose SkyRadar's data to any MCP client (e.g. Claude Desktop).

Tools: live situation (stats, bounding-box count, active alerts), the mart schema and a
guarded read-only SQL tool - the same guardrails as the assistant. Run over stdio:

    docker exec -i skyradar-assistant-1 python -m skyradar_assistant.mcp_server

(see docs/mcp.md for the Claude Desktop configuration).
"""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from skyradar_assistant.agent import TABLE_DOCS, Toolbox
from skyradar_assistant.main import run_safe_query, schema

server = MCPServer(
    name="skyradar",
    instructions=(
        "SkyRadar: real-time global air traffic (OpenSky ADS-B) and its history. Use the live "
        "tools for 'right now' questions and run_sql (read-only DuckDB, SELECT only) on the "
        "marts for history. Departures/arrivals are reconstructed; times are UTC."
    ),
)


def _toolbox() -> Toolbox:
    return Toolbox(run_safe_query)


@server.tool()
def live_stats() -> str:
    """Live worldwide counts: tracked, airborne, on ground, by registration country and altitude."""
    return _toolbox().get_live_stats()


@server.tool()
def count_live_aircraft(lamin: float, lomin: float, lamax: float, lomax: float) -> str:
    """Number of aircraft currently inside a latitude/longitude bounding box (degrees)."""
    return _toolbox().count_live_aircraft(lamin, lomin, lamax, lomax)


@server.tool()
def active_alerts() -> str:
    """Active anomaly alerts (emergency squawks, rapid descents, holdings, signal losses...)."""
    return _toolbox().get_active_alerts()


@server.tool()
def describe_marts() -> str:
    """Tables available to run_sql, with their columns and a short description."""
    return json.dumps(
        {
            table: {"description": TABLE_DOCS.get(table, ""), "columns": columns}
            for table, columns in schema().items()
        },
        ensure_ascii=False,
    )


@server.tool()
def run_sql(query: str) -> str:
    """Run ONE read-only DuckDB SELECT on the marts (allow-listed tables, LIMIT enforced)."""
    return _toolbox().run_sql(query)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
