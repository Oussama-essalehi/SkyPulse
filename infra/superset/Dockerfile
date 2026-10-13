FROM apache/superset:4.1.2
USER root
# DuckDB SQLAlchemy dialect: Superset queries the dbt marts (Parquet) through an
# in-memory DuckDB, never the dbt database file.
RUN pip install --no-cache-dir "duckdb>=1.1,<2" "duckdb-engine==0.17.0"
COPY superset_config.py /app/pythonpath/superset_config.py
COPY bootstrap.sh /app/docker/skyradar-bootstrap.sh
RUN chmod +x /app/docker/skyradar-bootstrap.sh
USER superset
# Pre-install the DuckDB extensions used by the datasets.
RUN python -c "import duckdb; duckdb.sql('INSTALL httpfs')"
HEALTHCHECK --interval=20s --timeout=5s --start-period=90s --retries=10 \
    CMD curl -fsS http://127.0.0.1:8088/health || exit 1
CMD ["/app/docker/skyradar-bootstrap.sh"]
