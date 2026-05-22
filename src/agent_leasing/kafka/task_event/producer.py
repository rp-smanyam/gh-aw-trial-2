"""task-event producer factory — thin binding over RegistryResolvingProducer.

Mirrors `kafka/task_activity/producer.py`. The reusable machinery lives in
`kafka/registry_producer.py`; this file only names the topic, the schema-
registry subject, and the message-key extractor.
"""

from agent_leasing.kafka.registry_producer import (
    KafkaProducerProtocol,
    RegistryResolvingProducer,
)
from agent_leasing.settings import settings


def _extract_task_id(value: dict) -> str | None:
    task = value.get("task")
    if not isinstance(task, dict):
        return None
    return task.get("id")


def build_task_event_producer() -> RegistryResolvingProducer | None:
    """Return a ready-to-start producer, or None if disabled / unconfigured."""
    if not settings.task_event_publishing_enabled:
        return None
    if not settings.kafka_task_event_topic:
        return None
    subject = f"{settings.kafka_task_event_topic}-value"
    return RegistryResolvingProducer(
        subject=subject,
        topic=settings.kafka_task_event_topic,
        key_extractor=_extract_task_id,
        poll_thread_name="task-event-poll",
        log_prefix="task_event_producer",
    )


__all__ = [
    "KafkaProducerProtocol",
    "RegistryResolvingProducer",
    "build_task_event_producer",
]
