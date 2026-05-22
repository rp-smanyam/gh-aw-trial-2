"""Task-activity-event binding for the generic `RegistryResolvingProducer`.

This file is intentionally thin — the reusable machinery (soft-fail
startup, schema-registry resolution, poll thread, on_delivery logging)
lives in `kafka/registry_producer.py`. Only task-activity-event
specifics live here: the topic setting, the schema-registry subject
(derived from the topic via TopicNameStrategy), and the `task.id` key
extractor.
"""

from agent_leasing.kafka.registry_producer import (
    KafkaProducerProtocol,
    RegistryResolvingProducer,
)
from agent_leasing.settings import settings


def _extract_task_id(value: dict) -> str | None:
    return (value.get("task") or {}).get("id")


def build_task_activity_producer() -> RegistryResolvingProducer | None:
    """Return a ready-to-start producer, or None if disabled / unconfigured.

    Caller is responsible for calling `.start()`; a successful start
    returns True and the producer is ready to `.produce(event)`. Any
    failure in start() is logged and falls back to a stub in the
    caller's hands.
    """
    if not settings.task_activity_event_publishing_enabled:
        return None
    if not settings.kafka_task_activity_topic:
        return None
    # Confluent's default TopicNameStrategy: subject = `<topic>-value`.
    # Topics are env-suffixed (alpha → `task-activity-event-qa`,
    # beta → `task-activity-event-sat`, prod → `task-activity-event`),
    # so the subject must follow the topic in every env.
    subject = f"{settings.kafka_task_activity_topic}-value"
    return RegistryResolvingProducer(
        subject=subject,
        topic=settings.kafka_task_activity_topic,
        key_extractor=_extract_task_id,
        poll_thread_name="task-activity-poll",
        log_prefix="task_activity_producer",
    )


__all__ = [
    "KafkaProducerProtocol",
    "RegistryResolvingProducer",
    "build_task_activity_producer",
]
