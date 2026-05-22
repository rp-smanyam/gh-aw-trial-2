"""KNCK-39556 PR 3: transfer_to_staff_* fires a Handoff TaskActivityEvent
on confirmed-success only.

The wire-in lives in two tools (voice + text) with several non-handoff
return paths: refused-summary, missing-confirmation, ask-for-summary
(voice), concurrent-guard (voice), CancelledError (voice). None of these
should emit. Only a real transfer should.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agents import RunContextWrapper
from agents.tool_context import ToolContext
from openai.types.responses import ResponseFunctionToolCall

from agent_leasing.models.context import SessionScope
from agent_leasing.settings import settings

tts = importlib.import_module("agent_leasing.agent.tools.transfer_to_staff.transfer_to_staff_voice")
ttt = importlib.import_module("agent_leasing.agent.tools.transfer_to_staff.transfer_to_staff_text")


# ---------------------------------------------------------------------------
# Voice
# ---------------------------------------------------------------------------


def _voice_ctx() -> SimpleNamespace:
    """Minimal voice ctx with the attributes the impl reads/writes."""
    context = SimpleNamespace(
        transfer_summary_requested=False,
        handoff_in_progress=False,
        call_ended_by_agent=False,
        call_management_in_progress=False,
        ask_request=SimpleNamespace(
            product_info=SimpleNamespace(
                call_sid="CA-handoff",
                knock_resident_id="R1",
                resident_manager_id="MGR-1",
            )
        ),
    )
    return SimpleNamespace(context=context)


@pytest.fixture
def patched_voice_transfer(monkeypatch):
    call_state = SimpleNamespace()

    async def wait_for_message_playback(_message_type, **_kwargs):
        return SimpleNamespace(completed=True)

    call_state.wait_for_message_playback = wait_for_message_playback
    monkeypatch.setattr(tts, "get_call_state_from_context", lambda _ctx: call_state)
    monkeypatch.setattr(tts, "_make_transfer_to_staff_api_call", AsyncMock(return_value=None))
    monkeypatch.setattr(tts, "_transfer_twilio_call", AsyncMock(return_value=None))


@pytest.fixture
def emit_spy(monkeypatch):
    """Patch the generic `publish_task_activity` bridge in both tools.

    Captures the (extractor, transfer_message, context, handoff_portal_link)
    bundle so tests can assert "fired exactly once with the expected
    extractor + payload". The handoff extractor is the only one either
    tool wires in — assert on identity rather than name.
    """
    calls: list[dict] = []

    def fake_publish(extractor, tool_output, context, **kwargs):
        calls.append(
            {
                "extractor": extractor,
                "transfer_message": tool_output,
                "context": context,
                "handoff_portal_link": kwargs.get("handoff_portal_link"),
                "reason": kwargs.get("reason"),
                "topic": kwargs.get("topic"),
            }
        )

    monkeypatch.setattr(tts, "publish_task_activity", fake_publish)
    monkeypatch.setattr(ttt, "publish_task_activity", fake_publish)
    return calls


@pytest.fixture
def handoff_extractor():
    """Identity reference for the spy assertions — keeps tests honest
    that we wired in the handoff extractor specifically (not, say, the
    SR-created one)."""
    from agent_leasing.kafka.task_activity.extractors import extract_handoff_events

    return extract_handoff_events


@pytest.mark.asyncio
async def test_voice_emit_on_successful_transfer(patched_voice_transfer, emit_spy, handoff_extractor):
    from agent_leasing.api.model import HandoffReasonCode

    ctx = _voice_ctx()
    result = await tts._transfer_to_staff_voice_impl(
        ctx, summary="Resident needs help with rent", reason=HandoffReasonCode.SYSTEM_ERROR
    )

    assert result == "Call transferred successfully."
    assert len(emit_spy) == 1
    # Voice carries the staff-facing transfer_message (summary or fallback),
    # not the raw `summary` arg — the activity stream should match what staff saw.
    assert emit_spy[0]["extractor"] is handoff_extractor
    assert emit_spy[0]["transfer_message"] == "Resident needs help with rent"
    assert emit_spy[0]["handoff_portal_link"] is None  # voice has no portal link
    assert emit_spy[0]["reason"] == HandoffReasonCode.SYSTEM_ERROR
    # ctx.handoff_result is populated for the session-end task-event payload.
    hr = ctx.context.handoff_result
    assert hr is not None
    assert hr.tool == "transfer_to_staff_voice"
    assert hr.reason == "SYSTEM_ERROR"
    assert hr.routing_confirmed is True
    assert hr.summary == "Resident needs help with rent"


@pytest.mark.asyncio
async def test_voice_emit_uses_fallback_message_when_summary_refused(patched_voice_transfer, emit_spy):
    ctx = _voice_ctx()
    ctx.context.transfer_summary_requested = True  # already asked once
    result = await tts._transfer_to_staff_voice_impl(ctx, summary=None)

    assert result == "Call transferred successfully."
    assert len(emit_spy) == 1
    assert emit_spy[0]["transfer_message"] == "Resident requested transfer to staff and refused to provide a reason"


@pytest.mark.asyncio
async def test_voice_no_emit_when_asking_for_summary(patched_voice_transfer, emit_spy):
    ctx = _voice_ctx()
    result = await tts._transfer_to_staff_voice_impl(ctx, summary=None)

    assert "[Action Required]" in result
    assert emit_spy == []


@pytest.mark.asyncio
async def test_voice_no_emit_when_concurrent_guard_fires(patched_voice_transfer, emit_spy):
    original_guard = settings.call_management_concurrency_guard_enabled
    settings.call_management_concurrency_guard_enabled = True
    try:
        ctx = _voice_ctx()
        ctx.context.call_management_in_progress = True

        result = await tts._transfer_to_staff_voice_impl(ctx, summary="Anything")

        assert result == tts.CALL_MANAGEMENT_GUARD_MESSAGE
        assert emit_spy == []
    finally:
        settings.call_management_concurrency_guard_enabled = original_guard


@pytest.mark.asyncio
async def test_voice_no_emit_on_cancelled_error(patched_voice_transfer, emit_spy, monkeypatch):
    async def raising_api_call(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(tts, "_make_transfer_to_staff_api_call", raising_api_call)

    ctx = _voice_ctx()
    result = await tts._transfer_to_staff_voice_impl(ctx, summary="Resident needs help")

    assert result == "Transfer cancelled: call ended by user."
    assert emit_spy == []


@pytest.mark.asyncio
async def test_voice_no_emit_on_unexpected_exception(patched_voice_transfer, emit_spy, monkeypatch):
    async def raising_api_call(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(tts, "_make_transfer_to_staff_api_call", raising_api_call)

    ctx = _voice_ctx()
    with pytest.raises(RuntimeError, match="boom"):
        await tts._transfer_to_staff_voice_impl(ctx, summary="Resident needs help")

    assert emit_spy == []


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def _build_text_tool_ctx(ask_request):
    context = SessionScope(ask_request=ask_request, thread_id="test-thread")
    tool_call = ResponseFunctionToolCall(
        arguments="{}",
        call_id="test-call-id",
        name="transfer_to_staff_text",
        type="function_call",
    )
    return ToolContext.from_agent_context(
        RunContextWrapper(context=context),
        tool_call_id="test-call-id",
        tool_call=tool_call,
    )


def _text_tool_args(
    *,
    repeated=False,
    sufficient=True,
    refused=False,
    user_confirmation=True,
    transfer_message="Resident wants help with rent",
    reason=None,
    handoff_topic=None,
):
    args = {
        "repeated_handoff_attempt": repeated,
        "sufficient_summary_information": sufficient,
        "user_refused_to_provide_summary": refused,
        "transfer_message": transfer_message,
        "user_confirmation": user_confirmation,
    }
    if reason is not None:
        args["reason"] = reason
    if handoff_topic is not None:
        args["handoff_topic"] = handoff_topic
    return json.dumps(args)


@pytest.fixture
def fake_redis_put(monkeypatch):
    async def mock_put(*_args, **_kwargs):
        return None

    monkeypatch.setattr(ttt, "put", mock_put)


@pytest.mark.asyncio
async def test_text_emit_on_chat_success_with_portal_link(
    ask_request_resident_chat_ll, emit_spy, fake_redis_put, handoff_extractor
):
    tool_ctx = _build_text_tool_ctx(ask_request_resident_chat_ll)
    result = await ttt.transfer_to_staff_text.on_invoke_tool(
        tool_ctx, _text_tool_args(transfer_message="Need rent help")
    )

    assert "Successfully set context variable" in result
    assert len(emit_spy) == 1
    assert emit_spy[0]["extractor"] is handoff_extractor
    assert emit_spy[0]["transfer_message"] == "Need rent help"
    # Chat fixture has portal config — link must populate.
    assert emit_spy[0]["handoff_portal_link"] is not None


@pytest.mark.asyncio
async def test_text_emit_on_email_success_with_portal_link(ask_request_resident_email_ll, emit_spy, fake_redis_put):
    tool_ctx = _build_text_tool_ctx(ask_request_resident_email_ll)
    result = await ttt.transfer_to_staff_text.on_invoke_tool(
        tool_ctx, _text_tool_args(transfer_message="Email summary")
    )

    assert "Successfully set context variable" in result
    assert len(emit_spy) == 1
    assert emit_spy[0]["transfer_message"] == "Email summary"


@pytest.mark.asyncio
async def test_text_emit_when_portal_base_url_missing(ask_request_resident_chat_ll, emit_spy, fake_redis_put):
    # Portal URL missing means no link is sent to the user — but the handoff
    # context is still set, so the activity event must still fire.
    # Build the context first (passes the SessionScope validator), then null
    # the field to simulate the runtime no-link branch.
    tool_ctx = _build_text_tool_ctx(ask_request_resident_chat_ll)
    tool_ctx.context.ask_request.product_info.uc_portal_base_url = None

    await ttt.transfer_to_staff_text.on_invoke_tool(tool_ctx, _text_tool_args(transfer_message="No-link case"))

    assert len(emit_spy) == 1
    assert emit_spy[0]["transfer_message"] == "No-link case"
    assert emit_spy[0]["handoff_portal_link"] is None


@pytest.mark.asyncio
async def test_text_no_emit_when_summary_insufficient(ask_request_resident_chat_ll, emit_spy, fake_redis_put):
    tool_ctx = _build_text_tool_ctx(ask_request_resident_chat_ll)
    result = await ttt.transfer_to_staff_text.on_invoke_tool(
        tool_ctx,
        _text_tool_args(sufficient=False, refused=False),
    )

    assert "did not provide sufficient summary information" in result
    assert emit_spy == []


@pytest.mark.asyncio
async def test_text_no_emit_when_user_did_not_confirm(ask_request_resident_chat_ll, emit_spy, fake_redis_put):
    tool_ctx = _build_text_tool_ctx(ask_request_resident_chat_ll)
    result = await ttt.transfer_to_staff_text.on_invoke_tool(
        tool_ctx,
        _text_tool_args(user_confirmation=False),
    )

    assert "did not confirm the action" in result
    assert emit_spy == []


@pytest.mark.asyncio
async def test_text_emit_on_repeated_handoff_attempt_bypasses_validation(
    ask_request_resident_chat_ll, emit_spy, fake_redis_put
):
    # Repeated handoff bypasses the summary/confirmation guards — the activity
    # event must still fire.
    tool_ctx = _build_text_tool_ctx(ask_request_resident_chat_ll)
    await ttt.transfer_to_staff_text.on_invoke_tool(
        tool_ctx,
        _text_tool_args(repeated=True, sufficient=False, user_confirmation=False, transfer_message="Repeated"),
    )

    assert len(emit_spy) == 1
    assert emit_spy[0]["transfer_message"] == "Repeated"


@pytest.mark.asyncio
async def test_text_emit_carries_explicit_reason(ask_request_resident_chat_ll, emit_spy, fake_redis_put):
    from agent_leasing.api.model import HandoffReasonCode

    tool_ctx = _build_text_tool_ctx(ask_request_resident_chat_ll)
    await ttt.transfer_to_staff_text.on_invoke_tool(
        tool_ctx,
        _text_tool_args(transfer_message="System failed mid-call", reason="SYSTEM_ERROR"),
    )

    assert len(emit_spy) == 1
    assert emit_spy[0]["reason"] == HandoffReasonCode.SYSTEM_ERROR


@pytest.mark.asyncio
async def test_text_emit_defaults_reason_when_unspecified(ask_request_resident_chat_ll, emit_spy, fake_redis_put):
    from agent_leasing.api.model import HandoffReasonCode

    tool_ctx = _build_text_tool_ctx(ask_request_resident_chat_ll)
    await ttt.transfer_to_staff_text.on_invoke_tool(tool_ctx, _text_tool_args())

    assert len(emit_spy) == 1
    assert emit_spy[0]["reason"] == HandoffReasonCode.RESIDENT_REQUESTED


@pytest.mark.asyncio
async def test_text_emit_carries_explicit_handoff_topic(ask_request_resident_chat_ll, emit_spy, fake_redis_put):
    from agent_leasing.api.model import HandoffTopic

    tool_ctx = _build_text_tool_ctx(ask_request_resident_chat_ll)
    await ttt.transfer_to_staff_text.on_invoke_tool(
        tool_ctx,
        _text_tool_args(
            transfer_message="Late fee waiver request",
            reason="COMPLAINT",
            handoff_topic="BALANCE_RESOLUTION",
        ),
    )

    assert len(emit_spy) == 1
    assert emit_spy[0]["topic"] == HandoffTopic.BALANCE_RESOLUTION


@pytest.mark.asyncio
async def test_text_emit_defaults_handoff_topic_to_none(ask_request_resident_chat_ll, emit_spy, fake_redis_put):
    tool_ctx = _build_text_tool_ctx(ask_request_resident_chat_ll)
    await ttt.transfer_to_staff_text.on_invoke_tool(tool_ctx, _text_tool_args())

    assert len(emit_spy) == 1
    assert emit_spy[0]["topic"] is None


@pytest.mark.asyncio
async def test_voice_emit_carries_explicit_handoff_topic(patched_voice_transfer, emit_spy):
    from agent_leasing.api.model import HandoffReasonCode, HandoffTopic

    ctx = _voice_ctx()
    result = await tts._transfer_to_staff_voice_impl(
        ctx,
        summary="Late fee waiver request",
        reason=HandoffReasonCode.COMPLAINT,
        handoff_topic=HandoffTopic.BALANCE_RESOLUTION,
    )

    assert result == "Call transferred successfully."
    assert len(emit_spy) == 1
    assert emit_spy[0]["topic"] == HandoffTopic.BALANCE_RESOLUTION


@pytest.mark.asyncio
async def test_voice_emit_defaults_handoff_topic_to_none(patched_voice_transfer, emit_spy):
    ctx = _voice_ctx()
    await tts._transfer_to_staff_voice_impl(ctx, summary="Generic transfer")

    assert len(emit_spy) == 1
    assert emit_spy[0]["topic"] is None


@pytest.mark.asyncio
async def test_voice_records_failed_handoff_when_twilio_raises(monkeypatch, emit_spy):
    from agent_leasing.api.model import HandoffReasonCode

    call_state = SimpleNamespace()

    async def wait_for_message_playback(_message_type, **_kwargs):
        return SimpleNamespace(completed=True)

    call_state.wait_for_message_playback = wait_for_message_playback
    monkeypatch.setattr(tts, "get_call_state_from_context", lambda _ctx: call_state)
    monkeypatch.setattr(tts, "_make_transfer_to_staff_api_call", AsyncMock(return_value=None))
    monkeypatch.setattr(tts, "_transfer_twilio_call", AsyncMock(side_effect=RuntimeError("twilio boom")))

    ctx = _voice_ctx()
    with pytest.raises(RuntimeError, match="twilio boom"):
        await tts._transfer_to_staff_voice_impl(
            ctx, summary="Resident has urgent maintenance", reason=HandoffReasonCode.EMERGENCY
        )

    assert len(emit_spy) == 0  # activity event only fires on confirmed-success
    hr = ctx.context.handoff_result
    assert hr is not None
    assert hr.tool == "transfer_to_staff_voice"
    assert hr.reason == "EMERGENCY"
    assert hr.routing_confirmed is False
    assert hr.summary == "Resident has urgent maintenance"
