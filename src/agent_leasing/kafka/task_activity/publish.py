"""Task-activity-event binding for the generic `fire_and_forget_publish`."""

import asyncio
from typing import Callable

from agent_leasing.kafka.fire_and_forget import (
    drain_pending_publishes,
    fire_and_forget_publish,
)
from agent_leasing.kafka.registry_producer import KafkaProducerProtocol
from agent_leasing.settings import settings


def publish_task_activity_fire_and_forget(
    producer: KafkaProducerProtocol,
    event: dict,
    pending_tasks: set[asyncio.Task],
    *,
    on_success: Callable[[], None] | None = None,
) -> asyncio.Task | None:
    """Schedule a task-activity-event publish as a background task.

    `on_success` runs only when delivery is confirmed — used for
    delivery-time dedup (e.g., FRUSTRATED_USER once-per-conversation).
    """
    return fire_and_forget_publish(
        producer,
        event,
        pending_tasks,
        enabled=settings.task_activity_event_publishing_enabled,
        timeout_seconds=settings.task_activity_publish_timeout_seconds,
        log_prefix="task_activity",
        on_success=on_success,
    )


__all__ = [
    "drain_pending_publishes",
    "publish_task_activity_fire_and_forget",
]
