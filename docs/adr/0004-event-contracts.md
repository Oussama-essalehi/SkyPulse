# ADR 0004 — Contrats d'événements et topics Redpanda

- **Statut** : accepté
- **Date** : 2026-10-13

## Décision
| Topic | Clé | Contenu | Partitions | Rétention |
|---|---|---|---|---|
| `aircraft.states` | `icao24` | un `StateVector` par avion et par snapshot | 6 | 24 h |
| `aircraft.snapshots` | `snapshot` | `SnapshotMeta` (horodatage, nombre d'avions, crédits restants) | 1 | 24 h |
| `aircraft.alerts` | `icao24` | `Alert` (cycle de vie : active → résolue) | 1 | 7 j |

- Messages **JSON** (orjson) validés par les modèles pydantic de `skyradar_common.models`,
  avec un champ `schema_version`. Plus simple à inspecter (console Redpanda) qu'Avro pour un
  volume d'environ 10 000 messages par snapshot ; le Schema Registry de Redpanda reste
  disponible si le volume ou le nombre de producteurs augmente.
- La clé `icao24` garantit l'ordre des positions d'un même avion (même partition).
- Les métadonnées de snapshot ont **leur propre topic** plutôt que d'être mêlées aux positions :
  le sink et le détecteur n'ont pas à filtrer de types hétérogènes.
