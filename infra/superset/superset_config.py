"""Superset configuration for SkyRadar (mounted at /app/pythonpath)."""

import os

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
SQLALCHEMY_DATABASE_URI = (
    f"postgresql+psycopg2://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    "@postgres:5432/superset"
)
# Local, single-node deployment.
TALISMAN_ENABLED = False
WTF_CSRF_ENABLED = True
FEATURE_FLAGS = {"DASHBOARD_NATIVE_FILTERS": True, "DASHBOARD_CROSS_FILTERS": True}
CACHE_CONFIG = {"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 300}
DATA_CACHE_CONFIG = {"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 300}
# Marts are refreshed hourly: a 5 minute chart cache is plenty.
SUPERSET_WEBSERVER_TIMEOUT = 120
