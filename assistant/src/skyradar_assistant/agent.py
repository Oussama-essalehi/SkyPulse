"""Natural-language assistant: text-to-SQL over the marts + live-state tools.

Claude (Anthropic API, Tool Runner) answers in French using four tools:

* ``run_sql``: a guarded, read-only DuckDB query over the allow-listed marts
  (``guardrails.validate_sql`` + sandboxed connection, see ``main.py``);
* ``get_live_stats`` / ``count_live_aircraft`` / ``get_active_alerts``: the live
  in-memory state exposed by the SkyRadar API ("right now" questions).

The tools record what they ran, so the response carries the executed SQL and the
result table next to the answer. A local model served by Ollama can be used instead
(``ollama.py``) with the same tools.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import duckdb
import httpx

from skyradar_assistant.guardrails import UnsafeQueryError

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("ASSISTANT_EFFORT", "medium")
API_URL = os.getenv("SKYRADAR_API_URL", "http://api:8000")
MAX_ROWS_TO_MODEL = 50

TABLE_DOCS: dict[str, str] = {
    "fct_flights": "Vols reconstitués (un par ligne) : icao24, callsign, origin_country (pays "
    "d'immatriculation), departure_airport / arrival_airport (codes ICAO, NULL si inconnus), "
    "departure_time / arrival_time (UTC), duration_min, distance_km, max_altitude_m, "
    "is_complete (départ ET arrivée identifiés).",
    "fct_traffic_hourly": "Avions distincts par heure (hour, UTC), zone géographique "
    "(Europe, North America, Asia, ...) et pays d'immatriculation : aircraft, airborne_aircraft, "
    "positions. Sommer sur origin_country pour un total par zone.",
    "fct_airport_activity": "Départs / arrivées par aéroport (airport_icao, airport_name, "
    "iso_country) et par heure : departures, arrivals, movements.",
    "fct_routes": "Paires départ -> arrivée (route 'LFPG-EGLL') : flights, avg_duration_min, "
    "avg_distance_km, first_seen, last_seen.",
    "fct_alerts_history": "Historique des alertes : alert_type (squawk_7700, rapid_descent, "
    "holding_pattern, signal_loss, diversion, trajectory_outlier, ...), severity, started_at, "
    "is_resolved, callsign, icao24.",
    "fct_altitude_profile": "Positions en vol par jour (day) et tranche d'altitude de 1000 m.",
    "fct_callsign_routes": "Destination habituelle par (callsign, departure_airport).",
    "fct_diversions": "Vols arrivés ailleurs que la destination habituelle de leur callsign.",
    "dim_airports": "Aéroports : airport_icao, airport_iata, airport_name, municipality, "
    "iso_country (code ISO 2 lettres), latitude, longitude.",
    "ml_traffic_forecast": "Trafic par zone : kind = 'actual' (observé) ou 'forecast' "
    "(prévision des prochaines heures), value, model.",
    "ml_flight_anomaly_scores": "Score Isolation Forest par vol (anomaly_score, is_outlier, "
    "explanation).",
    "ml_model_metrics": "Historique des métriques des modèles (model, metric, value, "
    "baseline_value, trained_at).",
}

EXAMPLES = """\
Question : Combien de vols ont été reconstitués aujourd'hui ?
SQL : SELECT count(*) AS vols FROM fct_flights WHERE departure_time >= current_date

Question : Quels sont les 5 aéroports les plus actifs ?
SQL : SELECT airport_icao, airport_name, sum(movements) AS mouvements FROM fct_airport_activity
      GROUP BY ALL ORDER BY mouvements DESC LIMIT 5

Question : Combien d'avions en vol par zone sur la dernière heure complète ?
SQL : SELECT zone, sum(airborne_aircraft) AS avions FROM fct_traffic_hourly
      WHERE hour = (SELECT max(hour) - INTERVAL 1 HOUR FROM fct_traffic_hourly)
      GROUP BY zone ORDER BY avions DESC
"""


def system_prompt(schema: dict[str, list[dict[str, str]]]) -> str:
    tables = []
    for table, columns in schema.items():
        cols = ", ".join(f"{c['name']} {c['type']}" for c in columns)
        tables.append(f"- {table} : {TABLE_DOCS.get(table, '')}\n  Colonnes : {cols}")
    return f"""Tu es l'assistant de SkyRadar, une plateforme de suivi du trafic aérien mondial
construite sur les données ADS-B d'OpenSky Network.

Réponds toujours en français, en une ou deux phrases factuelles, en citant les chiffres.

Outils :
- run_sql pour l'historique (marts DuckDB, lecture seule). Une seule requête SELECT
  (dialecte DuckDB), uniquement sur les tables ci-dessous ; un LIMIT est ajouté.
- get_live_stats, count_live_aircraft et get_active_alerts pour la situation EN DIRECT
  (« maintenant », « en ce moment »). Pour un pays ou une région, passe une bounding box
  approximative à count_live_aircraft (ex. France métropolitaine : lat 41.3 à 51.1,
  lon -5.2 à 9.6).
Si une requête échoue, corrige-la et réessaie. Si les données ne permettent pas de répondre
(historique trop court, information absente), dis-le simplement.

Contexte sur les données : les départs et arrivées sont reconstitués (l'ADS-B ne les
transporte pas) ; origin_country est le pays d'immatriculation, pas le pays survolé ;
toutes les heures sont en UTC.

Tables disponibles :
{chr(10).join(tables)}

