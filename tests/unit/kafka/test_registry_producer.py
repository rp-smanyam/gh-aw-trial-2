from unittest.mock import MagicMock, patch

from agent_leasing.kafka.registry_producer import (
    DEFAULT_KEY,
    RegistryResolvingProducer,
)


def _build_producer(topic="topic-qa", subject="topic-value"):
    return RegistryResolvingProducer(
        subject=subject,
        topic=topic,
        key_extractor=lambda value: (value.get("task") or {}).get("id"),
        poll_thread_name="test-poll",
        log_prefix="test_producer",
    )


class TestStart:
    @patch("agent_leasing.kafka.registry_producer.KafkaClusterConfig")
    def test_returns_false_when_cluster_not_configured(self, mock_cluster_cls):
        mock_cluster_cls.from_settings.return_value = None
        assert _build_producer().start() is False

    @patch("agent_leasing.kafka.registry_producer.Thread")
    @patch("agent_leasing.kafka.registry_producer.SerializingProducer")
    @patch("agent_leasing.kafka.registry_producer.AvroSerializer")
    @patch("agent_leasing.kafka.registry_producer.SchemaRegistryClient")
    @patch("agent_leasing.kafka.registry_producer.KafkaClusterConfig")
    def test_returns_true_on_successful_boot(
        self, mock_cluster_cls, mock_sr_client, _mock_avro, mock_serializing, mock_thread, fake_cluster
    ):
        mock_cluster_cls.from_settings.return_value = fake_cluster
        latest = MagicMock()
        latest.schema.schema_str = '{"type":"record","name":"t","fields":[]}'
        mock_sr_client.return_value.get_latest_version.return_value = latest
        mock_serializing.return_value = MagicMock()
        mock_thread.return_value = MagicMock()

        producer = _build_producer()
        assert producer.start() is True
        mock_thread.return_value.start.assert_called_once()

    @patch("agent_leasing.kafka.registry_producer.SchemaRegistryClient")
    @patch("agent_leasing.kafka.registry_producer.KafkaClusterConfig")
    def test_soft_fails_when_registry_unreachable(self, mock_cluster_cls, mock_sr_client, fake_cluster):
        mock_cluster_cls.from_settings.return_value = fake_cluster
        mock_sr_client.side_effect = RuntimeError("connection refused")
        assert _build_producer().start() is False

    @patch("agent_leasing.kafka.registry_producer.Thread")
    @patch("agent_leasing.kafka.registry_producer.SerializingProducer")
    @patch("agent_leasing.kafka.registry_producer.AvroSerializer")
    @patch("agent_leasing.kafka.registry_producer.SchemaRegistryClient")
    @patch("agent_leasing.kafka.registry_producer.KafkaClusterConfig")
    def test_soft_fails_when_producer_construction_raises(
        self, mock_cluster_cls, mock_sr_client, _mock_avro, mock_serializing, _mock_thread, fake_cluster
    ):
        mock_cluster_cls.from_settings.return_value = fake_cluster
        latest = MagicMock()
        latest.schema.schema_str = "{}"
        mock_sr_client.return_value.get_latest_version.return_value = latest
        mock_serializing.side_effect = RuntimeError("bootstrap unreachable")
        assert _build_producer().start() is False


class TestProduce:
    def test_no_op_before_start(self):
        producer = _build_producer()
        assert producer.produce({"task": {"id": "x"}}) is None

    @patch("agent_leasing.kafka.registry_producer.Thread")
    @patch("agent_leasing.kafka.registry_producer.SerializingProducer")
    @patch("agent_leasing.kafka.registry_producer.AvroSerializer")
    @patch("agent_leasing.kafka.registry_producer.SchemaRegistryClient")
    @patch("agent_leasing.kafka.registry_producer.KafkaClusterConfig")
    def test_forwards_to_serializing_producer(
        self, mock_cluster_cls, mock_sr_client, _mock_avro, mock_serializing, _mock_thread, fake_cluster
    ):
        mock_cluster_cls.from_settings.return_value = fake_cluster
        latest = MagicMock()
        latest.schema.schema_str = "{}"
        mock_sr_client.return_value.get_latest_version.return_value = latest
        mock_instance = MagicMock()
        mock_serializing.return_value = mock_instance

        producer = _build_producer(topic="topic-qa")
        producer.start()
        event = {"task": {"id": "task-uuid"}, "activity": {"summary": "x"}}
        producer.produce(event)

        mock_instance.produce.assert_called_once()
        call_kwargs = mock_instance.produce.call_args.kwargs
        assert call_kwargs["topic"] == "topic-qa"
        assert call_kwargs["key"] == "task-uuid"
        assert call_kwargs["value"] == event
        assert callable(call_kwargs["on_delivery"])

    @patch("agent_leasing.kafka.registry_producer.logger")
    @patch("agent_leasing.kafka.registry_producer.Thread")
    @patch("agent_leasing.kafka.registry_producer.SerializingProducer")
    @patch("agent_leasing.kafka.registry_producer.AvroSerializer")
    @patch("agent_leasing.kafka.registry_producer.SchemaRegistryClient")
    @patch("agent_leasing.kafka.registry_producer.KafkaClusterConfig")
    def test_warns_and_falls_back_to_default_key_when_extractor_returns_none(
        self, mock_cluster_cls, mock_sr_client, _mock_avro, mock_serializing, _mock_thread, mock_logger, fake_cluster
    ):
        mock_cluster_cls.from_settings.return_value = fake_cluster
        latest = MagicMock()
        latest.schema.schema_str = "{}"
        mock_sr_client.return_value.get_latest_version.return_value = latest
        mock_instance = MagicMock()
        mock_serializing.return_value = mock_instance

        producer = _build_producer()
        producer.start()
        producer.produce({"activity": {"summary": "x"}})

        assert mock_instance.produce.call_args.kwargs["key"] == DEFAULT_KEY
        assert any(call.args[0] == "test_producer_missing_key" for call in mock_logger.warning.call_args_list)


class TestClose:
    def test_no_op_when_not_started(self):
        producer = _build_producer()
        assert producer.close() is None

    @patch("agent_leasing.kafka.registry_producer.Thread")
    @patch("agent_leasing.kafka.registry_producer.SerializingProducer")
    @patch("agent_leasing.kafka.registry_producer.AvroSerializer")
    @patch("agent_leasing.kafka.registry_producer.SchemaRegistryClient")
    @patch("agent_leasing.kafka.registry_producer.KafkaClusterConfig")
    def test_joins_poll_thread_and_flushes(
        self, mock_cluster_cls, mock_sr_client, _mock_avro, mock_serializing, mock_thread, fake_cluster
    ):
        mock_cluster_cls.from_settings.return_value = fake_cluster
        latest = MagicMock()
        latest.schema.schema_str = "{}"
        mock_sr_client.return_value.get_latest_version.return_value = latest
        mock_instance = MagicMock()
        mock_serializing.return_value = mock_instance
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        producer = _build_producer()
        producer.start()
        producer.close()

        assert producer._cancelled is True
        mock_thread_instance.join.assert_called_once()
        mock_instance.flush.assert_called_once_with(10)

        mock_instance.produce.reset_mock()
        producer.produce({"task": {"id": "x"}})
        mock_instance.produce.assert_not_called()
