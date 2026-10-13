#!/usr/bin/env bash
# Initialise the metadata DB, the admin user and import the versioned dashboards.
set -euo pipefail

superset db upgrade
superset fab create-admin \
  --username "${SUPERSET_ADMIN_USER}" --password "${SUPERSET_ADMIN_PASSWORD}" \
  --firstname Admin --lastname SkyRadar --email admin@skyradar.local || true
superset init

# Each folder of /app/dashboards is an exported bundle (YAML, versioned in git).
for dir in /app/dashboards/*/; do
  [ -f "${dir}metadata.yaml" ] || continue
  name=$(basename "$dir")
  (cd /app/dashboards && python -m zipfile -c "/tmp/${name}.zip" "${name}/")
  echo "Importing dashboard bundle ${name}"
  superset import-dashboards -p "/tmp/${name}.zip" -u "${SUPERSET_ADMIN_USER}" \
    || echo "WARN: dashboard import failed for ${name}"
done

exec /usr/bin/run-server.sh
