"""Shared Confluent Cloud cluster configuration.

One cluster + schema registry hosts every topic agent-leasing produces
to (data-curation today, task-activity-event in KNCK-39556, task-event in
the follow-up). Cluster creds and schema-registry creds are shared. Each
producer adds only its own topic name (and, if using runtime schema
resolution, its subject name).

Reading via `KafkaClusterConfig.from_settings()` keeps producer classes
independent of the specific env var names — if the `kafka_reporting_data_*`
prefix gets renamed later to something cluster-neutral, there's one place
to change.
"""

from dataclasses import dataclass

from agent_leasing.settings import settings


@dataclass(frozen=True)
class KafkaClusterConfig:
    bootstrap_servers: str
    api_key: str
    api_secret: str
    schema_registry_url: str
    schema_api_key: str
    schema_api_secret: str

    @classmethod
    def from_settings(cls) -> "KafkaClusterConfig | None":
        """Return a populated config, or None if any field is missing.

        None tells callers "cluster isn't configured; soft-fail with a stub."
        """
        fields = {
            "bootstrap_servers": settings.kafka_reporting_data_bootstrap_servers,
            "api_key": settings.kafka_reporting_data_api_key,
            "api_secret": settings.kafka_reporting_data_api_secret,
            "schema_registry_url": settings.kafka_reporting_data_schema_registry_url,
            "schema_api_key": settings.kafka_reporting_data_schema_api_key,
            "schema_api_secret": settings.kafka_reporting_data_schema_api_secret,
        }
        if not all(fields.values()):
            return None
        return cls(**fields)

    def producer_configs(self) -> dict:
        """Base `confluent_kafka.SerializingProducer` config; callers add
        topic-specific `key.serializer` / `value.serializer` entries.
        """
        return {
            "bootstrap.servers": self.bootstrap_servers,
            "sasl.username": self.api_key,
            "sasl.password": self.api_secret,
            "acks": "all",
            "sasl.mechanisms": "PLAIN",
            "security.protocol": "SASL_SSL",
            "session.timeout.ms": 45000,
            "retries": 3,
            "retry.backoff.ms": 5000,
        }

    def schema_registry_config(self) -> dict:
        """Config dict for `SchemaRegistryClient`."""
        return {
            "url": self.schema_registry_url,
            "basic.auth.user.info": f"{self.schema_api_key}:{self.schema_api_secret}",
        }
