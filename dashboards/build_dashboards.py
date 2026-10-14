"""Build the SkyRadar Superset dashboards through the REST API, then export them.

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
    "fct_diversions": "arrival_time",
    "ml_model_metrics": "trained_at",
    "ml_traffic_forecast": "hour",
    "ml_flight_anomaly_scores": "last_seen_ts",
    "dim_aircraft": "last_seen_ts",
    "fct_airline_activity": "hour",
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


def where(sql: str) -> list[dict[str, str]]:
    return [{"expressionType": "SQL", "sqlExpression": sql, "clause": "WHERE"}]


ETA_MAE = "model = 'eta' and metric = 'mae_min'"

ML_CHARTS: list[tuple[str, str, str, dict[str, Any], int, int]] = [
    (
        "eta_mae",
        "ml_model_metrics",
        "MAE de l'ETA - modèle LightGBM (min)",
        {
            "viz_type": "big_number_total",
            "metric": metric("arg_max(value, trained_at)", "MAE modèle (min)"),
            "adhoc_filters": where(ETA_MAE),
            "subheader": "dernier entraînement, jeu de test (vols les plus récents)",
            "y_axis_format": ".2f",
        },
        4,
        30,
    ),
    (
        "eta_baseline",
        "ml_model_metrics",
        "MAE de l'ETA - baseline naïve (min)",
        {
            "viz_type": "big_number_total",
            "metric": metric("arg_max(baseline_value, trained_at)", "MAE baseline (min)"),
            "adhoc_filters": where(ETA_MAE),
            "subheader": "distance restante / vitesse sol",
            "y_axis_format": ".2f",
        },
        4,
        30,
    ),
    (
        "eta_mape",
        "ml_model_metrics",
        "Historique des métriques des modèles",
        {
            "viz_type": "table",
            "query_mode": "raw",
            "all_columns": [
                "trained_at",
                "model",
                "metric",
                "value",
                "baseline_value",
                "n_samples",
            ],
            "adhoc_filters": where("model in ('eta', 'traffic_forecast')"),
            "order_by_cols": [json.dumps(["trained_at", False])],
            "row_limit": 20,
        },
        4,
        30,
    ),
    (
        "eta_history",
        "ml_model_metrics",
        "Évolution de la MAE de l'ETA : modèle vs baseline",
        {
            "viz_type": "echarts_timeseries_line",
            "x_axis": "trained_at",
            "time_grain_sqla": "PT1H",
            "metrics": [
                metric("AVG(value)", "Modèle LightGBM"),
                metric("AVG(baseline_value)", "Baseline naïve"),
            ],
            "groupby": [],
            "adhoc_filters": where(ETA_MAE),
            "y_axis_format": ".2f",
            "y_axis_title": "MAE (min)",
            "markerEnabled": True,
            "markerSize": 8,
            "show_legend": True,
            "time_range": "No filter",
        },
        6,
        50,
    ),
    (
        "forecast",
        "ml_traffic_forecast",
        "Trafic en Europe : observé vs prévision (Holt-Winters)",
        {
            "viz_type": "echarts_timeseries_line",
            "x_axis": "hour",
            "time_grain_sqla": "PT1H",
            "metrics": [metric("SUM(value)", "Avions en vol")],
            "groupby": ["kind"],
            "adhoc_filters": where("zone = 'Europe'"),
            "y_axis_format": ",d",
            "show_legend": True,
            "time_range": "No filter",
        },
        6,
        50,
    ),
    (
        "outliers",
        "ml_flight_anomaly_scores",
        "Vols atypiques (Isolation Forest)",
        {
            "viz_type": "table",
            "query_mode": "raw",
            "all_columns": ["callsign", "icao24", "anomaly_score", "explanation", "last_seen_ts"],
            "adhoc_filters": where("is_outlier"),
            "order_by_cols": [json.dumps(["anomaly_score", False])],
            "row_limit": 20,
        },
        7,
        55,
    ),
    (
        "diversions",
        "fct_diversions",
        "Déroutements détectés",
        {
            "viz_type": "table",
            "query_mode": "raw",
            "all_columns": [
                "callsign",
                "departure_airport",
                "arrival_airport",
                "usual_arrival_airport",
                "usual_arrival_share",
                "arrival_time",
            ],
            "order_by_cols": [json.dumps(["arrival_time", False])],
            "row_limit": 20,
        },
        5,
        55,
    ),
]


AIRCRAFT = metric("COUNT(*)", "Avions")

FLEET_CHARTS: list[tuple[str, str, str, dict[str, Any], int, int]] = [
    (
        "airlines_count",
        "fct_airline_activity",
        "Compagnies observées",
        {
            "viz_type": "big_number_total",
            "metric": metric("COUNT(DISTINCT airline_icao)", "Compagnies"),
            "subheader": "préfixe OACI du callsign (AFR1234 -> Air France)",
            "y_axis_format": ",d",
        },
        4,
        30,
    ),
    (
        "registry_coverage",
        "dim_aircraft",
        "Avions identifiés dans la base OpenSky",
        {
            "viz_type": "big_number_total",
            "metric": metric("AVG(CASE WHEN in_registry THEN 1.0 ELSE 0 END)", "Identifiés"),
            "subheader": "immatriculation, type et opérateur connus",
            "y_axis_format": ".1%",
        },
        4,
        30,
    ),
    (
        "heavies",
        "dim_aircraft",
        "Gros porteurs observés",
        {
            "viz_type": "big_number_total",
            "metric": AIRCRAFT,
            "adhoc_filters": where("wake_category in ('H', 'J')"),
            "subheader": "catégorie de turbulence de sillage H ou J (A350, B777, A380…)",
            "y_axis_format": ",d",
        },
        4,
        30,
    ),
    (
        "airline_activity",
        "fct_airline_activity",
        "Avions en vol par compagnie, heure par heure (8 premières)",
        {
            "viz_type": "echarts_timeseries_line",
            "x_axis": "hour",
            "time_grain_sqla": "PT1H",
            "metrics": [AIRBORNE],
            "groupby": ["airline_name"],
            "limit": 8,
            "timeseries_limit_metric": AIRBORNE,
            "order_desc": True,
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
        "airline_flights",
        "fct_flights",
        "Vols reconstitués par compagnie",
        {
            "viz_type": "echarts_timeseries_bar",
            "x_axis": "airline_name",
            "metrics": [FLIGHTS],
            "groupby": [],
            "adhoc_filters": where("airline_name is not null"),
            "orientation": "horizontal",
            "row_limit": 15,
            "x_axis_sort": "Vols reconstitués",
            "x_axis_sort_asc": False,
            "y_axis_format": ",d",
            "show_legend": False,
            "time_range": "No filter",
        },
        6,
        55,
    ),
    (
        "types",
        "dim_aircraft",
        "Types d'avions les plus observés",
        {
            "viz_type": "echarts_timeseries_bar",
            "x_axis": "typecode",
            "metrics": [AIRCRAFT],
            "groupby": [],
            "adhoc_filters": where("typecode is not null"),
            "orientation": "horizontal",
            "row_limit": 15,
            "x_axis_sort": "Avions",
            "x_axis_sort_asc": False,
            "y_axis_format": ",d",
            "show_legend": False,
            "time_range": "No filter",
        },
        6,
        55,
    ),
    (
        "wake",
        "dim_aircraft",
        "Catégories de turbulence de sillage",
        {
            "viz_type": "pie",
            "groupby": ["wake_category"],
            "metric": AIRCRAFT,
            "adhoc_filters": where("wake_category is not null"),
            "donut": True,
            "show_labels": True,
            "label_type": "key_percent",
            "show_legend": True,
            "legendOrientation": "right",
            "time_range": "No filter",
        },
        4,
        50,
    ),
    (
        "fleets",
        "dim_aircraft",
        "Flotte observée par compagnie",
        {
            "viz_type": "table",
            "query_mode": "aggregate",
            "groupby": ["airline_name"],
            "metrics": [
                AIRCRAFT,
                metric("COUNT(DISTINCT typecode)", "Types"),
                metric("ROUND(AVG(year(current_date) - built_year), 1)", "Âge moyen (ans)"),
            ],
            "adhoc_filters": where("airline_name is not null"),
            "timeseries_limit_metric": AIRCRAFT,
            "order_desc": True,
            "row_limit": 20,
            "time_range": "No filter",
        },
        8,
        50,
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


TRAFFIC_INTRO = """### SkyRadar — trafic aérien mondial (données ADS-B OpenSky)

