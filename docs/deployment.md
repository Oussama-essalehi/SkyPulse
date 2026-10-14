# Déploiement du profil `demo`

Le profil `demo` (carte en direct : ingestion, Redpanda, détecteur d'anomalies, API,
frontend) tient dans ~1,5 Go de RAM. Le profil `full` (lakehouse, Dagster, Superset, MLflow)
vise une machine locale ou une VM de 8 Go.

## Option A — une petite VM (recommandée)

La plus simple et la moins chère (une VM 2 vCPU / 4 Go à quelques euros par mois, chez
Hetzner, OVH, Scaleway…), avec TLS automatique via Caddy.

```bash
# Sur la VM (Docker + compose installés), DNS du domaine pointé vers la VM
git clone <ce dépôt> skyradar && cd skyradar
cp .env.example .env
# .env : OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET (96 s au lieu de 960 s), SKYRADAR_DOMAIN=radar.example.com
docker compose -f docker-compose.yml -f deploy/compose.prod.yml --profile demo up -d --build --wait
```

- Seul Caddy expose des ports (80/443) : tous les autres services (API, Redpanda, stockage
  objet, Dagster, MLflow, Superset…) ne sont joignables que sur le réseau Docker. Le
  frontend proxifie `/api` (REST + WebSocket) vers l'API : une seule origine, pas de CORS.
