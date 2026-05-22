import io
import struct
from unittest.mock import MagicMock, patch

import pytest

from agent_leasing.kafka.kafka_producer import (
    AsyncKafkaProducer,
    AvroSerializer,
    EmptyKafkaProducerStub,
    JsonSerializer,
    KafkaSerializer,
    SchemaKafkaProducer,
)


def key_provider(x):
    return "test_key"


class TestKafkaSerializer:
    """Test cases for KafkaSerializer abstract base class."""

    def test_abstract_methods(self):
        """Test that KafkaSerializer cannot be instantiated directly."""
        with pytest.raises(TypeError):
            KafkaSerializer()


class TestJsonSerializer:
    """Test cases for JsonSerializer class."""

    def test_serialize(self):
        """Test JSON serialization."""
        serializer = JsonSerializer()
        data = {"key": "value", "number": 123}
        result = serializer.serialize(data)

        assert isinstance(result, bytes)
        assert result == b'{"key": "value", "number": 123}'

    def test_deserialize(self):
        """Test JSON deserialization."""
        serializer = JsonSerializer()
        data = b'{"key": "value", "number": 123}'
        result = serializer.deserialize(data)

        assert result == {"key": "value", "number": 123}

    def test_serialize_deserialize_roundtrip(self):
        """Test that serialize/deserialize is a proper roundtrip."""
        serializer = JsonSerializer()
        original_data = {"test": "data", "nested": {"key": "value"}, "list": [1, 2, 3]}

        serialized = serializer.serialize(original_data)
        deserialized = serializer.deserialize(serialized)

        assert deserialized == original_data


class TestAvroSerializer:
    """Test cases for AvroSerializer class."""

    @patch("agent_leasing.kafka.kafka_producer.fastavro")
    def test_init(self, mock_fastavro):
        """Test AvroSerializer initialization."""
        schema_dict = {"type": "record", "name": "test"}
        parsed_schema = {"parsed": "schema"}
        mock_fastavro.parse_schema.return_value = parsed_schema

        serializer = AvroSerializer(schema_dict, schema_id=123)

        assert serializer.schema_id == 123
        assert serializer.schema == parsed_schema
        mock_fastavro.parse_schema.assert_called_once_with(schema_dict)

    @patch("agent_leasing.kafka.kafka_producer.fastavro")
    def test_init_without_schema_id(self, mock_fastavro):
        """Test AvroSerializer initialization without schema_id."""
        schema_dict = {"type": "record", "name": "test"}
        parsed_schema = {"parsed": "schema"}
        mock_fastavro.parse_schema.return_value = parsed_schema

        serializer = AvroSerializer(schema_dict)

        assert serializer.schema_id is None
        assert serializer.schema == parsed_schema

    @patch("agent_leasing.kafka.kafka_producer.fastavro")
    def test_serialize_with_schema_id(self, mock_fastavro):
        """Test serialization with schema_id."""
        schema_dict = {"type": "record", "name": "test"}
        serializer = AvroSerializer(schema_dict, schema_id=123)

        data = {"field": "value"}
        mock_fastavro.schemaless_writer = MagicMock()

        result = serializer.serialize(data)

        # Check that result starts with magic byte and schema_id
        assert result[:1] == b"\x00"
        assert struct.unpack(">I", result[1:5])[0] == 123

        # Verify schemaless_writer was called
        mock_fastavro.schemaless_writer.assert_called_once()
        call_args = mock_fastavro.schemaless_writer.call_args[0]
        assert isinstance(call_args[0], io.BytesIO)
        assert call_args[1] == serializer.schema
        assert call_args[2] == data

    @patch("agent_leasing.kafka.kafka_producer.fastavro")
    def test_serialize_without_schema_id(self, mock_fastavro):
        """Test serialization without schema_id."""
        schema_dict = {"type": "record", "name": "test"}
        serializer = AvroSerializer(schema_dict)

        data = {"field": "value"}
        mock_fastavro.schemaless_writer = MagicMock()

        serializer.serialize(data)

        # Should not have magic byte and schema_id prefix
        mock_fastavro.schemaless_writer.assert_called_once()

    @patch("agent_leasing.kafka.kafka_producer.fastavro")
    def test_deserialize_with_schema_id(self, mock_fastavro):
        """Test deserialization with schema_id."""
        schema_dict = {"type": "record", "name": "test"}
        serializer = AvroSerializer(schema_dict, schema_id=123)

        # Create data with magic byte and schema_id
        schema_id_bytes = struct.pack(">I", 123)
        data = b"\x00" + schema_id_bytes + b"avro_data"

        expected_result = {"field": "value"}
        mock_fastavro.schemaless_reader.return_value = expected_result

        result = serializer.deserialize(data)

        assert result == expected_result
        mock_fastavro.schemaless_reader.assert_called_once()
        call_args = mock_fastavro.schemaless_reader.call_args[0]
        assert isinstance(call_args[0], io.BytesIO)
        assert call_args[1] == serializer.schema

    @patch("agent_leasing.kafka.kafka_producer.fastavro")
    def test_deserialize_with_wrong_schema_id(self, mock_fastavro):
        """Test deserialization with wrong schema_id raises error."""
        schema_dict = {"type": "record", "name": "test"}
        serializer = AvroSerializer(schema_dict, schema_id=123)

        # Create data with wrong schema_id
        schema_id_bytes = struct.pack(">I", 456)
        data = b"\x00" + schema_id_bytes + b"avro_data"

        with pytest.raises(ValueError, match="invalid schema_id = 456, expected = 123"):
            serializer.deserialize(data)

    @patch("agent_leasing.kafka.kafka_producer.fastavro")
    def test_deserialize_without_schema_id_prefix(self, mock_fastavro):
        """Test deserialization without schema_id prefix."""
        schema_dict = {"type": "record", "name": "test"}
        serializer = AvroSerializer(schema_dict, schema_id=123)

        data = b"avro_data_without_prefix"
        expected_result = {"field": "value"}
        mock_fastavro.schemaless_reader.return_value = expected_result

        result = serializer.deserialize(data)

        assert result == expected_result
        mock_fastavro.schemaless_reader.assert_called_once()


