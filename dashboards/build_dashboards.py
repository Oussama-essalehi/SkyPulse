"""Build the SkyRadar Superset dashboard through the REST API, then export it.

Dashboards are "as code": this script (re)creates the DuckDB database, the
virtual datasets over the mart Parquet files, the charts and the dashboard,
then exports the bundle as YAML into ``dashboards/skyradar/``. That folder is
versioned and imported automatically when the Superset container starts.

    uv run python dashboards/build_dashboards.py --url http://localhost:8088

Requires a running Superset (``make up``).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any

import httpx

DATABASE_NAME = "SkyRadar marts (DuckDB)"
DASHBOARD_TITLE = "SkyRadar — Trafic aérien"
DASHBOARD_SLUG = "skyradar"
MARTS = "/data/marts"
EXPORT_DIR = Path(__file__).parent / "skyradar"

DATASETS: dict[str, str | None] = {
    # dataset name -> main datetime column
    "fct_traffic_hourly": "hour",
    "fct_flights": "departure_time",
    "fct_airport_activity": "hour",
    "fct_routes": None,
    "fct_alerts_history": "started_at",
    "fct_altitude_profile": "day",
}


def metric(sql: str, label: str) -> dict[str, Any]:
    return {"expressionType": "SQL", "sqlExpression": sql, "label": label}


AIRBORNE = metric("SUM(airborne_aircraft)", "Avions en vol")
AIRCRAFT_HOURS = metric("SUM(aircraft)", "Avions-heures")
MOVEMENTS = metric("SUM(movements)", "Mouvements")
DEPARTURES = metric("SUM(departures)", "Départs")
ARRIVALS = metric("SUM(arrivals)", "Arrivées")
ALERTS = metric("COUNT(*)", "Alertes")
POSITIONS = metric("SUM(positions)", "Positions")
FLIGHTS = metric("COUNT(*)", "Vols reconstitués")
COMPLETE = metric("AVG(CASE WHEN is_complete THEN 1.0 ELSE 0 END)", "Vols complets (%)")

# (key, dataset, title, params, width, height)
CHARTS: list[tuple[str, str, str, dict[str, Any], int, int]] = [
    (
        "flights",
        "fct_flights",
        "Vols reconstitués",
        {
            "viz_type": "big_number_total",
            "metric": FLIGHTS,
            "subheader": "départ et/ou arrivée identifiés",
            "y_axis_format": ",d",
        },
        3,
        30,
    ),
    (
        "complete",
        "fct_flights",
        "Part de vols complets",
        {
            "viz_type": "big_number_total",
            "metric": COMPLETE,
            "subheader": "départ ET arrivée identifiés",
            "y_axis_format": ".1%",
        },
        3,
        30,
    ),
    (
        "alerts_total",
        "fct_alerts_history",
        "Alertes détectées",
        {
            "viz_type": "big_number_total",
            "metric": ALERTS,
            "y_axis_format": ",d",
            "subheader": "squawk, descente rapide, attente…",
        },
        3,
        30,
    ),
    (
        "alerts_by_type",
        "fct_alerts_history",
        "Alertes par type",
        {
            "viz_type": "pie",
            "groupby": ["alert_type"],
            "metric": ALERTS,
            "donut": True,
            "show_labels": True,
            "label_type": "key_percent",
            "row_limit": 20,
            "show_legend": True,
            "legendOrientation": "right",
        },
        3,
        30,
    ),
    (
        "traffic",
        "fct_traffic_hourly",
        "Trafic mondial heure par heure (par zone)",
        {
            "viz_type": "echarts_area",
            "x_axis": "hour",
            "time_grain_sqla": "PT1H",
            "metrics": [AIRBORNE],
            "groupby": ["zone"],
            "stack": "Stack",
            "row_limit": 50_000,
            "rich_tooltip": True,
            "show_legend": True,
            "y_axis_format": ",d",
            "time_range": "No filter",
        },
        12,
        50,
    ),
    (
        "countries",
        "fct_traffic_hourly",
        "Top pays d'immatriculation",
        {
            "viz_type": "echarts_timeseries_bar",
            "x_axis": "origin_country",
            "metrics": [AIRCRAFT_HOURS],
            "groupby": [],
            "orientation": "horizontal",
            "row_limit": 15,
            "x_axis_sort": "Avions-heures",
            "x_axis_sort_asc": False,
            "y_axis_format": ",d",
            "show_legend": False,
            "time_range": "No filter",
        },
        6,
        50,
    ),
    (
        "altitudes",
        "fct_altitude_profile",
        "Distribution des altitudes (positions en vol)",
        {
            "viz_type": "echarts_timeseries_bar",
            "x_axis": "altitude_band_m",
            "metrics": [POSITIONS],
            "groupby": [],
            "row_limit": 50,
            "x_axis_sort": "altitude_band_m",
            "x_axis_sort_asc": True,
            "x_axis_title": "Altitude (m, tranches de 1000 m)",
            "y_axis_format": ",d",
            "show_legend": False,
            "time_range": "No filter",
        },
        6,
        50,
    ),
    (
        "airports",
        "fct_airport_activity",
        "Aéroports les plus actifs",
        {
            "viz_type": "table",
            "query_mode": "aggregate",
            "groupby": ["airport_icao", "airport_name", "iso_country"],
            "metrics": [MOVEMENTS, DEPARTURES, ARRIVALS],
            "timeseries_limit_metric": MOVEMENTS,
            "order_desc": True,
            "row_limit": 20,
            "time_range": "No filter",
        },
        6,
        60,
    ),
    (
        "routes",
        "fct_routes",
        "Routes les plus fréquentes",
        {
            "viz_type": "table",
            "query_mode": "raw",
            "all_columns": [
                "route",
                "departure_airport_name",
                "arrival_airport_name",
                "flights",
                "avg_duration_min",
                "avg_distance_km",
            ],
            "order_by_cols": [json.dumps(["flights", False])],
            "row_limit": 20,
        },
        6,
        60,
    ),
]


class Superset:
    def __init__(self, url: str, user: str, password: str) -> None:
        self.client = httpx.Client(base_url=url.rstrip("/"), timeout=60)
        token = (
            self.client.post(
                "/api/v1/security/login",
                json={"username": user, "password": password, "provider": "db", "refresh": True},
            )
            .raise_for_status()
            .json()["access_token"]
        )
        self.client.headers["Authorization"] = f"Bearer {token}"
        csrf = self.client.get("/api/v1/security/csrf_token/").raise_for_status().json()["result"]
        self.client.headers["X-CSRFToken"] = csrf
        self.client.headers["Referer"] = url

    def find(self, resource: str, column: str, value: str) -> list[int]:
        escaped = value.replace("!", "!!").replace("'", "!'")  # rison string escaping
        query = f"(filters:!((col:{column},opr:eq,value:'{escaped}')))"
        result = self.client.get(f"/api/v1/{resource}/", params={"q": query}).raise_for_status()
        return [r["id"] for r in result.json()["result"]]

    def delete(self, resource: str, ids: list[int]) -> None:
        for rid in ids:
            self.client.delete(f"/api/v1/{resource}/{rid}").raise_for_status()

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post(path, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f"{path}: {response.status_code} {response.text[:500]}")
        return dict(response.json())


def build(api: Superset) -> int:
    # Clean previous objects (idempotent rebuild).
    api.delete("dashboard", api.find("dashboard", "slug", DASHBOARD_SLUG))
    for _, _, title, *_ in CHARTS:
        api.delete("chart", api.find("chart", "slice_name", title))
    for name in DATASETS:
        api.delete("dataset", api.find("dataset", "table_name", name))
    api.delete("database", api.find("database", "database_name", DATABASE_NAME))

    database_id = api.post(
        "/api/v1/database/",
        {
            "database_name": DATABASE_NAME,
            # In-memory DuckDB: every query reads the mart Parquet files, the dbt
            # database file is never opened (single-writer constraint).
            "sqlalchemy_uri": "duckdb:///:memory:",
            "expose_in_sqllab": True,
            "allow_run_async": False,
        },
    )["id"]

    dataset_ids: dict[str, int] = {}
    for name, dttm in DATASETS.items():
        dataset_id = api.post(
            "/api/v1/dataset/",
            {
                "database": database_id,
                "schema": "main",
                "table_name": name,
                "sql": f"select * from read_parquet('{MARTS}/{name}.parquet')",
            },
        )["id"]
        api.client.put(f"/api/v1/dataset/{dataset_id}/refresh").raise_for_status()
        if dttm:
            api.client.put(
                f"/api/v1/dataset/{dataset_id}", json={"main_dttm_col": dttm}
            ).raise_for_status()
        dataset_ids[name] = dataset_id

    chart_ids: dict[str, tuple[int, str, int, int]] = {}
    chart_uuids: dict[int, str] = {}
    for key, dataset, title, params, width, height in CHARTS:
        params = {**params, "datasource": f"{dataset_ids[dataset]}__table"}
        chart_id = api.post(
            "/api/v1/chart/",
            {
                "slice_name": title,
                "viz_type": params["viz_type"],
                "datasource_id": dataset_ids[dataset],
                "datasource_type": "table",
                "params": json.dumps(params),
            },
        )["id"]
        chart_ids[key] = (chart_id, title, width, height)
        # The layout must reference charts by uuid: ids change on import.
        chart_uuids[chart_id] = api.client.get(
            f"/api/v1/chart/{chart_id}"
        ).raise_for_status().json()["result"].get("uuid") or _uuid_from_export(api, chart_id)

    position = layout(chart_ids, chart_uuids)
    dashboard_id = api.post(
        "/api/v1/dashboard/",
        {
            "dashboard_title": DASHBOARD_TITLE,
            "slug": DASHBOARD_SLUG,
            "published": True,
            "position_json": json.dumps(position),
            "json_metadata": json.dumps(
                {"color_scheme": "supersetColors", "refresh_frequency": 300}
            ),
        },
    )["id"]
    for chart_id, *_ in chart_ids.values():
        api.client.put(
            f"/api/v1/chart/{chart_id}", json={"dashboards": [dashboard_id]}
        ).raise_for_status()
    return int(dashboard_id)


def _uuid_from_export(api: Superset, chart_id: int) -> str:
    """The chart GET endpoint does not always expose the uuid: read it from an export."""
    response = api.client.get("/api/v1/chart/export/", params={"q": f"!({chart_id})"})
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
        for member in bundle.namelist():
            if "/charts/" in member:
                for line in bundle.read(member).decode().splitlines():
                    if line.startswith("uuid:"):
                        return line.split(":", 1)[1].strip()
    raise RuntimeError(f"uuid not found for chart {chart_id}")


def layout(charts: dict[str, tuple[int, str, int, int]], uuids: dict[int, str]) -> dict[str, Any]:
    rows = [
        ["intro"],
        ["flights", "complete", "alerts_total", "alerts_by_type"],
        ["traffic"],
        ["countries", "altitudes"],
        ["airports", "routes"],
    ]
    position: dict[str, Any] = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
        "HEADER_ID": {"id": "HEADER_ID", "type": "HEADER", "meta": {"text": DASHBOARD_TITLE}},
    }
    for i, row in enumerate(rows):
        row_id = f"ROW-{i}"
        position["GRID_ID"]["children"].append(row_id)
        position[row_id] = {
            "type": "ROW",
            "id": row_id,
            "children": [],
            "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        }
        for key in row:
            node_id = f"{'MARKDOWN' if key == 'intro' else 'CHART'}-{key}"
            position[row_id]["children"].append(node_id)
            parents = ["ROOT_ID", "GRID_ID", row_id]
            if key == "intro":
                position[node_id] = {
                    "type": "MARKDOWN",
                    "id": node_id,
                    "children": [],
                    "parents": parents,
                    "meta": {"width": 12, "height": 18, "code": INTRO},
                }
                continue
            chart_id, title, width, height = charts[key]
            position[node_id] = {
                "type": "CHART",
                "id": node_id,
                "children": [],
                "parents": parents,
                "meta": {
                    "width": width,
                    "height": height,
                    "chartId": chart_id,
                    "sliceName": title,
                    "uuid": uuids[chart_id],
                },
            }
    return position


INTRO = """### SkyRadar — trafic aérien mondial (données ADS-B OpenSky)

