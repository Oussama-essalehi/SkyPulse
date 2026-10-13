import ssl

from skyradar_common.kafka import KafkaSettings


def test_plaintext_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "redpanda:9092")
    options = KafkaSettings().client_options()
    assert options == {"bootstrap_servers": "redpanda:9092", "security_protocol": "PLAINTEXT"}


def test_sasl_ssl_for_managed_kafka(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    for key, value in {
        "KAFKA_BOOTSTRAP_SERVERS": "seed.cloud.redpanda.com:9092",
        "KAFKA_SECURITY_PROTOCOL": "SASL_SSL",
        "KAFKA_SASL_MECHANISM": "SCRAM-SHA-256",
        "KAFKA_SASL_USERNAME": "skyradar",
        "KAFKA_SASL_PASSWORD": "secret",
    }.items():
        monkeypatch.setenv(key, value)
    options = KafkaSettings().client_options()
    assert options["sasl_mechanism"] == "SCRAM-SHA-256"
    assert options["sasl_plain_username"] == "skyradar"
    assert isinstance(options["ssl_context"], ssl.SSLContext)
