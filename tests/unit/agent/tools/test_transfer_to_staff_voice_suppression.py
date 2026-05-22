"""KNCK-39515: transfer_to_staff_voice sets handoff_in_progress for interrupt suppression.

Mirrors the ESR pattern so a caller talking over the "transferring you now" message cannot
cancel the handoff mid-playback.
"""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_leasing.settings import settings

tts = importlib.import_module("agent_leasing.agent.tools.transfer_to_staff.transfer_to_staff_voice")


def _build_ctx() -> SimpleNamespace:
    """Minimal ctx with the attributes the impl reads/writes."""
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


@pytest.fixture(autouse=True)
def _enable_suppression():
    original = settings.interrupt_suppression_enabled
    settings.interrupt_suppression_enabled = True
    yield
    settings.interrupt_suppression_enabled = original


@pytest.fixture
def patched_transfer(monkeypatch):
    """Patch outbound network + state calls. Returns a call_state with wait_for_message_playback."""
    observed = {"flag_during_playback": None}

    call_state = SimpleNamespace()

    async def wait_for_message_playback(_message_type, **_kwargs):
        # Record what the flag looked like while the transition message was "playing".
        observed["flag_during_playback"] = ctx_holder["ctx"].context.handoff_in_progress
        return SimpleNamespace(completed=True)

    call_state.wait_for_message_playback = wait_for_message_playback

    ctx_holder: dict = {"ctx": None}
    monkeypatch.setattr(tts, "get_call_state_from_context", lambda _ctx: call_state)

    api_call_mock = AsyncMock(return_value=None)
    twilio_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(tts, "_make_transfer_to_staff_api_call", api_call_mock)
    monkeypatch.setattr(tts, "_transfer_twilio_call", twilio_mock)

    return observed, ctx_holder


@pytest.mark.asyncio
async def test_handoff_flag_set_before_playback_and_cleared_on_success(patched_transfer):
    observed, ctx_holder = patched_transfer
    ctx = _build_ctx()
    ctx_holder["ctx"] = ctx

    result = await tts._transfer_to_staff_voice_impl(ctx, summary="Resident needs help")

    assert result == "Call transferred successfully."
    assert observed["flag_during_playback"] is True, (
        "handoff_in_progress must be True while the transition message is playing"
    )
    assert ctx.context.handoff_in_progress is False, "handoff_in_progress must be cleared after a successful transfer"


