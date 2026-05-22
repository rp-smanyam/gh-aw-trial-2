from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_cluster() -> MagicMock:
    """Shared `KafkaClusterConfig` double used by kafka_context,
    registry_producer, and task-activity producer tests.
    """
    cluster = MagicMock()
    cluster.bootstrap_servers = "b:9092"
    cluster.schema_registry_config.return_value = {"url": "https://sr"}
    cluster.producer_configs.return_value = {
        "bootstrap.servers": "b:9092",
        "sasl.username": "k",
        "sasl.password": "s",
        "acks": "all",
        "sasl.mechanisms": "PLAIN",
        "security.protocol": "SASL_SSL",
        "session.timeout.ms": 45000,
        "retries": 3,
        "retry.backoff.ms": 5000,
    }
    return cluster
