"""Tests for the thin task-event factory.

Producer lifecycle / soft-fail / poll-thread / key extraction are tested
against the generic `RegistryResolvingProducer` in
`tests/unit/kafka/test_registry_producer.py`. This file only tests the
task-event binding: subject name, topic wiring, and the factory's
flag/topic guards.
"""

from unittest.mock import patch

from agent_leasing.kafka.task_event.producer import (
    _extract_task_id,
    build_task_event_producer,
)


class TestExtractTaskId:
    def test_returns_task_id_when_present(self):
        assert _extract_task_id({"task": {"id": "task-uuid"}}) == "task-uuid"

    def test_returns_none_when_task_missing(self):
        assert _extract_task_id({"event_id": "x"}) is None

    def test_returns_none_when_task_has_no_id(self):
        assert _extract_task_id({"task": {}}) is None

    def test_returns_none_when_task_is_not_dict(self):
        # Defensive: upstream could pass malformed payloads. Test a truthy
        # non-dict so the isinstance guard is exercised (a falsy None would
        # short-circuit on the earlier `value.get("task") or {}` pattern).
        assert _extract_task_id({"task": "bad"}) is None
        assert _extract_task_id({"task": [1, 2, 3]}) is None
        assert _extract_task_id({"task": None}) is None


class TestBuildTaskEventProducer:
    @patch("agent_leasing.kafka.task_event.producer.settings")
    def test_returns_none_when_flag_disabled(self, mock_settings):
        mock_settings.task_event_publishing_enabled = False
        assert build_task_event_producer() is None

    @patch("agent_leasing.kafka.task_event.producer.settings")
    def test_returns_none_when_topic_missing(self, mock_settings):
        mock_settings.task_event_publishing_enabled = True
        mock_settings.kafka_task_event_topic = None
        assert build_task_event_producer() is None

    @patch("agent_leasing.kafka.task_event.producer.settings")
    def test_returns_configured_producer(self, mock_settings):
        mock_settings.task_event_publishing_enabled = True
        mock_settings.kafka_task_event_topic = "task-event-qa"

        producer = build_task_event_producer()

        assert producer is not None
        assert producer._subject == "task-event-qa-value"
        assert producer._topic == "task-event-qa"
        assert producer._key_extractor is _extract_task_id
        assert producer._evt_started == "task_event_producer_started"
        assert producer._poll_thread_name == "task-event-poll"