- Démo sans quota : `SOURCE=replay` dans `.env` (fichier `data/replay/replay.parquet`
  enregistré avec `make record`, ou trafic synthétique s'il est absent).
- **Profil complet** sur la même VM (8 Go), avec les outils d'administration derrière un
  mot de passe :
  ```bash
  # DNS : enregistrement générique *.radar.example.com vers la VM, en plus de radar.example.com
  docker run --rm caddy:2.8-alpine caddy hash-password --plaintext 'mot-de-passe-admin'
  # .env : SKYRADAR_CADDYFILE=Caddyfile.full, ADMIN_USER=admin,
  #        ADMIN_PASSWORD_HASH='$2a$14$...'   (entre apostrophes : le hash contient des $)
  docker compose -f docker-compose.yml -f deploy/compose.prod.yml --profile full up -d --build --wait
  ```
  La carte reste publique sur `radar.example.com` ; `dagster.`, `mlflow.`, `superset.`,
  `kafka.` (console Redpanda) et `grafana.` demandent le mot de passe (authentification
  HTTP de Caddy ; Superset garde en plus son propre login).

### Protections d'une démo publique

| Risque | Protection | Réglage (`.env`) |
|---|---|---|
| Coût de l'assistant (chaque question appelle un LLM) | 5 questions / min et 40 / jour par client, **300 / jour au total** | `API_RATE_ASSISTANT`, `API_ASSISTANT_BUDGET` |
| Clics en rafale sur les avions (une recherche de route par clic) | 60 fiches / min par client | `API_RATE_AIRCRAFT` |
| Connexions WebSocket ouvertes en masse | 4 par client | `API_MAX_WS_PER_CLIENT` |
| Outils d'administration sans authentification | Ports fermés, accès par Caddy avec mot de passe | `ADMIN_USER`, `ADMIN_PASSWORD_HASH` |

Le client est identifié par son adresse IP réelle : Caddy remplace tout `X-Forwarded-For`
envoyé par le navigateur, nginx ne fait confiance qu'aux proxys des réseaux privés, et l'API
n'accepte l'en-tête `X-Real-IP` (ou `Fly-Client-IP` sur Fly.io) que d'un pair privé. Les
compteurs sont en mémoire (une seule instance d'API) et repartent à zéro au redémarrage.

Vérifié en local avec `SKYRADAR_DOMAIN=localhost` (certificat interne de Caddy) : seuls les
ports 80/443 sont publiés, les sous-domaines d'administration répondent 401 sans mot de
passe, et la 61e fiche d'avion dans la minute répond 429, même avec un `X-Forwarded-For`
différent à chaque requête.

## Option B — hébergement séparé (Cloudflare Pages + Fly.io + Kafka managé)

```
Cloudflare Pages (frontend statique) ──HTTPS/WSS──► Fly.io : skyradar-api
                                                          ▲
Fly.io : skyradar-ingestion ──► Redpanda Serverless ──────┤
Fly.io : skyradar-anomaly   ◄──────────────┘──────────────┘
```

1. **Kafka managé** : créer un cluster Redpanda Serverless (ou Confluent Cloud) et un
   utilisateur SCRAM. Les services lisent `KAFKA_BOOTSTRAP_SERVERS`,
   `KAFKA_SECURITY_PROTOCOL=SASL_SSL`, `KAFKA_SASL_MECHANISM`, `KAFKA_SASL_USERNAME`,
   `KAFKA_SASL_PASSWORD` (voir `skyradar_common.kafka.KafkaSettings`). Les topics sont créés
   au démarrage s'ils n'existent pas (droit de création nécessaire, sinon les créer à la main :
   `aircraft.states` 6 partitions, `aircraft.snapshots` et `aircraft.alerts` 1 partition).
2. **Fly.io** (depuis la racine du dépôt) :
   ```bash
   fly apps create skyradar-api && fly apps create skyradar-ingestion && fly apps create skyradar-anomaly
   fly secrets set -a skyradar-api KAFKA_BOOTSTRAP_SERVERS=... KAFKA_SASL_USERNAME=... KAFKA_SASL_PASSWORD=...
   fly secrets set -a skyradar-ingestion KAFKA_BOOTSTRAP_SERVERS=... KAFKA_SASL_USERNAME=... \
       KAFKA_SASL_PASSWORD=... OPENSKY_CLIENT_ID=... OPENSKY_CLIENT_SECRET=...
   fly secrets set -a skyradar-anomaly KAFKA_BOOTSTRAP_SERVERS=... KAFKA_SASL_USERNAME=... KAFKA_SASL_PASSWORD=...
   fly deploy . --config deploy/fly/api.toml --dockerfile api/Dockerfile
   fly deploy . --config deploy/fly/ingestion.toml --dockerfile ingestion/Dockerfile --ha=false
   fly deploy . --config deploy/fly/anomaly-detector.toml --dockerfile streaming/Dockerfile --ha=false
   ```
   L'ingestion doit tourner sur **une seule machine** (`--ha=false`) : deux instances
   consommeraient deux fois le quota OpenSky.
3. **Cloudflare Pages** : projet relié au dépôt GitHub.
   - Build command : `cd frontend && npm ci && npm run build`
   - Output directory : `frontend/dist`
   - Variable : `VITE_API_URL=https://skyradar-api.fly.dev` (le WebSocket devient
     `wss://skyradar-api.fly.dev/ws/live`).
   - Ajouter l'URL Pages dans `API_CORS_ORIGINS` (`deploy/fly/api.toml`).
   Sans `404.html`, Pages sert `index.html` pour toutes les routes (SPA).

Render fonctionne de la même façon (services Docker « web » pour l'API, « background
worker » pour l'ingestion et le détecteur), avec les mêmes variables d'environnement.

## Points d'attention

- **Licence OpenSky** : usage non commercial uniquement ; une démo publique doit l'indiquer
  (c'est le cas dans le pied de page de la carte).
- **Secrets** : uniquement côté serveur (`fly secrets`, `.env`) ; le navigateur ne reçoit
  aucune clé, l'assistant passe par le proxy `/assistant/ask` de l'API.
- **Coûts** : le profil `demo` n'a pas besoin du lake. Pour historiser depuis l'option B,
  ajouter le `parquet-sink` (même image que le détecteur, `cmd` par défaut) avec un bucket
  S3 managé (Cloudflare R2, AWS S3…) : `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`.