class TestEmptyKafkaProducerStub:
    """Test cases for EmptyKafkaProducerStub class."""

    def test_produce(self):
        """Test produce method does nothing."""
        stub = EmptyKafkaProducerStub()
        # Should not raise any exceptions
        stub.produce("value")

        def noop_on_delivery(err, msg):
            return None

        stub.produce("value", on_delivery=noop_on_delivery)

    def test_close(self):
        """Test close method does nothing."""
        stub = EmptyKafkaProducerStub()
        # Should not raise any exceptions
        stub.close()


class TestAsyncKafkaProducer:
    """Test cases for AsyncKafkaProducer class."""

    @patch("agent_leasing.kafka.kafka_producer.confluent_kafka.Producer")
    @patch("agent_leasing.kafka.kafka_producer.Thread")
    @patch("agent_leasing.kafka.kafka_producer.asyncio.get_event_loop")
    def test_init(self, mock_get_loop, mock_thread, mock_producer_class):
        """Test AsyncKafkaProducer initialization."""
        mock_loop = MagicMock()
        mock_get_loop.return_value = mock_loop
        mock_producer = MagicMock()
        mock_producer_class.return_value = mock_producer
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        topic = "test_topic"
        configs = {"bootstrap.servers": "localhost:9092"}
        value_serializer = MagicMock()

        producer = AsyncKafkaProducer(
            topic=topic,
            configs=configs,
            value_serializer=value_serializer,
            key_provider=key_provider,
            loop=mock_loop,
        )

        assert producer._topic == topic
        assert producer._value_serializer == value_serializer
        assert producer._key_provider == key_provider
        assert producer._loop == mock_loop
        assert producer._producer == mock_producer
        assert producer._cancelled is False

        mock_producer_class.assert_called_once_with(configs)
        mock_thread.assert_called_once_with(target=producer._poll_loop)
        mock_thread_instance.start.assert_called_once()

    @patch("agent_leasing.kafka.kafka_producer.confluent_kafka.Producer")
    @patch("agent_leasing.kafka.kafka_producer.Thread")
    @patch("agent_leasing.kafka.kafka_producer.asyncio.get_event_loop")
    def test_init_with_default_key_provider(self, mock_get_loop, mock_thread, mock_producer_class):
        """Test AsyncKafkaProducer initialization with default key provider."""
        mock_loop = MagicMock()
        mock_get_loop.return_value = mock_loop
        mock_producer = MagicMock()
        mock_producer_class.return_value = mock_producer
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        producer = AsyncKafkaProducer(topic="test_topic", configs={}, value_serializer=MagicMock())

        # Test that default key provider generates random keys
        key1 = producer._key_provider({})
        key2 = producer._key_provider({})
        assert isinstance(key1, str)
        assert isinstance(key2, str)
        assert key1 != key2  # Should be random

    @patch("agent_leasing.kafka.kafka_producer.secrets.token_urlsafe")
    def test_gen_random_key(self, mock_token):
        """Test _gen_random_key static method."""
        mock_token.return_value = "random_key_123"

        result = AsyncKafkaProducer._gen_random_key()

        assert result == "random_key_123"
        mock_token.assert_called_once_with(32)

    @patch("agent_leasing.kafka.kafka_producer.confluent_kafka.Producer")
    @patch("agent_leasing.kafka.kafka_producer.Thread")
    def test_poll_loop(self, mock_thread, mock_producer_class):
        """Test _poll_loop method."""
        mock_producer = MagicMock()
        mock_producer_class.return_value = mock_producer
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        producer = AsyncKafkaProducer(topic="test_topic", configs={}, value_serializer=MagicMock())

        # Simulate poll loop running a few times then stopping
        poll_count = 0

        def side_effect(*args):
            nonlocal poll_count
            poll_count += 1
            if poll_count >= 3:
                producer._cancelled = True

        mock_producer.poll.side_effect = side_effect

        producer._poll_loop()

        assert poll_count == 3
        assert mock_producer.poll.call_count == 3

    @patch("agent_leasing.kafka.kafka_producer.confluent_kafka.Producer")
    @patch("agent_leasing.kafka.kafka_producer.Thread")
    def test_close(self, mock_thread, mock_producer_class):
        """Test close method."""
        mock_producer = MagicMock()
        mock_producer_class.return_value = mock_producer
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        producer = AsyncKafkaProducer(topic="test_topic", configs={}, value_serializer=MagicMock())

        producer.close()

        assert producer._cancelled is True
        mock_thread_instance.join.assert_called_once()
        mock_producer.flush.assert_called_once()

    @patch("agent_leasing.kafka.kafka_producer.confluent_kafka.Producer")
    @patch("agent_leasing.kafka.kafka_producer.Thread")
    def test_produce_with_on_delivery(self, mock_thread, mock_producer_class):
        """Test produce method with on_delivery callback."""
        mock_producer = MagicMock()
        mock_producer_class.return_value = mock_producer
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        mock_serializer = MagicMock()
        mock_serializer.serialize.return_value = b"serialized_value"

        producer = AsyncKafkaProducer(
            topic="test_topic",
            configs={},
            value_serializer=mock_serializer,
            key_provider=key_provider,
        )

        value = {"test": "data"}
        on_delivery = MagicMock()

        result = producer.produce(value, on_delivery=on_delivery)

        assert result is None
        mock_serializer.serialize.assert_called_once_with(value)
        mock_producer.produce.assert_called_once_with(
            "test_topic",
            key="test_key",
            value=b"serialized_value",
            on_delivery=on_delivery,
        )
        mock_producer.flush.assert_not_called()

    @patch("agent_leasing.kafka.kafka_producer.confluent_kafka.Producer")
    @patch("agent_leasing.kafka.kafka_producer.Thread")
    def test_produce_without_on_delivery(self, mock_thread, mock_producer_class):
        """Test produce method without on_delivery callback."""
        mock_producer = MagicMock()
        mock_producer_class.return_value = mock_producer
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        mock_loop = MagicMock()
        mock_future = MagicMock()
        mock_loop.create_future.return_value = mock_future

        mock_serializer = MagicMock()
        mock_serializer.serialize.return_value = b"serialized_value"

        producer = AsyncKafkaProducer(
            topic="test_topic",
            configs={},
            value_serializer=mock_serializer,
            key_provider=key_provider,
            loop=mock_loop,
        )

        value = {"test": "data"}

        result = producer.produce(value)

        assert result == mock_future
        mock_loop.create_future.assert_called_once()
        mock_producer.produce.assert_called_once()

        # Test the ack callback
        call_args = mock_producer.produce.call_args
        ack_callback = call_args[1]["on_delivery"]

        # Test successful ack
        ack_callback(None, "success_msg")
        mock_loop.call_soon_threadsafe.assert_called_with(mock_future.set_result, "success_msg")

        # Reset mock and test error ack
        mock_loop.call_soon_threadsafe.reset_mock()
        with patch("agent_leasing.kafka.kafka_producer.KafkaException") as mock_kafka_exception:
            mock_exception = MagicMock()
            mock_kafka_exception.return_value = mock_exception

            ack_callback("error", "error_msg")
            mock_kafka_exception.assert_called_once_with("error")
            mock_loop.call_soon_threadsafe.assert_called_with(mock_future.set_exception, mock_exception)


