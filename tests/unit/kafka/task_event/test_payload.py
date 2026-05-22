"""Tests for task-event payload builders.

Covers the routing logic between IN_PROGRESS / PENDING+escalation /
COMPLETED+escalation / COMPLETED-no-escalation, schema-required fields,
and stable task.id derivation.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_leasing.kafka.task_event import (
    TASK_STATUS_COMPLETED,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_PENDING,
    build_end_of_session_event,
    build_in_progress_event,
    build_pending_handoff_event,
)
from agent_leasing.models.context import HandoffResult


def _make_ctx(
    *,
    chat_session_id: str | None = "session-1",
    call_sid: str | None = "CA-abc",
    knock_resident_id: str | None = "R-1",
    knock_property_id: str | None = "P-1",
    knock_company_id: str | None = "C-1",
    conversation_type_value: str = "voice",
    persona_value: str = "RESIDENT",
    handoff_result: HandoffResult | None = None,
    handoff_message: str | None = None,
    session_marker: str = "marker-default",
) -> SimpleNamespace:
    product_info = SimpleNamespace(
        call_sid=call_sid,
        knock_resident_id=knock_resident_id,
        knock_property_id=knock_property_id,
        knock_company_id=knock_company_id,
    )
    ask_request = SimpleNamespace(
        chat_session_id=chat_session_id,
        product_info=product_info,
        conversation_type=SimpleNamespace(value=conversation_type_value),
    )
    return SimpleNamespace(
        ask_request=ask_request,
        persona=SimpleNamespace(value=persona_value),
        handoff_result=handoff_result,
        handoff_message=handoff_message,
        session_marker=session_marker,
    )


class TestInProgress:
    def test_status_is_in_progress_with_no_escalation(self):
        event = build_in_progress_event(_make_ctx())
        assert event["task"]["status"] == TASK_STATUS_IN_PROGRESS
        assert event["task"]["escalation"] is None

    def test_envelope_has_event_id_and_timestamp(self):
        event = build_in_progress_event(_make_ctx())
        assert "event_id" in event
        assert isinstance(event["event_timestamp"], int)

    def test_schema_required_fields(self):
        task = build_in_progress_event(_make_ctx())["task"]
        assert task["name"] == "Resident Conversations"
        assert task["code"] == "RESIDENT_CONVERSATION"
        assert task["source"] == "KNCK"
        assert task["owner_type"] == "AI"
        assert task["domain"] == "RESIDENT"
        assert task["publisher"] == "agent-leasing"

    def test_extra_map_includes_channel_and_session_keys(self):
        ctx = _make_ctx(conversation_type_value="sms", chat_session_id="s-42", call_sid="CA-9")
        extra = build_in_progress_event(ctx)["task"]["extra"]
        assert extra["originating_source"] == "RESIDENT_AI"
        assert extra["channel"] == "SMS"
        assert extra["session_id"] == "s-42"
        assert extra["call_sid"] == "CA-9"

    def test_references_include_company_property_resident(self):
        refs = build_in_progress_event(_make_ctx())["task"]["references"]
        types = {ref["type"] for ref in refs}
        assert types == {"COMPANY", "PROPERTY", "RESIDENT"}


class TestEndOfSessionBranching:
    def test_no_handoff_emits_completed_no_escalation(self):
        event = build_end_of_session_event(_make_ctx(handoff_result=None))
        assert event["task"]["status"] == TASK_STATUS_COMPLETED
        assert event["task"]["escalation"] is None

    def test_handoff_with_routing_confirmed_emits_completed_with_escalation(self):
        handoff = HandoffResult(
            tool="transfer_to_staff_voice",
            reason="RESIDENT_REQUESTED",
            routing_confirmed=True,
            summary="resident asked for staff",
        )
        event = build_end_of_session_event(_make_ctx(handoff_result=handoff))
        assert event["task"]["status"] == TASK_STATUS_COMPLETED
        assert event["task"]["escalation"] == {
            "reason": "RESIDENT_REQUESTED",
            "summary": "resident asked for staff",
        }

    def test_handoff_with_routing_unconfirmed_emits_pending_with_escalation(self):
        handoff = HandoffResult(
            tool="emergency_service_transfer_basic",
            reason="EMERGENCY",
            routing_confirmed=False,
            summary="twilio failed",
        )
        event = build_end_of_session_event(_make_ctx(handoff_result=handoff))
        assert event["task"]["status"] == TASK_STATUS_PENDING
        # EMERGENCY is already a valid EscalationReason symbol — pass-through.
        assert event["task"]["escalation"]["reason"] == "EMERGENCY"


class TestPendingHandoff:
    def test_status_is_pending_with_escalation(self):
        ctx = _make_ctx(handoff_message="user asked for staff")
        event = build_pending_handoff_event(ctx)
        assert event["task"]["status"] == TASK_STATUS_PENDING
        assert event["task"]["escalation"] is not None

    def test_escalation_summary_pulled_from_handoff_message(self):
        ctx = _make_ctx(handoff_message="(AI Summary) wants leasing office")
        escalation = build_pending_handoff_event(ctx)["task"]["escalation"]
        assert escalation["summary"] == "(AI Summary) wants leasing office"

    def test_escalation_reason_is_resident_requested(self):
        # Non-voice handoffs only flow through transfer_to_staff_text today
        # (resident-initiated), so reason is hardcoded to RESIDENT_REQUESTED.
        ctx = _make_ctx(handoff_message="msg")
        reason = build_pending_handoff_event(ctx)["task"]["escalation"]["reason"]
        assert reason == "RESIDENT_REQUESTED"

    def test_envelope_has_event_id_and_timestamp(self):
        event = build_pending_handoff_event(_make_ctx(handoff_message="m"))
        assert "event_id" in event
        assert isinstance(event["event_timestamp"], int)

    def test_task_id_matches_in_progress_event_for_same_session(self):
        # Non-voice handoff must correlate with the IN_PROGRESS event emitted
        # at session start — same task.id ties the lifecycle together.
        ctx_kwargs = dict(conversation_type_value="sms", chat_session_id="p", session_marker="m")
        in_progress_id = build_in_progress_event(_make_ctx(**ctx_kwargs))["task"]["id"]
        pending_id = build_pending_handoff_event(_make_ctx(handoff_message="m", **ctx_kwargs))["task"]["id"]
        assert in_progress_id == pending_id


class TestTaskIdStability:
    def test_same_chat_session_id_produces_same_task_id(self):
        ctx_a = _make_ctx(chat_session_id="stable-session")
        ctx_b = _make_ctx(chat_session_id="stable-session", call_sid="DIFFERENT")
        # task.id is derived from channel + chat_session_id; call_sid does
        # not affect the id, so two events for the same session correlate.
        id_a = build_in_progress_event(ctx_a)["task"]["id"]
        id_b = build_end_of_session_event(ctx_b)["task"]["id"]
        assert id_a == id_b

    def test_different_chat_session_ids_produce_different_task_ids(self):
        id_a = build_in_progress_event(_make_ctx(chat_session_id="one"))["task"]["id"]
        id_b = build_in_progress_event(_make_ctx(chat_session_id="two"))["task"]["id"]
        assert id_a != id_b

    def test_different_channels_with_same_session_produce_different_ids(self):
        # task_id derivation includes the channel, so a chat_session_id reused
        # across voice and chat would still get distinct ids — guards against
        # cross-channel collision.
        voice = build_in_progress_event(_make_ctx(conversation_type_value="voice"))["task"]["id"]
        chat = build_in_progress_event(_make_ctx(conversation_type_value="chat"))["task"]["id"]
        assert voice != chat

    def test_voice_chat_ignore_session_marker(self):
        # For voice/chat the session boundary is chat_session_id itself,
        # so two SessionScope instances with the same chat_session_id but
        # different session_markers must produce the same task.id.
        for channel in ("voice", "chat"):
            id_a = build_in_progress_event(
                _make_ctx(conversation_type_value=channel, chat_session_id="s", session_marker="m1")
            )["task"]["id"]
            id_b = build_in_progress_event(
                _make_ctx(conversation_type_value=channel, chat_session_id="s", session_marker="m2")
            )["task"]["id"]
            assert id_a == id_b, f"{channel}: session_marker should be ignored"

    def test_sms_email_split_task_ids_when_session_marker_changes(self):
        # SMS/EMAIL chat_session_id is upstream's stream_id (person-level),
        # so the same chat_session_id with different session_markers must
        # produce *different* task.ids — modeling the case where Redis cache
        # expired between sessions for the same person.
        for channel in ("sms", "email"):
            id_a = build_in_progress_event(
                _make_ctx(conversation_type_value=channel, chat_session_id="person-x", session_marker="m1")
            )["task"]["id"]
            id_b = build_in_progress_event(
                _make_ctx(conversation_type_value=channel, chat_session_id="person-x", session_marker="m2")
            )["task"]["id"]
            assert id_a != id_b, f"{channel}: distinct session_marker must yield distinct task.id"

    def test_sms_email_same_marker_yields_same_task_id(self):
        # Within a single SMS/EMAIL session (same session_marker), task.id
        # must remain stable so all events correlate.
        for channel in ("sms", "email"):
            id_a = build_in_progress_event(
                _make_ctx(conversation_type_value=channel, chat_session_id="p", session_marker="m")
            )["task"]["id"]
            id_b = build_end_of_session_event(
                _make_ctx(conversation_type_value=channel, chat_session_id="p", session_marker="m")
            )["task"]["id"]
            assert id_a == id_b
