# ADR 0005 — Sink Parquet : at-least-once + dédoublonnage en aval

- **Statut** : accepté
- **Date** : 2026-10-13

## Décision
Le sink bufferise les messages, écrit un fichier Parquet par partition `date=/hour=` toutes les
`SINK_FLUSH_INTERVAL_SECONDS` (5 min), puis **seulement ensuite** committe les offsets Kafka.

- Crash avant l'écriture : rien n'est perdu, les messages sont relus.
- Crash entre l'écriture et le commit : le lot est rejoué. Le nom de fichier est dérivé des
  plages d'offsets du lot (`part-<sha1>.parquet`) : un rejeu identique écrase le même objet.
  Si les bornes du lot diffèrent, au plus un lot est dupliqué ; `stg_aircraft_states`
  dédoublonne sur `(icao24, position_epoch)`.
- Arrêt propre (SIGTERM) : flush final avant la sortie.

Le partitionnement suit l'**heure de l'événement** (`snapshot_time`), pas l'heure de réception.

## Alternatives
L'exactly-once (transactions Kafka + commit atomique dans le stockage) apporterait beaucoup de
complexité pour un gain nul, puisque la déduplication est déjà nécessaire côté source :
OpenSky renvoie la même position tant que l'avion n'a pas émis de nouvelle trame.