Marts dbt (Parquet) rafraîchis toutes les heures par Dagster. Les vols sont **reconstitués**
à partir des positions (l'ADS-B ne transporte ni origine ni destination) : un vol commence
au décollage ou après un trou de plus de 30 min, et ses extrémités sont rattachées à
l'aéroport le plus proche quand l'avion est au sol ou sous 1 500 m.
"""


def export(api: Superset, dashboard_id: int) -> None:
    response = api.client.get("/api/v1/dashboard/export/", params={"q": f"!({dashboard_id})"})
    response.raise_for_status()
    if EXPORT_DIR.exists():
        shutil.rmtree(EXPORT_DIR)
    with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
        for member in bundle.namelist():
            # Strip the timestamped root folder: stable paths give clean git diffs.
            relative = Path(*Path(member).parts[1:])
            target = EXPORT_DIR / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bundle.read(member))
    print(f"Exported dashboard {dashboard_id} to {EXPORT_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8088")
    parser.add_argument("--user", default=os.getenv("SUPERSET_ADMIN_USER", "admin"))
    parser.add_argument("--password", default=os.getenv("SUPERSET_ADMIN_PASSWORD", "admin"))
    args = parser.parse_args()
    api = Superset(args.url, args.user, args.password)
    dashboard_id = build(api)
    export(api, dashboard_id)


if __name__ == "__main__":
    main()
