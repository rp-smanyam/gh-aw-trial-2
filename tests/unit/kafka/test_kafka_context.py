from unittest.mock import MagicMock, patch

from agent_leasing.kafka.kafka_context import (
    KafkaApplicationContext,
    kafka_application_context,
)
from agent_leasing.kafka.kafka_producer import EmptyKafkaProducerStub


class TestKafkaApplicationContext:
    """Test cases for KafkaApplicationContext class."""

    def test_init(self):
        """Test KafkaApplicationContext initialization."""
        context = KafkaApplicationContext()
        assert isinstance(context.reporting_data_kafka_producer, EmptyKafkaProducerStub)
        assert isinstance(context.task_activity_producer, EmptyKafkaProducerStub)

    @patch("agent_leasing.kafka.kafka_context.KafkaClusterConfig")
    @patch("agent_leasing.kafka.kafka_context.settings")
    @patch("agent_leasing.kafka.kafka_context.AsyncKafkaProducer")
    @patch("agent_leasing.kafka.kafka_context.AvroSerializer")
    def test_start_with_kafka_configured(
        self, mock_avro_serializer, mock_async_producer, mock_settings, mock_cluster_cls, fake_cluster
    ):
        """Test start method when Kafka is configured."""
        mock_settings.kafka_reporting_enabled = True
        mock_settings.kafka_reporting_data_topic = "test_topic"
        mock_settings.data_curation_schema_id = 123
        mock_settings.data_curation_schema = {"type": "record", "name": "test"}
        mock_cluster_cls.from_settings.return_value = fake_cluster

        mock_serializer_instance = MagicMock()
        mock_avro_serializer.return_value = mock_serializer_instance

        mock_producer_instance = MagicMock()
        mock_async_producer.return_value = mock_producer_instance

        context = KafkaApplicationContext()
        context.start()

        mock_avro_serializer.assert_called_once_with(mock_settings.data_curation_schema, schema_id=123)

        mock_async_producer.assert_called_once()
        call_args = mock_async_producer.call_args
        assert call_args[1]["topic"] == "test_topic"
        # Cluster config delivers the base producer configs; kafka_context
        # no longer builds its own dict.
        assert call_args[1]["configs"] == fake_cluster.producer_configs.return_value
        assert call_args[1]["value_serializer"] == mock_serializer_instance
        assert callable(call_args[1]["key_provider"])

        key_provider = call_args[1]["key_provider"]
        assert key_provider({"conversation_id": "test_conv_id"}) == "test_conv_id"
        assert key_provider({"other_field": "value"}) == "default"

        assert context.reporting_data_kafka_producer == mock_producer_instance

    @patch("agent_leasing.kafka.kafka_context.settings")
    @patch("agent_leasing.kafka.kafka_context.logger")
    def test_start_with_kafka_not_configured(self, mock_logger, mock_settings):
        """Flag off → stub stays."""
        mock_settings.kafka_reporting_enabled = False

        context = KafkaApplicationContext()
        context.start()

        assert isinstance(context.reporting_data_kafka_producer, EmptyKafkaProducerStub)
        mock_logger.info.assert_not_called()

    @patch("agent_leasing.kafka.kafka_context.KafkaClusterConfig")
    @patch("agent_leasing.kafka.kafka_context.settings")
    def test_start_stays_stub_when_cluster_not_configured(self, mock_settings, mock_cluster_cls):
        """Flag on but cluster creds missing → stub stays, no exception."""
        mock_settings.kafka_reporting_enabled = True
        mock_cluster_cls.from_settings.return_value = None

        context = KafkaApplicationContext()
        context.start()

        assert isinstance(context.reporting_data_kafka_producer, EmptyKafkaProducerStub)

    @patch("agent_leasing.kafka.kafka_context.KafkaClusterConfig")
    @patch("agent_leasing.kafka.kafka_context.settings")
    def test_start_stays_stub_when_schema_missing(self, mock_settings, mock_cluster_cls, fake_cluster):
        """Flag on, cluster ok, but schema not loaded → stub stays."""
        mock_settings.kafka_reporting_enabled = True
        mock_settings.data_curation_schema_id = None
        mock_settings.data_curation_schema = {}
        mock_settings.kafka_reporting_data_topic = "t"
        mock_cluster_cls.from_settings.return_value = fake_cluster

        context = KafkaApplicationContext()
        context.start()

        assert isinstance(context.reporting_data_kafka_producer, EmptyKafkaProducerStub)

    def test_send_message_kafka_not_configured(self):
        """Producer still stub → send_message is a no-op."""
        context = KafkaApplicationContext()
        result = context.send_message({"test": "payload"})
        assert result is None

    @patch("agent_leasing.kafka.kafka_context.logger")
    def test_send_message_kafka_configured(self, mock_logger):
        """When a real producer is wired, send_message forwards + acks."""
        mock_producer = MagicMock()
        context = KafkaApplicationContext()
        context.reporting_data_kafka_producer = mock_producer

        payload = {"test": "payload"}
        context.send_message(payload)

        mock_producer.produce.assert_called_once()
        call_args = mock_producer.produce.call_args
        assert call_args[0][0] == payload
        assert callable(call_args[1]["on_delivery"])

        ack_callback = call_args[1]["on_delivery"]

        ack_callback(None, "success_msg")
        mock_logger.warning.assert_not_called()

        ack_callback("error", "error_msg")
        mock_logger.warning.assert_called_once_with(f"Kafka error on ack: error error_msg {payload}")

    def test_close(self):
        mock_producer = MagicMock()
        context = KafkaApplicationContext()
        context.reporting_data_kafka_producer = mock_producer

        context.close()

        mock_producer.close.assert_called_once()

    def test_global_instance_exists(self):
        assert isinstance(kafka_application_context, KafkaApplicationContext)
        assert isinstance(
            kafka_application_context.reporting_data_kafka_producer,
            EmptyKafkaProducerStub,
        )
        assert isinstance(
            kafka_application_context.task_activity_producer,
            EmptyKafkaProducerStub,
        )

    @patch("agent_leasing.kafka.kafka_context.build_task_activity_producer")
    @patch("agent_leasing.kafka.kafka_context.settings")
    def test_start_wires_task_activity_producer_when_configured(self, mock_settings, mock_factory):
        mock_settings.kafka_reporting_enabled = False
        producer_instance = MagicMock()
        producer_instance.start.return_value = True
        mock_factory.return_value = producer_instance

        context = KafkaApplicationContext()
        context.start()

        producer_instance.start.assert_called_once()
        assert context.task_activity_producer is producer_instance

    @patch("agent_leasing.kafka.kafka_context.build_task_activity_producer")
    @patch("agent_leasing.kafka.kafka_context.settings")
    def test_start_soft_fails_when_task_activity_producer_cannot_start(self, mock_settings, mock_factory):
        mock_settings.kafka_reporting_enabled = False
        producer_instance = MagicMock()
        producer_instance.start.return_value = False
        mock_factory.return_value = producer_instance

        context = KafkaApplicationContext()
        context.start()

        assert isinstance(context.task_activity_producer, EmptyKafkaProducerStub)

    @patch("agent_leasing.kafka.kafka_context.build_task_activity_producer")
    @patch("agent_leasing.kafka.kafka_context.settings")
    def test_start_skips_task_activity_producer_when_factory_returns_none(self, mock_settings, mock_factory):
        mock_settings.kafka_reporting_enabled = False
        mock_factory.return_value = None

        context = KafkaApplicationContext()
        context.start()

        assert isinstance(context.task_activity_producer, EmptyKafkaProducerStub)

    def test_close_closes_task_activity_producer(self):
        context = KafkaApplicationContext()
        mock_task_activity_producer = MagicMock()
        mock_reporting_producer = MagicMock()
        context.reporting_data_kafka_producer = mock_reporting_producer
        context.task_activity_producer = mock_task_activity_producer

        context.close()

        mock_reporting_producer.close.assert_called_once()
        mock_task_activity_producer.close.assert_called_once()

    @patch("agent_leasing.kafka.kafka_context.build_task_event_producer")
    @patch("agent_leasing.kafka.kafka_context.settings")
    def test_start_wires_task_event_producer_when_configured(self, mock_settings, mock_factory):
        mock_settings.kafka_reporting_enabled = False
        producer_instance = MagicMock()
        producer_instance.start.return_value = True
        mock_factory.return_value = producer_instance

        context = KafkaApplicationContext()
        context.start()

        producer_instance.start.assert_called_once()
        assert context.task_event_producer is producer_instance

    @patch("agent_leasing.kafka.kafka_context.build_task_event_producer")
    @patch("agent_leasing.kafka.kafka_context.settings")
    def test_start_soft_fails_when_task_event_producer_cannot_start(self, mock_settings, mock_factory):
        mock_settings.kafka_reporting_enabled = False
        producer_instance = MagicMock()
        producer_instance.start.return_value = False
        mock_factory.return_value = producer_instance

        context = KafkaApplicationContext()
        context.start()

        assert isinstance(context.task_event_producer, EmptyKafkaProducerStub)

    @patch("agent_leasing.kafka.kafka_context.build_task_event_producer")
    @patch("agent_leasing.kafka.kafka_context.settings")
    def test_start_skips_task_event_producer_when_factory_returns_none(self, mock_settings, mock_factory):
        mock_settings.kafka_reporting_enabled = False
        mock_factory.return_value = None

        context = KafkaApplicationContext()
        context.start()

        assert isinstance(context.task_event_producer, EmptyKafkaProducerStub)

    def test_close_closes_task_event_producer(self):
        context = KafkaApplicationContext()
        mock_task_event_producer = MagicMock()
        context.reporting_data_kafka_producer = MagicMock()
        context.task_activity_producer = MagicMock()
        context.task_event_producer = mock_task_event_producer

        context.close()

        mock_task_event_producer.close.assert_called_once()
