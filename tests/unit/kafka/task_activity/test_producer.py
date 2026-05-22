"""Tests for the thin task-activity-event factory.

Producer lifecycle / soft-fail / poll-thread / key extraction are tested
against the generic `RegistryResolvingProducer` in
`tests/unit/kafka/test_registry_producer.py`. This file only tests the
task-activity binding: subject name, topic wiring, and the factory's
flag/topic guards.
"""

from unittest.mock import patch

from agent_leasing.kafka.task_activity.producer import (
    _extract_task_id,
    build_task_activity_producer,
)


class TestExtractTaskId:
    def test_returns_task_id_when_present(self):
        assert _extract_task_id({"task": {"id": "task-uuid"}}) == "task-uuid"

    def test_returns_none_when_task_missing(self):
        assert _extract_task_id({"activity": {"summary": "x"}}) is None

    def test_returns_none_when_task_has_no_id(self):
        assert _extract_task_id({"task": {}}) is None

    def test_returns_none_when_task_is_not_dict(self):
        # Defensive: upstream could pass malformed payloads.
        assert _extract_task_id({"task": None}) is None


class TestBuildTaskActivityProducer:
    @patch("agent_leasing.kafka.task_activity.producer.settings")
    def test_returns_none_when_flag_disabled(self, mock_settings):
        mock_settings.task_activity_event_publishing_enabled = False
        assert build_task_activity_producer() is None

    @patch("agent_leasing.kafka.task_activity.producer.settings")
    def test_returns_none_when_topic_missing(self, mock_settings):
        mock_settings.task_activity_event_publishing_enabled = True
        mock_settings.kafka_task_activity_topic = None
        assert build_task_activity_producer() is None

    @patch("agent_leasing.kafka.task_activity.producer.settings")
    def test_returns_configured_producer(self, mock_settings):
        mock_settings.task_activity_event_publishing_enabled = True
        mock_settings.kafka_task_activity_topic = "task-activity-event-qa"

        producer = build_task_activity_producer()

        assert producer is not None
        # Subject derives from topic via TopicNameStrategy, so it stays
        # in lockstep with the env-suffixed topic name.
        assert producer._subject == "task-activity-event-qa-value"
        assert producer._topic == "task-activity-event-qa"
        assert producer._key_extractor is _extract_task_id
        # Log event names are precomputed from log_prefix in __init__.
        assert producer._evt_started == "task_activity_producer_started"
        assert producer._poll_thread_name == "task-activity-poll"

    @patch("agent_leasing.kafka.task_activity.producer.settings")
    def test_subject_follows_topic_for_prod(self, mock_settings):
        mock_settings.task_activity_event_publishing_enabled = True
        mock_settings.kafka_task_activity_topic = "task-activity-event"
        producer = build_task_activity_producer()
        assert producer._subject == "task-activity-event-value"
