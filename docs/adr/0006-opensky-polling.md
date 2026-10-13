# ADR 0006 — Polling OpenSky piloté par le quota de crédits

- **Statut** : accepté
- **Date** : 2026-10-13

## Contexte
Documentation officielle OpenSky (REST API, *API credit usage*) : `/states/all` coûte 4 crédits
en global (1 à 3 crédits si la zone fait moins de 400 deg²) ; quota journalier : 400 crédits
en anonyme, 4 000 avec un compte, 8 000 pour un feeder actif. Depuis mars 2026,
l'authentification se fait uniquement en OAuth2 *client credentials* (token d'environ 30 min).

## Décision
- `POLL_INTERVAL_SECONDS=auto` calcule `ceil(86400 × coût / (quota × (1 − marge)))` avec une
  marge de 10 % : **960 s** en anonyme, **96 s** avec un compte, **48 s** pour un feeder. Une
  valeur explicite plus agressive est relevée au minimum sûr (et logguée).
- La cadence est fixe : l'intervalle est mesuré depuis le début de l'appel.
- 429 : backoff exponentiel à partir de l'intervalle, en respectant
  `X-Rate-Limit-Retry-After-Seconds`. Erreurs réseau ou 5xx : 3 retries rapides (30 s, 60 s,
  120 s) puis retour à l'intervalle normal.
- `X-Rate-Limit-Remaining` est loggué et exposé dans `/metrics`.
- Token OAuth2 mis en cache, renouvelé 60 s avant expiration, ou immédiatement après un 401.

## Conséquences
En anonyme, la carte n'est rafraîchie que toutes les 16 minutes : l'interpolation (*dead
reckoning*) côté frontend compense. Le mode replay permet de développer sans consommer de
crédits.