Exemples :
{EXAMPLES}"""


@dataclass
class Trace:
    """What the tools did during one question (returned to the UI)."""

    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class Toolbox:
    """Tool implementations shared by the Anthropic and Ollama providers."""

    def __init__(self, run_query: Any, api_url: str = API_URL) -> None:
        self._run_query = run_query
        self._api_url = api_url.rstrip("/")
        self.trace = Trace()

    def run_sql(self, query: str) -> str:
        self.trace.tools_used.append("run_sql")
        try:
            safe_sql, columns, rows = self._run_query(query)
        except UnsafeQueryError as exc:
            return f"Requête refusée par les garde-fous : {exc}"
        except duckdb.Error as exc:
            return f"Erreur SQL : {exc}"
        self.trace.sql, self.trace.columns, self.trace.rows = safe_sql, columns, rows
        return _json(
            {
                "sql": safe_sql,
                "columns": columns,
                "rows": rows[:MAX_ROWS_TO_MODEL],
                "row_count": len(rows),
            }
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = httpx.get(f"{self._api_url}{path}", params=params, timeout=10)
        response.raise_for_status()
        return response.json()

    def get_live_stats(self) -> str:
        self.trace.tools_used.append("get_live_stats")
        try:
            return _json(self._get("/stats/live"))
        except httpx.HTTPError as exc:
            return f"API temps réel indisponible : {exc}"

    def count_live_aircraft(self, lamin: float, lomin: float, lamax: float, lomax: float) -> str:
        self.trace.tools_used.append("count_live_aircraft")
        try:
            result = self._get(
                "/live/region", {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax}
            )
        except httpx.HTTPError as exc:
            return f"API temps réel indisponible : {exc}"
        self.trace.columns = ["zone", "avions_total", "en_vol", "au_sol"]
        self.trace.rows = [
            [
                f"{lamin},{lomin} -> {lamax},{lomax}",
                result["aircraft_total"],
                result["airborne"],
                result["on_ground"],
            ]
        ]
        return _json(result)

    def get_active_alerts(self) -> str:
        self.trace.tools_used.append("get_active_alerts")
        try:
            alerts = self._get("/alerts")
        except httpx.HTTPError as exc:
            return f"API temps réel indisponible : {exc}"
        keep = ("type", "severity", "callsign", "icao24", "message")
        return _json([{k: a.get(k) for k in keep} for a in alerts[:30]])


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "run_sql",
        "description": "Exécute UNE requête SELECT DuckDB en lecture seule sur les marts "
        "historiques et renvoie colonnes + lignes.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Requête SELECT DuckDB"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_live_stats",
        "description": "Statistiques mondiales en direct : avions suivis, en vol, au sol, par "
        "pays d'immatriculation et par tranche d'altitude.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "count_live_aircraft",
        "description": "Nombre d'avions actuellement dans une bounding box (latitudes et "
        "longitudes en degrés décimaux).",
        "input_schema": {
            "type": "object",
            "properties": {
                "lamin": {"type": "number"},
                "lomin": {"type": "number"},
                "lamax": {"type": "number"},
                "lomax": {"type": "number"},
            },
            "required": ["lamin", "lomin", "lamax", "lomax"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_active_alerts",
        "description": "Alertes actives en ce moment (urgences, descentes rapides, attentes, "
        "pertes de signal, déroutements).",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def call_tool(toolbox: Toolbox, name: str, arguments: dict[str, Any]) -> str:
    handler = getattr(toolbox, name, None)
    if name not in {spec["name"] for spec in TOOL_SPECS} or handler is None:
        return f"Outil inconnu : {name}"
    return str(handler(**arguments))


def ask_claude(
    question: str,
    toolbox: Toolbox,
    schema: dict[str, list[dict[str, str]]],
    client: Any = None,
    max_iterations: int = 8,
) -> dict[str, Any]:
    """Run the agent loop with the Anthropic Tool Runner and return answer + trace."""
    import anthropic
    from anthropic import beta_tool

    client = client or anthropic.Anthropic(timeout=90.0)

    @beta_tool
    def run_sql(query: str) -> str:
        """Exécute UNE requête SELECT DuckDB en lecture seule sur les marts historiques.

        Args:
            query: Requête SELECT DuckDB (une seule instruction).
        """
        return toolbox.run_sql(query)

    @beta_tool
    def get_live_stats() -> str:
        """Statistiques mondiales en direct : avions suivis, en vol, au sol, par pays."""
        return toolbox.get_live_stats()

    @beta_tool
    def count_live_aircraft(lamin: float, lomin: float, lamax: float, lomax: float) -> str:
        """Nombre d'avions actuellement dans une bounding box.

        Args:
            lamin: Latitude minimale (degrés).
            lomin: Longitude minimale (degrés).
            lamax: Latitude maximale (degrés).
            lomax: Longitude maximale (degrés).
        """
        return toolbox.count_live_aircraft(lamin, lomin, lamax, lomax)

    @beta_tool
    def get_active_alerts() -> str:
        """Alertes actives en ce moment (urgences, descentes rapides, attentes, déroutements)."""
        return toolbox.get_active_alerts()

    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=16000,
        system=system_prompt(schema),
        tools=[run_sql, get_live_stats, count_live_aircraft, get_active_alerts],
        messages=[{"role": "user", "content": question}],
        output_config={"effort": EFFORT},
        # Server-side fallback if a safety classifier declines the request.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        max_iterations=max_iterations,
    )
    final = None
    for message in runner:
        final = message
    if final is None:
        raise RuntimeError("empty response from the model")
    if final.stop_reason == "refusal":
        answer = "Je ne peux pas répondre à cette question."
    else:
        answer = "\n".join(b.text for b in final.content if b.type == "text").strip()
    return {
        "answer": answer or "Pas de réponse.",
        "sql": toolbox.trace.sql,
        "columns": toolbox.trace.columns,
        "rows": toolbox.trace.rows,
        "tools_used": toolbox.trace.tools_used,
        "model": final.model,
        "provider": "anthropic",
    }
