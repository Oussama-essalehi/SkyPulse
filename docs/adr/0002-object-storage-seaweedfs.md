# ADR 0002 — Stockage objet : SeaweedFS (S3) au lieu de MinIO

- **Statut** : accepté
- **Date** : 2026-10-13

## Contexte
Le cahier des charges prévoit MinIO. Depuis fin 2025 MinIO ne publie plus d'images ni de
binaires pour l'édition communautaire et le dépôt GitHub est archivé ; en octobre 2026,
`docker pull minio/minio` échoue (« repository does not exist ») et l'image Quay est figée sur
une version de 2025 sans correctifs.

## Décision
Utiliser **SeaweedFS** (`chrislusf/seaweedfs`, Apache-2.0) en mode `server -s3` : un seul
conteneur, API S3 sur le port 8333, interface « filer » (port 8888) pour parcourir le lake.
Tout le code reste **générique S3** (`S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`) :
pyarrow `S3FileSystem` côté sink, secret S3 DuckDB côté dbt, boto3 côté MLflow.

## Alternatives
- MinIO compilé depuis les sources : build Go lourd, pas de correctifs de sécurité.
- Garage : très bon, mais nécessite une étape d'initialisation du layout du cluster.
- RustFS : compatible MinIO mais jeune.

## Conséquences
Revenir à MinIO, passer à AWS S3, Cloudflare R2 ou GCS (interop S3) est un changement de
variables d'environnement uniquement.
