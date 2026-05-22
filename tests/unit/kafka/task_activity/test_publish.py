"""Tests for the thin task-activity `publish` binding.

Fire-and-forget / timeout / exception semantics are tested against the
generic helper in `tests/unit/kafka/test_fire_and_forget.py`. This file
only checks the binding wires its settings through correctly.
"""

import asyncio
from unittest.mock import MagicMock, patch


@patch("agent_leasing.kafka.task_activity.publish.fire_and_forget_publish")
@patch("agent_leasing.kafka.task_activity.publish.settings")
def test_binding_passes_through_task_activity_settings(mock_settings, mock_fire_and_forget):
    from agent_leasing.kafka.task_activity.publish import publish_task_activity_fire_and_forget

    mock_settings.task_activity_event_publishing_enabled = True
    mock_settings.task_activity_publish_timeout_seconds = 0.25
    producer = MagicMock()
    pending: set[asyncio.Task] = set()
    event = {"task": {"id": "t"}}

    publish_task_activity_fire_and_forget(producer, event, pending)

    mock_fire_and_forget.assert_called_once_with(
        producer,
        event,
        pending,
        enabled=True,
        timeout_seconds=0.25,
        log_prefix="task_activity",
        on_success=None,
    )


def test_drain_is_re_exported_from_generic_helper():
    # Both names must resolve to the same function object so call sites
    # don't fall out of sync.
    from agent_leasing.kafka.fire_and_forget import drain_pending_publishes as generic
    from agent_leasing.kafka.task_activity.publish import drain_pending_publishes as binding

    assert generic is binding
