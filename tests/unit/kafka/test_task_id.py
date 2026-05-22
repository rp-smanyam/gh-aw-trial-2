from types import SimpleNamespace
from uuid import UUID

import pytest

from agent_leasing.kafka.task_id import (
    AGENT_LEASING_TASK_NAMESPACE,
    build_task_id,
    derive_conversation_key,
)


def _ctx(*, conversation_type: str, chat_session_id: str, session_marker: str = "marker-x"):
    return SimpleNamespace(
        ask_request=SimpleNamespace(
            chat_session_id=chat_session_id,
            conversation_type=SimpleNamespace(value=conversation_type),
        ),
        session_marker=session_marker,
    )


def test_namespace_is_a_stable_uuid():
    # Guard against accidentally rotating the namespace — doing so would
    # orphan every in-flight task.id from the sibling task-event topic.
    assert str(AGENT_LEASING_TASK_NAMESPACE) == "3c9f8a7b-2e1d-4f6a-8b5c-0d9e7f3a6c1b"


def test_build_task_id_returns_a_valid_uuid_string():
    task_id = build_task_id("CHAT", "abc-123")
    UUID(task_id)


def test_build_task_id_is_deterministic():
    a = build_task_id("CHAT", "abc-123")
    b = build_task_id("CHAT", "abc-123")
    assert a == b


def test_build_task_id_differs_by_channel():
    chat = build_task_id("CHAT", "abc-123")
    sms = build_task_id("SMS", "abc-123")
    assert chat != sms


def test_build_task_id_differs_by_conversation_key():
    a = build_task_id("CHAT", "abc-123")
    b = build_task_id("CHAT", "xyz-456")
    assert a != b


@pytest.mark.parametrize("channel,conversation_key", [("", "k"), ("CHAT", ""), (None, "k"), ("CHAT", None)])
def test_build_task_id_rejects_empty_inputs(channel, conversation_key):
    with pytest.raises(ValueError):
        build_task_id(channel, conversation_key)


class TestDeriveConversationKey:
    """Single source of truth for (channel, conversation_key) per SessionScope.

    Both the task-event and task-activity-event topics call this so they
    end up with the same task.id per conversation.
    """

    @pytest.mark.parametrize("conversation_type", ["voice", "chat"])
    def test_voice_chat_use_chat_session_id_directly(self, conversation_type):
        # chat_session_id IS the session boundary for voice (per-call) and
        # chat (upstream rotates on TTL), so session_marker is irrelevant.
        ctx = _ctx(conversation_type=conversation_type, chat_session_id="abc")
        channel, key = derive_conversation_key(ctx)
        assert channel == conversation_type.upper()
        assert key == "abc"

    @pytest.mark.parametrize("conversation_type", ["voice", "chat"])
    def test_voice_chat_ignore_session_marker(self, conversation_type):
        a = derive_conversation_key(
            _ctx(conversation_type=conversation_type, chat_session_id="s", session_marker="m1")
        )
        b = derive_conversation_key(
            _ctx(conversation_type=conversation_type, chat_session_id="s", session_marker="m2")
        )
        assert a == b

    @pytest.mark.parametrize("conversation_type", ["sms", "email"])
    def test_sms_email_combine_chat_session_id_with_session_marker(self, conversation_type):
        # chat_session_id is upstream's stream_id (person-level) for SMS/EMAIL,
        # so session_marker disambiguates per-session.
        ctx = _ctx(conversation_type=conversation_type, chat_session_id="person-x", session_marker="m1")
        channel, key = derive_conversation_key(ctx)
        assert channel == conversation_type.upper()
        assert key == "person-x:m1"

    @pytest.mark.parametrize("conversation_type", ["sms", "email"])
    def test_sms_email_distinct_marker_yields_distinct_key(self, conversation_type):
        a = derive_conversation_key(
            _ctx(conversation_type=conversation_type, chat_session_id="p", session_marker="m1")
        )
        b = derive_conversation_key(
            _ctx(conversation_type=conversation_type, chat_session_id="p", session_marker="m2")
        )
        assert a != b
