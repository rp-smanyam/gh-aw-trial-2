import structlog

from agent_leasing.kafka.cluster_config import KafkaClusterConfig
from agent_leasing.kafka.kafka_producer import (
    AsyncKafkaProducer,
    AvroSerializer,
    EmptyKafkaProducerStub,
)
from agent_leasing.kafka.task_activity import (
    KafkaProducerProtocol,
    build_task_activity_producer,
)
from agent_leasing.kafka.task_event import build_task_event_producer
from agent_leasing.settings import settings

logger = structlog.getLogger()


class KafkaApplicationContext:
    def __init__(self):
        self.reporting_data_kafka_producer = EmptyKafkaProducerStub()
        self.task_activity_producer: KafkaProducerProtocol = EmptyKafkaProducerStub()
        self.task_event_producer: KafkaProducerProtocol = EmptyKafkaProducerStub()

    def start(self):
        """Create Kafka producers."""
        if settings.kafka_reporting_enabled:
            self._start_reporting_data_producer()

        # Factory returns None when disabled / topic unconfigured;
        # start() returns False on any other boot failure. Either way
        # the stub stays in place — agent-leasing must not fail to boot.
        task_activity = build_task_activity_producer()
        if task_activity is not None and task_activity.start():
            self.task_activity_producer = task_activity

        task_event = build_task_event_producer()
        if task_event is not None and task_event.start():
            self.task_event_producer = task_event

    def _start_reporting_data_producer(self):
        cluster = KafkaClusterConfig.from_settings()
        if cluster is None:
            return
        if not (
            settings.data_curation_schema_id and settings.data_curation_schema and settings.kafka_reporting_data_topic
        ):
            return

        logger.info(f"Starting Kafka application context: {cluster.bootstrap_servers}")
        avro_serializer = AvroSerializer(
            settings.data_curation_schema,
            schema_id=settings.data_curation_schema_id,
        )

        def get_conversation_id(value):
            return value.get("conversation_id", "default")

        self.reporting_data_kafka_producer = AsyncKafkaProducer(
            topic=settings.kafka_reporting_data_topic,
            configs=cluster.producer_configs(),
            value_serializer=avro_serializer,  # noqa
            key_provider=get_conversation_id,
        )

    def send_message(self, payload):
        if isinstance(self.reporting_data_kafka_producer, EmptyKafkaProducerStub):
            return

        def ack(err, msg):
            if err:
                logger.warning(f"Kafka error on ack: {err} {msg} {payload}")

        self.reporting_data_kafka_producer.produce(payload, on_delivery=ack)

    def close(self):
        self.reporting_data_kafka_producer.close()
        self.task_activity_producer.close()
        self.task_event_producer.close()


kafka_application_context = KafkaApplicationContext()
