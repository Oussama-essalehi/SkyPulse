#!/usr/bin/env bash
# Initialise the metadata DB, the admin user and import the versioned dashboards.
set -euo pipefail

superset db upgrade
superset fab create-admin \
  --username "${SUPERSET_ADMIN_USER}" --password "${SUPERSET_ADMIN_PASSWORD}" \
  --firstname Admin --lastname SkyRadar --email admin@skyradar.local || true
superset init

for bundle in /app/dashboards/*.zip; do
  [ -e "$bundle" ] || continue
  echo "Importing dashboards from $bundle"
  superset import-dashboards -p "$bundle" -u "${SUPERSET_ADMIN_USER}" || echo "WARN: import failed: $bundle"
done

exec /usr/bin/run-server.sh
