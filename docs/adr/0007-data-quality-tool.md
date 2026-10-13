# ADR 0007 — Outil de qualité des données : Soda Core (prévu), tests dbt + asset checks (actuel)

- **Statut** : proposé (étape d'enrichissement)
- **Date** : 2026-10-13

## Contexte
Les contrôles demandés sont : bornes lat/lon et altitude, téléportation (vitesse implicite
entre deux positions), fraîcheur du dernier snapshot, volume minimal par heure. Ils doivent
être visibles dans Dagster.

## Décision
- **Déjà en place** : tests dbt (bornes, unicité, non-nullité, valeurs acceptées), exposés
  automatiquement comme *asset checks* Dagster, et un asset check Python de fraîcheur du lake.
- **Étape suivante** : **Soda Core** plutôt que Great Expectations. SodaCL est déclaratif (YAML),
  dispose d'un connecteur DuckDB natif, gère nativement la fraîcheur, le volume et les
  requêtes SQL de lignes en échec (téléportation), et reste léger (GX impose un « data
  context » et beaucoup de configuration pour un résultat équivalent ici).
  Chaque fichier de checks devient un asset check Dagster.
