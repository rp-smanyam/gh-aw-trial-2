from unittest.mock import patch

from agent_leasing.kafka.cluster_config import KafkaClusterConfig


class TestFromSettings:
    @patch("agent_leasing.kafka.cluster_config.settings")
    def test_returns_config_when_all_fields_populated(self, mock_settings):
        mock_settings.kafka_reporting_data_bootstrap_servers = "b:9092"
        mock_settings.kafka_reporting_data_api_key = "k"
        mock_settings.kafka_reporting_data_api_secret = "s"
        mock_settings.kafka_reporting_data_schema_registry_url = "https://sr"
        mock_settings.kafka_reporting_data_schema_api_key = "sk"
        mock_settings.kafka_reporting_data_schema_api_secret = "ss"

        config = KafkaClusterConfig.from_settings()

        assert config is not None
        assert config.bootstrap_servers == "b:9092"
        assert config.api_key == "k"
        assert config.api_secret == "s"
        assert config.schema_registry_url == "https://sr"
        assert config.schema_api_key == "sk"
        assert config.schema_api_secret == "ss"

    @patch("agent_leasing.kafka.cluster_config.settings")
    def test_returns_none_when_any_field_missing(self, mock_settings):
        mock_settings.kafka_reporting_data_bootstrap_servers = "b:9092"
        mock_settings.kafka_reporting_data_api_key = None  # <-- missing
        mock_settings.kafka_reporting_data_api_secret = "s"
        mock_settings.kafka_reporting_data_schema_registry_url = "https://sr"
        mock_settings.kafka_reporting_data_schema_api_key = "sk"
        mock_settings.kafka_reporting_data_schema_api_secret = "ss"

        assert KafkaClusterConfig.from_settings() is None

    @patch("agent_leasing.kafka.cluster_config.settings")
    def test_returns_none_when_empty_string_field(self, mock_settings):
        # Empty strings should be treated the same as None — falsy means
        # "not configured".
        mock_settings.kafka_reporting_data_bootstrap_servers = ""
        mock_settings.kafka_reporting_data_api_key = "k"
        mock_settings.kafka_reporting_data_api_secret = "s"
        mock_settings.kafka_reporting_data_schema_registry_url = "https://sr"
        mock_settings.kafka_reporting_data_schema_api_key = "sk"
        mock_settings.kafka_reporting_data_schema_api_secret = "ss"

        assert KafkaClusterConfig.from_settings() is None


class TestProducerConfigs:
    def test_base_producer_configs_include_sasl_ssl(self):
        config = KafkaClusterConfig(
            bootstrap_servers="b:9092",
            api_key="k",
            api_secret="s",
            schema_registry_url="https://sr",
            schema_api_key="sk",
            schema_api_secret="ss",
        )
        configs = config.producer_configs()
        assert configs["bootstrap.servers"] == "b:9092"
        assert configs["sasl.username"] == "k"
        assert configs["sasl.password"] == "s"
        assert configs["security.protocol"] == "SASL_SSL"
        assert configs["sasl.mechanisms"] == "PLAIN"
        assert configs["acks"] == "all"
        # Key / value serializer are the caller's responsibility — not
        # on the base dict.
        assert "key.serializer" not in configs
        assert "value.serializer" not in configs

    def test_producer_configs_returns_fresh_dict(self):
        # Callers mutate the dict (adding serializers); must not leak
        # across producer instances.
        config = KafkaClusterConfig(
            bootstrap_servers="b:9092",
            api_key="k",
            api_secret="s",
            schema_registry_url="https://sr",
            schema_api_key="sk",
            schema_api_secret="ss",
        )
        first = config.producer_configs()
        first["key.serializer"] = object()
        second = config.producer_configs()
        assert "key.serializer" not in second


class TestSchemaRegistryConfig:
    def test_schema_registry_config_combines_creds(self):
        config = KafkaClusterConfig(
            bootstrap_servers="b:9092",
            api_key="k",
            api_secret="s",
            schema_registry_url="https://sr",
            schema_api_key="sk",
            schema_api_secret="ss",
        )
        sr_config = config.schema_registry_config()
        assert sr_config["url"] == "https://sr"
        assert sr_config["basic.auth.user.info"] == "sk:ss"
