# ADR 0001 — Monorepo Python en workspace `uv`

- **Statut** : accepté
- **Date** : 2026-10-13

## Contexte
Sept composants Python (ingestion, API, streaming, ML, assistant, orchestration, lib commune)
partagent les mêmes schémas de messages et doivent être construits en images Docker distinctes.

## Décision
Un seul dépôt, un **workspace `uv`** : un `pyproject.toml` par service, un `uv.lock` unique,
une lib `libs/common` (`skyradar_common`) qui porte les modèles pydantic des messages,
l'accès Kafka, le logging et l'accès S3. Chaque Dockerfile installe uniquement son package
(`uv sync --package <service> --no-editable`) dans un build multi-stage.

## Conséquences
- (+) Un seul jeu de versions résolu pour tout le projet, pas de dérive de schéma entre
  producteurs et consommateurs, images légères (l'API n'embarque ni dbt ni Dagster).
- (−) Une contrainte de version (ex. `sqlalchemy<2.1` pour dagster-postgres) s'applique à tout
  le workspace.
