import asyncio
import io
import json
import secrets
import struct
from abc import ABC, abstractmethod
from threading import Thread

import confluent_kafka
import fastavro
from confluent_kafka import KafkaException

from agent_leasing.settings import settings


class KafkaSerializer(ABC):
    @abstractmethod
    def serialize(self, data):
        pass

    @abstractmethod
    def deserialize(self, data):
        pass


class JsonSerializer(KafkaSerializer):
    def serialize(self, data):
        return json.dumps(data).encode("utf-8")

    def deserialize(self, data):
        return json.loads(data.decode("utf-8"))


class AvroSerializer:
    def __init__(self, schema_dict, schema_id=None):
        """Initialize with an Avro schema."""
        self.schema_id = schema_id
        self.schema = fastavro.parse_schema(schema_dict)

    def serialize(self, data):
        """Serialize Python dictionary to Avro binary format."""
        bytes_writer = io.BytesIO()
        with bytes_writer:
            if self.schema_id:
                bytes_writer.write(b"\x00")
                bytes_writer.write(struct.pack(">I", self.schema_id))
            fastavro.schemaless_writer(bytes_writer, self.schema, data)
            return bytes_writer.getvalue()

    def deserialize(self, data):
        """Deserialize Avro binary format back to Python dictionary."""
        if len(data) >= 5 and data[0] == 0x00:
            schema_id = struct.unpack(">I", data[1:5])[0]
            data = data[5:]
            if self.schema_id != schema_id:
                raise ValueError(f"invalid schema_id = {schema_id}, expected = {self.schema_id}")
        bytes_reader = io.BytesIO(data)
        with bytes_reader:
            return fastavro.schemaless_reader(bytes_reader, self.schema)


class EmptyKafkaProducerStub:
    def produce(self, value, on_delivery=None):
        pass

    def close(self):
        pass


class AsyncKafkaProducer:
    def __init__(
        self,
        topic: str,
        configs: dict,
        value_serializer: KafkaSerializer,
        key_provider=None,
        loop=None,
    ):
        self._topic = topic
        self._value_serializer = value_serializer
        self._key_provider = key_provider or (lambda value: self._gen_random_key())
        if loop:
            self._loop = loop
        else:
            try:
                self._loop = asyncio.get_event_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        self._producer = confluent_kafka.Producer(configs)
        self._cancelled = False
        self._poll_thread = Thread(target=self._poll_loop)
        self._poll_thread.start()

    @staticmethod
    def _gen_random_key():
        return secrets.token_urlsafe(32)

    def _poll_loop(self):
        """Background thread that polls for delivery callbacks.

        Poll interval controls CPU vs callback latency trade-off:
        - Message delivery is NOT affected (flush() ensures immediate send)
        - Only callback processing latency is affected by this interval
        """
        while not self._cancelled:
            self._producer.poll(settings.kafka_producer_poll_interval_seconds)

    def close(self):
        self._cancelled = True
        self._poll_thread.join()
        self._producer.flush(10)  # Wait up to 10 seconds for any outstanding messages to be delivered

    def produce(self, value, on_delivery=None):
        serialized_value = self._value_serializer.serialize(value)
        key = self._key_provider(value)

        if on_delivery:
            self._producer.produce(self._topic, key=key, value=serialized_value, on_delivery=on_delivery)
            return None

        result = self._loop.create_future()

        def ack(err, msg):
            if err:
                self._loop.call_soon_threadsafe(result.set_exception, KafkaException(err))
            else:
                self._loop.call_soon_threadsafe(result.set_result, msg)
            if on_delivery:
                self._loop.call_soon_threadsafe(on_delivery, err, msg)

        self._producer.produce(self._topic, key=key, value=serialized_value, on_delivery=ack)
        self._producer.flush()
        return result


class SchemaKafkaProducer:
    def __init__(
        self,
        topic: str,
        configs: dict,
        loop=None,
        key_serializer=None,
        value_serializer=None,
    ):
        configs.update(
            {
                "request.timeout.ms": 10000,  # Increase timeout
            }
        )
        self._topic = topic
        if loop:
            self._loop = loop
        else:
            try:
                self._loop = asyncio.get_event_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        self._producer = confluent_kafka.SerializingProducer(configs)
        self._cancelled = False
        self._poll_thread = Thread(target=self._poll_loop)
        self._poll_thread.start()
        self._key_serializer = key_serializer
        self._value_serializer = value_serializer

    def _poll_loop(self):
        while not self._cancelled:
            self._producer.poll(settings.kafka_producer_poll_interval_seconds)

    @staticmethod
    def _gen_key():
        return secrets.token_urlsafe(32)

    def close(self):
        self._cancelled = True
        self._poll_thread.join()

    def produce(self, value, on_delivery=None):
        """
        Produces a message to Kafka using the SerializingProducer.
        """
        key = value.get("conversation_id", "default")
        if on_delivery:
            self._producer.produce(self._topic, key=key, value=value, on_delivery=on_delivery)
            self._producer.flush()
            return None

        result = self._loop.create_future()

        def ack(err, msg):
            if err:
                self._loop.call_soon_threadsafe(result.set_exception, KafkaException(err))
            else:
                self._loop.call_soon_threadsafe(result.set_result, msg)
            if on_delivery:
                self._loop.call_soon_threadsafe(on_delivery, err, msg)

        self._producer.produce(topic=self._topic, key=key, value=value, on_delivery=ack)
        self._producer.flush()
        return result
