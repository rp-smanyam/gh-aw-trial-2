"""Generic registry-resolving Avro producer for topics where the schema
lives in Schema Registry (as opposed to the local-schema + schema-id
pattern used by the data-curation producer in `kafka_producer.py`).

Instantiate one per topic; the task-activity-event producer is the
first caller, the sibling task-event producer will be the second.
Adding a third topic = construct another instance with a different
subject / topic / key extractor.
"""

from collections.abc import Callable
from threading import Thread
from typing import Protocol

import structlog
from confluent_kafka import SerializingProducer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import StringSerializer

from agent_leasing.kafka.cluster_config import KafkaClusterConfig
from agent_leasing.settings import settings

logger = structlog.getLogger()

DEFAULT_KEY = "default"

KeyExtractor = Callable[[dict], str | None]


class KafkaProducerProtocol(Protocol):
    """Shared interface for real producers and no-op stubs."""

    def produce(self, value: dict) -> None: ...
    def close(self) -> None: ...


class RegistryResolvingProducer:
    """Confluent-Kafka `SerializingProducer` with runtime Schema Registry
    resolution. Soft-fails on startup so Kafka / registry outages never
    block agent-leasing boot. A background poll thread drains delivery
    callbacks so ack errors surface under bursty-then-quiet traffic.
    """

    def __init__(
        self,
        *,
        subject: str,
        topic: str,
        key_extractor: KeyExtractor,
        poll_thread_name: str = "kafka-registry-poll",
        log_prefix: str = "registry_producer",
    ) -> None:
        self._subject = subject
        self._topic = topic
        self._key_extractor = key_extractor
        self._poll_thread_name = poll_thread_name
        # Precompute the structured-log event names so the hot path
        # (`produce()` + `on_delivery`) doesn't rebuild the f-strings.
        self._evt_disabled = f"{log_prefix}_disabled"
        self._evt_started = f"{log_prefix}_started"
        self._evt_startup_failed = f"{log_prefix}_startup_failed"
        self._evt_missing_key = f"{log_prefix}_missing_key"
        self._evt_delivery_error = f"{log_prefix}_delivery_error"
        self._producer: SerializingProducer | None = None
        self._cancelled: bool = False
        self._poll_thread: Thread | None = None

    def start(self) -> bool:
        cluster = KafkaClusterConfig.from_settings()
        if cluster is None:
            logger.info(
                self._evt_disabled,
                reason="cluster_not_configured",
                topic=self._topic,
            )
            return False
        try:
            sr_client = SchemaRegistryClient(cluster.schema_registry_config())
            latest = sr_client.get_latest_version(self._subject)
            producer = self._build_producer(cluster, sr_client, latest.schema.schema_str)
            # Only expose `self._producer` AFTER the poll thread is
            # running — otherwise a Thread.start() failure leaves a live
            # producer with no callback drainer, and close() would hang
            # 10s waiting on flush.
            self._cancelled = False
            # daemon=True so uvicorn's signal handler doesn't hang
            # waiting on this thread if close() isn't called.
            self._poll_thread = Thread(
                target=self._poll_loop,
                name=self._poll_thread_name,
                daemon=True,
            )
            self._poll_thread.start()
            self._producer = producer
            logger.info(
                self._evt_started,
                subject=self._subject,
                topic=self._topic,
                bootstrap=cluster.bootstrap_servers,
            )
            return True
        except Exception:
            logger.exception(
                self._evt_startup_failed,
                subject=self._subject,
                topic=self._topic,
            )
            self._producer = None
            self._poll_thread = None
            return False

    @staticmethod
    def _build_producer(
        cluster: KafkaClusterConfig,
        sr_client: SchemaRegistryClient,
        schema_str: str,
    ) -> SerializingProducer:
        configs = cluster.producer_configs()
        configs["key.serializer"] = StringSerializer("utf-8")
        # Cross-team topics own their schemas — we're a producer of an
        # external contract, not the registrar. Pin the serializer to the
        # latest registered version so writes don't require schema-write
        # ACLs (only read).
        configs["value.serializer"] = AvroSerializer(
            sr_client,
            schema_str,
            conf={"auto.register.schemas": False, "use.latest.version": True},
        )
        return SerializingProducer(configs)

    def _poll_loop(self) -> None:
        # Mirrors `AsyncKafkaProducer._poll_loop` — drains delivery
        # callbacks so ack errors surface under bursty-then-quiet traffic.
        while not self._cancelled:
            if self._producer is not None:
                self._producer.poll(settings.kafka_producer_poll_interval_seconds)

    def produce(self, value: dict) -> None:
        if self._producer is None:
            return None

        key = self._key_extractor(value)
        if not key:
            # Silent fallback would collapse partition spread; surface it
            # so the author of the missing-key extractor can fix it.
            logger.warning(
                self._evt_missing_key,
                topic=self._topic,
            )
            key = DEFAULT_KEY

        evt_delivery_error = self._evt_delivery_error
        topic = self._topic

        def _on_delivery(err, _msg):
            if err:
                logger.warning(
                    evt_delivery_error,
                    error=str(err),
                    key=key,
                    topic=topic,
                )

        self._producer.produce(
            topic=self._topic,
            key=key,
            value=value,
            on_delivery=_on_delivery,
        )

    def close(self) -> None:
        if self._producer is None:
            return None
        # Stop the poll thread BEFORE flush so it doesn't race a slow flush.
        self._cancelled = True
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=5)
            self._poll_thread = None
        self._producer.flush(10)
        self._producer = None
