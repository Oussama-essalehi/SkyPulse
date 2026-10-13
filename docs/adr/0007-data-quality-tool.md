# ADR 0007 — Qualité des données : Soda Core (contrats v4) + tests dbt

- **Statut** : accepté
- **Date** : 2026-10-13

## Contexte
Contrôles demandés : bornes lat/lon et altitude, téléportation (vitesse implicite entre deux
positions), fraîcheur du dernier snapshot, volume minimal par heure, le tout visible dans
Dagster.

## Décision
**Soda Core v4** (contrats de données YAML, `soda-duckdb`) plutôt que Great Expectations :
- déclaratif et lisible (`data_quality/contracts/*.yml`), versionné à côté du code ;
- connecteur DuckDB natif qui accepte une **connexion existante** : les contrats s'exécutent
  sur une connexion DuckDB en mémoire avec des vues sur les Parquet du lake ou des marts, donc
  sans toucher au fichier dbt (ADR 0003) ;
- fraîcheur, volume, bornes et requêtes de « lignes en échec » (téléportation) gérés
  nativement ; GX impose un *data context* et beaucoup plus de configuration pour le même
  résultat.

Intégration : chaque check nommé d'un contrat devient un **asset check Dagster**
(`skyradar_orchestration/quality.py`) avec les diagnostics Soda en métadonnées. Les checks
opérationnels (fraîcheur, volume) sont en sévérité WARN ; les checks d'exactitude en ERROR.
Ils s'exécutent dans le job horaire, avec les 40 tests dbt (également exposés en asset checks).

| Contrat | Checks |
|---|---|
| `raw_aircraft_states` (48 h de partitions) | icao24 présent, lat/lon dans les bornes, altitude et vitesse plausibles (< 0,5 % d'invalides), lake non vide, fraîcheur < 30 min, téléportation (> 600 m/s, < 0,1 % des lignes), volume horaire minimal |
| `fct_flights` | `flight_id` présent et unique, durée dans [0, 24 h], chronologie départ/arrivée, vitesse moyenne plausible |

## Limites
Les variables Soda sont substituées comme des chaînes : elles servent dans les requêtes SQL
(seuil de volume) mais pas comme seuils numériques ; la fraîcheur maximale est donc fixée dans
le contrat (30 min : polling anonyme de 16 min + flush du sink de 5 min).