Marts dbt (Parquet) rafraîchis toutes les heures par Dagster. Les vols sont **reconstitués**
à partir des positions (l'ADS-B ne transporte ni origine ni destination) : un vol commence
au décollage ou après un trou de plus de 30 min, et ses extrémités sont rattachées à
l'aéroport le plus proche quand l'avion est au sol ou sous 1 500 m.
"""

ML_INTRO = """### SkyRadar — modèles de ML (suivis dans MLflow)

**ETA** : LightGBM corrige l'estimation naïve (distance restante / vitesse sol) ; évaluation
sur les vols les plus récents (split temporel par vol). **Prévision** : Holt-Winters
(saisonnalité 24 h) contre une baseline saisonnière naïve. **Isolation Forest** sur les
caractéristiques de trajectoire, et **déroutements** (arrivée différente de la destination
habituelle du callsign). Les modèles ne s'entraînent qu'au-delà d'un seuil d'historique.
"""

FLEET_INTRO = """### Compagnies et flotte
Chaque avion observé est rapproché de la **base aéronefs OpenSky** (immatriculation, type,
opérateur) et des **types OACI** (Doc 8643 : motorisation, catégorie de sillage). La
compagnie vient du préfixe OACI du callsign (OpenFlights), sinon de l'opérateur déclaré.
"""

DASHBOARDS: list[dict[str, Any]] = [
    {
        "slug": "skyradar",
        "title": "SkyRadar — Trafic aérien",
        "intro": TRAFFIC_INTRO,
        "charts": CHARTS,
        "rows": [
            ["intro"],
            ["flights", "complete", "alerts_total", "alerts_by_type"],
            ["traffic"],
            ["countries", "altitudes"],
            ["airports", "routes"],
        ],
    },
    {
        "slug": "skyradar-ml",
        "title": "SkyRadar — Modèles ML",
        "intro": ML_INTRO,
        "charts": ML_CHARTS,
        "rows": [
            ["intro"],
            ["eta_mae", "eta_baseline", "eta_mape"],
            ["eta_history", "forecast"],
            ["outliers", "diversions"],
        ],
    },
    {
        "slug": "skyradar-fleet",
        "title": "SkyRadar — Compagnies et flotte",
        "intro": FLEET_INTRO,
        "charts": FLEET_CHARTS,
        "rows": [
            ["intro"],
            ["airlines_count", "registry_coverage", "heavies"],
            ["airline_activity"],
            ["airline_flights", "types"],
            ["wake", "fleets"],
        ],
    },
]


def build(api: Superset) -> list[int]:
    # Clean previous objects (idempotent rebuild).
    for spec in DASHBOARDS:
        api.delete("dashboard", api.find("dashboard", "slug", spec["slug"]))
        for _, _, title, *_ in spec["charts"]:
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

    return [build_dashboard(api, spec, dataset_ids) for spec in DASHBOARDS]


def build_dashboard(api: Superset, spec: dict[str, Any], dataset_ids: dict[str, int]) -> int:
    chart_ids: dict[str, tuple[int, str, int, int]] = {}
    chart_uuids: dict[int, str] = {}
    for key, dataset, title, params, width, height in spec["charts"]:
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
        chart_uuids[chart_id] = _uuid_from_export(api, chart_id)

    position = layout(spec, chart_ids, chart_uuids)
    dashboard_id = api.post(
        "/api/v1/dashboard/",
        {
            "dashboard_title": spec["title"],
            "slug": spec["slug"],
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


def layout(
    spec: dict[str, Any], charts: dict[str, tuple[int, str, int, int]], uuids: dict[int, str]
) -> dict[str, Any]:
    position: dict[str, Any] = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
        "HEADER_ID": {"id": "HEADER_ID", "type": "HEADER", "meta": {"text": spec["title"]}},
    }
    for i, row in enumerate(spec["rows"]):
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
                    "meta": {"width": 12, "height": 20, "code": spec["intro"]},
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


def export(api: Superset, dashboard_ids: list[int]) -> None:
    ids = ",".join(str(i) for i in dashboard_ids)
    response = api.client.get("/api/v1/dashboard/export/", params={"q": f"!({ids})"})
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
    print(f"Exported dashboards {dashboard_ids} to {EXPORT_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8088")
    parser.add_argument("--user", default=os.getenv("SUPERSET_ADMIN_USER", "admin"))
    parser.add_argument("--password", default=os.getenv("SUPERSET_ADMIN_PASSWORD", "admin"))
    args = parser.parse_args()
    api = Superset(args.url, args.user, args.password)
    export(api, build(api))


if __name__ == "__main__":
    main()
