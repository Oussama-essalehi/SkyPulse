# Protocole WebSocket `/ws/live`

Messages JSON texte (compression *permessage-deflate* négociée par uvicorn/websockets).

## 1. `snapshot` puis `delta`

À la connexion, le serveur envoie l'état complet (`type: "snapshot"`), puis, au plus une fois
par seconde (`API_WS_PUSH_INTERVAL_SECONDS`), un `delta` contenant uniquement les avions mis
à jour et les avions retirés.

```json
{
  "type": "delta",
  "server_time": 1791926400.12,
  "data_time": 1791926385,
  "source": "opensky",
  "poll_interval": 96.0,
  "count": 11242,
  "upserts": {
    "icao24": ["3c6444", "4b1814"],
    "callsign": ["DLH9LF", null],
    "country": ["Germany", "Switzerland"],
    "lon": [8.55, 8.6], "lat": [50.03, 47.4],
    "alt": [11278, null], "velocity": [236.5, 5.0], "track": [268.4, 90.0],
    "vrate": [0.0, null], "on_ground": [0, 1], "squawk": ["1000", "7700"],
    "time_position": [1791926383, 1791926370], "category": [4, null]
  },
  "removed": ["a1b2c3"]
}
```

**Format colonne** : avec plus de 10 000 avions, les noms de champs ne sont transmis qu'une
fois. La charge est environ 3 fois plus petite qu'une liste d'objets, et le frontend remplit
directement des `Float32Array` pour deck.gl.

- `server_time` permet au client d'estimer le décalage d'horloge.
- `time_position` sert de point de départ à l'interpolation (*dead reckoning*).
- Unités : altitude en m, vitesse en m/s, cap en degrés (sens horaire depuis le nord),
  vitesse verticale en m/s.

## 2. `alerts`

Envoyé à la connexion puis à chaque changement de l'ensemble des alertes actives :

```json
{"type": "alerts", "alerts": [{"alert_id": "squawk_7700:4b1814:1791926370", "icao24": "4b1814",
  "callsign": "SWR123", "type": "squawk_7700", "severity": "critical", "status": "active",
  "message": "Squawk 7700 : situation d'urgence générale déclarée", "latitude": 47.4,
  "longitude": 8.6, "altitude": null, "event_time": 1791926370, "detected_at": 1791926371.5,
  "details": {"squawk": "7700"}}]}
```
