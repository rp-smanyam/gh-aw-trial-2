"""Fire-and-forget binding for task-event publishes."""

import asyncio

from agent_leasing.kafka.fire_and_forget import fire_and_forget_publish
from agent_leasing.kafka.registry_producer import KafkaProducerProtocol
from agent_leasing.settings import settings


def publish_task_event_fire_and_forget(
    producer: KafkaProducerProtocol,
    event: dict,
    pending_tasks: set[asyncio.Task],
) -> asyncio.Task | None:
    """Schedule a task-event publish as a non-blocking background task."""
    return fire_and_forget_publish(
        producer,
        event,
        pending_tasks,
        enabled=settings.task_event_publishing_enabled,
        timeout_seconds=settings.task_event_publish_timeout_seconds,
        log_prefix="task_event",
    )


__all__ = ["publish_task_event_fire_and_forget"]
