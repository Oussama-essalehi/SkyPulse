# ADR 0003 — DuckDB : un seul écrivain, des lecteurs sur Parquet

- **Statut** : accepté
- **Date** : 2026-10-13

## Contexte
Un fichier DuckDB n'accepte qu'un processus écrivain, et un écrivain empêche les autres
processus de l'ouvrir. dbt écrit toutes les heures ; Superset, l'API et le ML lisent en
continu.

## Décision
1. **dbt est le seul processus qui ouvre `skyradar.duckdb`** (volume `warehouse`). Toutes les
   exécutions passent par la file d'attente de Dagster (`QueuedRunCoordinator`,
   `max_concurrent_runs: 1`), donc jamais deux `dbt build` en parallèle.
2. Les **marts sont matérialisés en Parquet** (`materialized: external`) dans le volume `marts`.
3. Les lecteurs ouvrent une connexion DuckDB **en mémoire** et lisent ces Parquet
   (`read_parquet`), montés en lecture seule.

## Conséquences
- (+) Aucun conflit de verrou, des lecteurs sans état, des marts lisibles par n'importe quel
  outil Parquet.
- (−) Une requête lancée pendant la réécriture d'un mart peut échouer (fenêtre de quelques
  centaines de ms par heure). Amélioration prévue : écrire dans un fichier temporaire puis
  renommer de façon atomique.
- La matérialisation `external` de dbt-duckdb est surchargée
  (`macros/materialization_external.sql`) pour ne pas écrire la ligne de NULL qu'elle ajoute
  aux modèles vides.