class TestSchemaKafkaProducer:
    """Test cases for SchemaKafkaProducer class."""

    @patch("agent_leasing.kafka.kafka_producer.confluent_kafka.SerializingProducer")
    @patch("agent_leasing.kafka.kafka_producer.Thread")
    @patch("agent_leasing.kafka.kafka_producer.asyncio.get_event_loop")
    def test_init(self, mock_get_loop, mock_thread, mock_producer_class):
        """Test SchemaKafkaProducer initialization."""
        mock_loop = MagicMock()
        mock_get_loop.return_value = mock_loop
        mock_producer = MagicMock()
        mock_producer_class.return_value = mock_producer
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        topic = "test_topic"
        configs = {"bootstrap.servers": "localhost:9092"}
        key_serializer = MagicMock()
        value_serializer = MagicMock()

        producer = SchemaKafkaProducer(
            topic=topic,
            configs=configs,
            loop=mock_loop,
            key_serializer=key_serializer,
            value_serializer=value_serializer,
        )

        assert producer._topic == topic
        assert producer._loop == mock_loop
        assert producer._producer == mock_producer
        assert producer._cancelled is False
        assert producer._key_serializer == key_serializer
        assert producer._value_serializer == value_serializer

        expected_configs = configs.copy()
        expected_configs.update({"request.timeout.ms": 10000})  # noqa
        mock_producer_class.assert_called_once_with(expected_configs)
        mock_thread.assert_called_once_with(target=producer._poll_loop)
        mock_thread_instance.start.assert_called_once()

    @patch("agent_leasing.kafka.kafka_producer.secrets.token_urlsafe")
    def test_gen_key(self, mock_token):
        """Test _gen_key static method."""
        mock_token.return_value = "random_key_456"

        result = SchemaKafkaProducer._gen_key()

        assert result == "random_key_456"
        mock_token.assert_called_once_with(32)

    @patch("agent_leasing.kafka.kafka_producer.confluent_kafka.SerializingProducer")
    @patch("agent_leasing.kafka.kafka_producer.Thread")
    def test_close(self, mock_thread, mock_producer_class):
        """Test close method."""
        mock_producer = MagicMock()
        mock_producer_class.return_value = mock_producer
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        producer = SchemaKafkaProducer(topic="test_topic", configs={})

        producer.close()

        assert producer._cancelled is True
        mock_thread_instance.join.assert_called_once()

    @patch("agent_leasing.kafka.kafka_producer.confluent_kafka.SerializingProducer")
    @patch("agent_leasing.kafka.kafka_producer.Thread")
    def test_produce_with_on_delivery(self, mock_thread, mock_producer_class):
        """Test produce method with on_delivery callback."""
        mock_producer = MagicMock()
        mock_producer_class.return_value = mock_producer
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        producer = SchemaKafkaProducer(topic="test_topic", configs={})

        value = {"conversation_id": "test_conv_id", "data": "test"}
        on_delivery = MagicMock()

        result = producer.produce(value, on_delivery=on_delivery)

        assert result is None
        mock_producer.produce.assert_called_once_with(
            "test_topic", key="test_conv_id", value=value, on_delivery=on_delivery
        )
        mock_producer.flush.assert_called_once()

    @patch("agent_leasing.kafka.kafka_producer.confluent_kafka.SerializingProducer")
    @patch("agent_leasing.kafka.kafka_producer.Thread")
    def test_produce_without_conversation_id(self, mock_thread, mock_producer_class):
        """Test produce method without conversation_id uses default."""
        mock_producer = MagicMock()
        mock_producer_class.return_value = mock_producer
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        producer = SchemaKafkaProducer(topic="test_topic", configs={})

        value = {"data": "test"}
        on_delivery = MagicMock()

        producer.produce(value, on_delivery=on_delivery)

        mock_producer.produce.assert_called_once_with(
            "test_topic", key="default", value=value, on_delivery=on_delivery
        )

    @patch("agent_leasing.kafka.kafka_producer.confluent_kafka.SerializingProducer")
    @patch("agent_leasing.kafka.kafka_producer.Thread")
    def test_produce_without_on_delivery(self, mock_thread, mock_producer_class):
        """Test produce method without on_delivery callback."""
        mock_producer = MagicMock()
        mock_producer_class.return_value = mock_producer
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        mock_loop = MagicMock()
        mock_future = MagicMock()
        mock_loop.create_future.return_value = mock_future

        producer = SchemaKafkaProducer(topic="test_topic", configs={}, loop=mock_loop)

        value = {"conversation_id": "test_conv_id", "data": "test"}

        result = producer.produce(value)

        assert result == mock_future
        mock_loop.create_future.assert_called_once()
        mock_producer.produce.assert_called_once()

        # Test the ack callback
        call_args = mock_producer.produce.call_args
        ack_callback = call_args[1]["on_delivery"]

        # Test successful ack
        ack_callback(None, "success_msg")
        mock_loop.call_soon_threadsafe.assert_called_with(mock_future.set_result, "success_msg")

        # Reset mock and test error ack
        mock_loop.call_soon_threadsafe.reset_mock()
        with patch("agent_leasing.kafka.kafka_producer.KafkaException") as mock_kafka_exception:
            mock_exception = MagicMock()
            mock_kafka_exception.return_value = mock_exception

            ack_callback("error", "error_msg")
            mock_kafka_exception.assert_called_once_with("error")
            mock_loop.call_soon_threadsafe.assert_called_with(mock_future.set_exception, mock_exception)
