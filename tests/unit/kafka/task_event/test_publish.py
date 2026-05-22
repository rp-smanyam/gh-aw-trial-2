"""Tests for the thin task-event `publish` binding.

Fire-and-forget / timeout / exception semantics are tested against the
generic helper in `tests/unit/kafka/test_fire_and_forget.py`. This file
only checks the binding wires its settings through correctly.
"""

import asyncio
from unittest.mock import MagicMock, patch


@patch("agent_leasing.kafka.task_event.publish.fire_and_forget_publish")
@patch("agent_leasing.kafka.task_event.publish.settings")
def test_binding_passes_through_task_event_settings(mock_settings, mock_fire_and_forget):
    from agent_leasing.kafka.task_event.publish import publish_task_event_fire_and_forget

    mock_settings.task_event_publishing_enabled = True
    mock_settings.task_event_publish_timeout_seconds = 0.5
    producer = MagicMock()
    pending: set[asyncio.Task] = set()
    event = {"task": {"id": "t"}}

    publish_task_event_fire_and_forget(producer, event, pending)

    mock_fire_and_forget.assert_called_once_with(
        producer,
        event,
        pending,
        enabled=True,
        timeout_seconds=0.5,
        log_prefix="task_event",
    )
