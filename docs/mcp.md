# Serveur MCP (Claude Desktop et autres clients MCP)

`assistant/src/skyradar_assistant/mcp_server.py` expose les données de SkyRadar à tout client
[MCP](https://modelcontextprotocol.io) :

| Outil | Rôle |
|---|---|
| `live_stats` | Statistiques mondiales en direct (avions suivis, en vol, au sol, pays, altitudes) |
| `count_live_aircraft(lamin, lomin, lamax, lomax)` | Avions présents en ce moment dans une bounding box |
| `active_alerts` | Alertes actives (squawk d'urgence, descente rapide, attente, perte de signal, déroutement…) |
| `describe_marts` | Tables interrogeables, colonnes et descriptions |
| `run_sql(query)` | Une requête `SELECT` DuckDB en lecture seule sur les marts (mêmes garde-fous que l'assistant) |

## Claude Desktop

La stack doit tourner (`make up`). Le serveur s'exécute en stdio **dans** le conteneur
`assistant`, qui a déjà accès aux marts (volume en lecture seule) et à l'API. Dans
`claude_desktop_config.json` :

```json
{
  "mcpServers": {
    "skyradar": {
      "command": "docker",
      "args": ["exec", "-i", "skyradar-assistant-1", "python", "-m", "skyradar_assistant.mcp_server"]
    }
  }
}
```

Exemples de questions : « Combien d'avions survolent l'Espagne en ce moment ? », « Quelles
routes sont les plus fréquentes depuis Paris-CDG ? », « Y a-t-il des urgences en cours ? ».

## Sécurité

`run_sql` passe par `guardrails.validate_sql` (une seule instruction `SELECT`, tables en liste
blanche, pas de fonction de lecture de fichier, `LIMIT` forcé), puis s'exécute sur une
connexion DuckDB en mémoire dont l'accès aux fichiers est limité au dossier des marts
(`enable_external_access = false`), avec un timeout.