@pytest.mark.asyncio
async def test_handoff_flag_cleared_on_cancelled_error(patched_transfer, monkeypatch):
    _, ctx_holder = patched_transfer
    ctx = _build_ctx()
    ctx_holder["ctx"] = ctx

    async def raising_api_call(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(tts, "_make_transfer_to_staff_api_call", raising_api_call)

    result = await tts._transfer_to_staff_voice_impl(ctx, summary="summary")

    assert result == "Transfer cancelled: call ended by user."
    assert ctx.context.handoff_in_progress is False


@pytest.mark.asyncio
async def test_handoff_flag_cleared_on_unexpected_exception(patched_transfer, monkeypatch):
    _, ctx_holder = patched_transfer
    ctx = _build_ctx()
    ctx_holder["ctx"] = ctx

    async def raising_api_call(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(tts, "_make_transfer_to_staff_api_call", raising_api_call)

    with pytest.raises(RuntimeError, match="boom"):
        await tts._transfer_to_staff_voice_impl(ctx, summary="summary")

    assert ctx.context.handoff_in_progress is False, (
        "handoff_in_progress must not remain stuck True if the transfer crashes"
    )


@pytest.mark.asyncio
async def test_prior_handoff_state_restored_on_cancelled_error(patched_transfer, monkeypatch):
    """Edge case: ESR may have already set handoff_in_progress=True before transfer_to_staff
    is invoked. On failure we must restore the prior value instead of stripping ESR's suppression.
    """
    _, ctx_holder = patched_transfer
    ctx = _build_ctx()
    ctx.context.handoff_in_progress = True  # ESR-active entry state
    ctx_holder["ctx"] = ctx

    async def raising_api_call(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(tts, "_make_transfer_to_staff_api_call", raising_api_call)

    await tts._transfer_to_staff_voice_impl(ctx, summary="summary")

    assert ctx.context.handoff_in_progress is True, (
        "prior ESR-set suppression must not be cleared by a transfer_to_staff cancellation"
    )


@pytest.mark.asyncio
async def test_prior_handoff_state_restored_on_unexpected_exception(patched_transfer, monkeypatch):
    """Same restore behavior when transfer_to_staff crashes — don't drop ESR suppression."""
    _, ctx_holder = patched_transfer
    ctx = _build_ctx()
    ctx.context.handoff_in_progress = True
    ctx_holder["ctx"] = ctx

    async def raising_api_call(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(tts, "_make_transfer_to_staff_api_call", raising_api_call)

    with pytest.raises(RuntimeError, match="boom"):
        await tts._transfer_to_staff_voice_impl(ctx, summary="summary")

    assert ctx.context.handoff_in_progress is True


@pytest.mark.asyncio
async def test_handoff_flag_not_set_when_asking_for_summary(patched_transfer):
    """The first turn returns the 'ask for summary' prompt without playing anything —
    the flag must NOT be set because no handoff message is being spoken yet.
    """
    _, ctx_holder = patched_transfer
    ctx = _build_ctx()
    ctx_holder["ctx"] = ctx

    result = await tts._transfer_to_staff_voice_impl(ctx, summary=None)

    assert "[Action Required]" in result
    assert ctx.context.handoff_in_progress is False


@pytest.mark.asyncio
async def test_handoff_flag_not_set_when_setting_disabled(patched_transfer):
    """Kill switch: if interrupt_suppression_enabled is False, don't flip the flag."""
    observed, ctx_holder = patched_transfer
    settings.interrupt_suppression_enabled = False

    ctx = _build_ctx()
    ctx_holder["ctx"] = ctx

    await tts._transfer_to_staff_voice_impl(ctx, summary="summary")

    assert observed["flag_during_playback"] is False
    assert ctx.context.handoff_in_progress is False


@pytest.mark.asyncio
async def test_office_closed_first_staff_request_forces_warning(monkeypatch):
    """When office is closed, first transfer request must return the closed-hours warning."""
    ctx = _build_ctx()

    monkeypatch.setattr(tts, "is_office_currently_open", lambda *args, **kwargs: False)

    result = await tts._transfer_to_staff_voice_impl(ctx, summary=None)

    assert "[Action Required]" in result
    assert "office is currently closed" in result.lower()
    assert "leave a voicemail" in result.lower()
    assert getattr(ctx.context, "office_closed_warning_given", False) is True


@pytest.mark.asyncio
async def test_office_closed_warning_is_not_repeated_on_second_attempt(monkeypatch, patched_transfer):
    """After first warning, next transfer attempt should proceed through normal transfer path."""
    _, ctx_holder = patched_transfer
    ctx = _build_ctx()
    ctx_holder["ctx"] = ctx

    monkeypatch.setattr(tts, "is_office_currently_open", lambda *args, **kwargs: False)

    first = await tts._transfer_to_staff_voice_impl(ctx, summary=None)
    second = await tts._transfer_to_staff_voice_impl(ctx, summary="billing issue")

    assert "[Action Required]" in first
    assert second == "Call transferred successfully."


@pytest.mark.asyncio
async def test_office_closed_second_attempt_without_summary_transfers_immediately(monkeypatch, patched_transfer):
    """If caller confirms transfer after warning without a summary, do not ask summary again."""
    _, ctx_holder = patched_transfer
    ctx = _build_ctx()
    ctx_holder["ctx"] = ctx

    monkeypatch.setattr(tts, "is_office_currently_open", lambda *args, **kwargs: False)

    first = await tts._transfer_to_staff_voice_impl(ctx, summary=None)
    second = await tts._transfer_to_staff_voice_impl(ctx, summary=None)

    assert "[Action Required]" in first
    assert second == "Call transferred successfully."


@pytest.mark.asyncio
async def test_office_closed_frustrated_user_bypasses_warning(monkeypatch, patched_transfer):
    """Frustration signal should force immediate transfer without closed-hours warning."""
    _, ctx_holder = patched_transfer
    ctx = _build_ctx()
    ctx.context.frustrated_user_emitted = True
    ctx_holder["ctx"] = ctx

    monkeypatch.setattr(tts, "is_office_currently_open", lambda *args, **kwargs: False)

    result = await tts._transfer_to_staff_voice_impl(ctx, summary=None)

    assert result == "Call transferred successfully."
    assert getattr(ctx.context, "office_closed_warning_given", False) is False


@pytest.mark.asyncio
async def test_office_closed_callback_summary_bypasses_warning(monkeypatch, patched_transfer):
    """Callback summary should transfer immediately without closed-hours warning."""
    _, ctx_holder = patched_transfer
    ctx = _build_ctx()
    ctx_holder["ctx"] = ctx

    monkeypatch.setattr(tts, "is_office_currently_open", lambda *args, **kwargs: False)

    result = await tts._transfer_to_staff_voice_impl(ctx, summary="returning a call from the property")

    assert result == "Call transferred successfully."
    assert getattr(ctx.context, "office_closed_warning_given", False) is False
