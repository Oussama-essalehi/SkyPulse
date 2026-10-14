# MLflow tracking server: metadata in Postgres, artifacts in the S3 bucket
# (proxied by the server, so clients never need S3 credentials).
FROM python:3.12-slim
RUN pip install --no-cache-dir "mlflow==3.17.1" "psycopg2-binary>=2.9.10" "boto3>=1.35" \
    && useradd --create-home --uid 1000 mlflow
USER mlflow
EXPOSE 5000
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=8 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=4)" || exit 1
CMD mlflow server --host 0.0.0.0 --port 5000 \
    --backend-store-uri "postgresql+psycopg2://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/mlflow" \
    --artifacts-destination "s3://${MLFLOW_ARTIFACT_BUCKET}/" --serve-artifacts \
    --allowed-hosts "*"
