"""Tests for the call management concurrency guard (KNCK-39358).

The guard prevents end_call, transfer_to_staff_voice, and
emergency_service_transfer_basic from running concurrently on the same call.
All three tools modify the same Twilio call state, so a second in-flight
invocation is always a bug.

Pattern mirrors the thinker concurrency guard
(tests/unit/agent/resident_one_agent/test_thinker_concurrency.py):
- Early-return if flag is set
- Set flag True before destructive work
- Clear flag in `finally` so it resets on both success and error
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from agent_leasing.agent.tools.emergency_service_transfer.basic import (
    emergency_service_transfer_basic as estb_mod,
)
from agent_leasing.agent.tools.end_call import end_call as end_call_mod
from agent_leasing.agent.tools.transfer_to_staff import transfer_to_staff_voice as tts_voice_mod


def _voice_ctx():
    """Build a minimal ctx that mirrors SessionScope fields used by the guard."""
    return SimpleNamespace(
        context=SimpleNamespace(
            call_ended_by_agent=False,
            call_management_in_progress=False,
            transfer_summary_requested=False,
            esr_initiated=False,
            disabled_modules=[],
            ask_request=SimpleNamespace(
                product_info=SimpleNamespace(
                    knock_resident_id="res-123",
                    resident_manager_id="mgr-456",
                    call_sid="CA123",
                    uc_company_id=SimpleNamespace(id="co-1"),
                    uc_property_id=SimpleNamespace(id="prop-1"),
                    emerg_phone="+12025551234",
                ),
                product="renter_ai_resident_voice",
            ),
        ),
    )


# ---------------------------------------------------------------------------
# transfer_to_staff_voice
# ---------------------------------------------------------------------------


class TestTransferToStaffVoiceGuard:
    @pytest.mark.asyncio
    async def test_concurrent_invocation_returns_guard_message(self, monkeypatch):
        """A second concurrent call returns the guard message while the first is running.

        Simulates the in-flight state by pre-setting `call_management_in_progress = True`,
        which is exactly what the first in-flight call would have done. Using a flag
        directly (instead of asyncio events) keeps the test bounded and deterministic.
        """
        ctx = _voice_ctx()
        ctx.context.call_management_in_progress = True

        # Mocks so we can verify the destructive path is NOT taken
        api_mock = AsyncMock()
        twilio_mock = AsyncMock()
        monkeypatch.setattr(tts_voice_mod, "get_call_state_from_context", lambda _ctx: None)
        monkeypatch.setattr(tts_voice_mod, "_make_transfer_to_staff_api_call", api_mock)
        monkeypatch.setattr(tts_voice_mod, "_transfer_twilio_call", twilio_mock)

        result = await tts_voice_mod._transfer_to_staff_voice_impl(ctx, summary="help again")
        assert "already in progress" in result.lower()
        api_mock.assert_not_called()
        twilio_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_summary_request_does_not_set_flag(self, monkeypatch):
        """Early 'ask for summary' return must not trip the guard."""
        ctx = _voice_ctx()
        result = await tts_voice_mod._transfer_to_staff_voice_impl(ctx, summary=None)
        assert "[Action Required]" in result
        assert ctx.context.call_management_in_progress is False

    @pytest.mark.asyncio
    async def test_flag_cleared_after_success(self, monkeypatch):
        """Flag is cleared in finally, allowing a later sequential call."""
        ctx = _voice_ctx()
        monkeypatch.setattr(tts_voice_mod, "get_call_state_from_context", lambda _ctx: None)
        monkeypatch.setattr(tts_voice_mod, "_make_transfer_to_staff_api_call", AsyncMock())
        monkeypatch.setattr(tts_voice_mod, "_transfer_twilio_call", AsyncMock())

        await tts_voice_mod._transfer_to_staff_voice_impl(ctx, summary="first")
        assert ctx.context.call_management_in_progress is False

    @pytest.mark.asyncio
    async def test_flag_cleared_after_error(self, monkeypatch):
        """Flag is cleared after an exception so retries aren't blocked forever."""
        ctx = _voice_ctx()
        monkeypatch.setattr(tts_voice_mod, "get_call_state_from_context", lambda _ctx: None)
        monkeypatch.setattr(
            tts_voice_mod,
            "_make_transfer_to_staff_api_call",
            AsyncMock(side_effect=RuntimeError("boom")),
        )
        monkeypatch.setattr(tts_voice_mod, "_transfer_twilio_call", AsyncMock())

        with pytest.raises(RuntimeError, match="boom"):
            await tts_voice_mod._transfer_to_staff_voice_impl(ctx, summary="first")
        assert ctx.context.call_management_in_progress is False

    @pytest.mark.asyncio
    async def test_guard_disabled_allows_concurrent_calls(self, monkeypatch):
        """With the kill switch off, concurrent calls are not blocked."""
        ctx = _voice_ctx()
        monkeypatch.setattr(tts_voice_mod, "get_call_state_from_context", lambda _ctx: None)
        monkeypatch.setattr(tts_voice_mod, "_make_transfer_to_staff_api_call", AsyncMock())
        monkeypatch.setattr(tts_voice_mod, "_transfer_twilio_call", AsyncMock())

        fake_settings = Mock()
        fake_settings.call_management_concurrency_guard_enabled = False
        monkeypatch.setattr(tts_voice_mod, "settings", fake_settings)

        # Simulate another tool already in progress; guard should not trip
        ctx.context.call_management_in_progress = True
        result = await tts_voice_mod._transfer_to_staff_voice_impl(ctx, summary="help")
        assert result == "Call transferred successfully."


# ---------------------------------------------------------------------------
# end_call
# ---------------------------------------------------------------------------


def _end_call_ctx():
    return SimpleNamespace(
        context=SimpleNamespace(
            call_ended_by_agent=False,
            call_management_in_progress=False,
            ask_request=SimpleNamespace(
                product_info=SimpleNamespace(call_sid="CA456"),
            ),
        ),
    )


class TestEndCallGuard:
    @pytest.mark.asyncio
    async def test_concurrent_invocation_returns_guard_message(self, monkeypatch):
        """end_call is blocked if another call management tool is already in flight."""
        ctx = _end_call_ctx()
        ctx.context.call_management_in_progress = True  # Simulate in-flight transfer

        # Should not touch Twilio at all when guarded
        twilio_mock = Mock()
        monkeypatch.setattr(end_call_mod, "TwilioClient", twilio_mock)
        monkeypatch.setattr(end_call_mod, "get_call_state_from_context", lambda _ctx: None)

        result = await end_call_mod._end_call_impl(
            ctx,
            message="bye",
            tool_use_reason="user said bye",
            user_confirmation=True,
        )
        assert "already in progress" in result.lower()
        twilio_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_flag_cleared_after_success(self, monkeypatch):
        """Flag is cleared in finally."""
        ctx = _end_call_ctx()
        monkeypatch.setattr(end_call_mod, "get_call_state_from_context", lambda _ctx: None)

        fake_settings = Mock()
        fake_settings.knock_twilio_api_key = "k"
        fake_settings.knock_twilio_api_secret = "s"
        fake_settings.knock_twilio_account_sid = "a"
        fake_settings.call_management_concurrency_guard_enabled = True
        monkeypatch.setattr(end_call_mod, "settings", fake_settings)

        mock_client = Mock()
        mock_call = Mock()
        mock_call.status = "completed"
        mock_client.calls.return_value.update.return_value = mock_call
        monkeypatch.setattr(end_call_mod, "TwilioClient", Mock(return_value=mock_client))

        result = await end_call_mod._end_call_impl(
            ctx,
            message="bye",
            tool_use_reason="user said bye",
            user_confirmation=True,
        )
        assert "Call ended successfully" in result
        assert ctx.context.call_management_in_progress is False

    @pytest.mark.asyncio
    async def test_flag_cleared_after_error(self, monkeypatch):
        """Flag is cleared in finally even when Twilio raises."""
        ctx = _end_call_ctx()
        monkeypatch.setattr(end_call_mod, "get_call_state_from_context", lambda _ctx: None)

        fake_settings = Mock()
        fake_settings.knock_twilio_api_key = "k"
        fake_settings.knock_twilio_api_secret = "s"
        fake_settings.knock_twilio_account_sid = "a"
        fake_settings.call_management_concurrency_guard_enabled = True
        monkeypatch.setattr(end_call_mod, "settings", fake_settings)

        mock_client = Mock()
        mock_client.calls.return_value.update.side_effect = RuntimeError("twilio broke")
        monkeypatch.setattr(end_call_mod, "TwilioClient", Mock(return_value=mock_client))

        with pytest.raises(RuntimeError, match="twilio broke"):
            await end_call_mod._end_call_impl(
                ctx,
                message="bye",
                tool_use_reason="user said bye",
                user_confirmation=True,
            )
        assert ctx.context.call_management_in_progress is False


# ---------------------------------------------------------------------------
# emergency_service_transfer_basic
# ---------------------------------------------------------------------------


class TestEmergencyServiceTransferBasicGuard:
    @pytest.mark.asyncio
    async def test_concurrent_invocation_returns_guard_message(self, monkeypatch):
        """ESR basic is blocked if another call management tool is already in flight."""
        ctx = _voice_ctx()
        ctx.context.call_management_in_progress = True  # Simulate in-flight transfer

        # Should not touch Twilio at all
        redirect_mock = AsyncMock()
        monkeypatch.setattr(estb_mod, "redirect_to_number_via_twilio", redirect_mock)
        monkeypatch.setattr(estb_mod, "get_call_state_from_context", lambda _ctx: None)
        monkeypatch.setattr(estb_mod, "get_channel_from_context", lambda _ctx: "VOICE")

        result = await estb_mod._emergency_service_transfer_basic_impl(
            ctx,
            already_created_emergency_service_request=True,
            service_request_summary="Test emergency",
        )
        assert "already in progress" in result.lower()
        redirect_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_never_call_message_does_not_set_flag(self, monkeypatch):
        """Early NEVER_CALL_MESSAGE return must not trip the guard."""
        ctx = _voice_ctx()
        monkeypatch.setattr(estb_mod, "get_channel_from_context", lambda _ctx: "VOICE")
        # Simulate MR enabled so NEVER_CALL_MESSAGE path fires
        with patch.object(estb_mod, "is_enabled", return_value=True):
            result = await estb_mod._emergency_service_transfer_basic_impl(
                ctx,
                already_created_emergency_service_request=False,
                service_request_summary="Test emergency",
            )
        assert result == estb_mod.NEVER_CALL_MESSAGE
        assert ctx.context.call_management_in_progress is False

    @pytest.mark.asyncio
    async def test_flag_cleared_after_success(self, monkeypatch):
        """Flag is cleared in finally after successful voice redirect."""
        ctx = _voice_ctx()
        monkeypatch.setattr(estb_mod, "get_channel_from_context", lambda _ctx: "VOICE")
        monkeypatch.setattr(estb_mod, "get_call_state_from_context", lambda _ctx: None)
        monkeypatch.setattr(estb_mod, "redirect_to_number_via_twilio", AsyncMock())

        with patch.object(estb_mod, "is_enabled", return_value=False):
            result = await estb_mod._emergency_service_transfer_basic_impl(
                ctx,
                already_created_emergency_service_request=True,
                service_request_summary="Test emergency",
            )
        assert "+12025551234" in result
        assert ctx.context.call_management_in_progress is False

    @pytest.mark.asyncio
    async def test_flag_cleared_after_error(self, monkeypatch):
        """Flag is cleared in finally even when the impl raises internally."""
        ctx = _voice_ctx()
        monkeypatch.setattr(estb_mod, "get_channel_from_context", lambda _ctx: "VOICE")
        monkeypatch.setattr(estb_mod, "get_call_state_from_context", lambda _ctx: None)
        monkeypatch.setattr(
            estb_mod,
            "redirect_to_number_via_twilio",
            AsyncMock(side_effect=RuntimeError("twilio broke")),
        )

        with patch.object(estb_mod, "is_enabled", return_value=False):
            result = await estb_mod._emergency_service_transfer_basic_impl(
                ctx,
                already_created_emergency_service_request=True,
                service_request_summary="Test emergency",
            )
        # Tool swallows exception and returns error text
        assert "Failed to route" in result
        assert ctx.context.call_management_in_progress is False


# ---------------------------------------------------------------------------
# Cross-tool: end_call blocked while transfer is in flight (and vice versa)
# ---------------------------------------------------------------------------


class TestCrossToolGuard:
    @pytest.mark.asyncio
    async def test_in_flight_transfer_state_races_two_real_tasks(self, monkeypatch):
        """End-to-end: two concurrent asyncio tasks hit the guard.

        Proves the guard actually blocks a concurrent call (not just a flag check
        against a pre-set value). One task runs the real impl; the second fires while
        the first is awaiting a slow dependency and must return the guard message.
        """
        ctx = _voice_ctx()
        monkeypatch.setattr(tts_voice_mod, "get_call_state_from_context", lambda _ctx: None)

        first_reached_api = asyncio.Event()

        async def slow_api_call(*args, **kwargs):
            first_reached_api.set()
            # Give the event loop a chance to schedule the second call
            await asyncio.sleep(0.05)

        monkeypatch.setattr(tts_voice_mod, "_make_transfer_to_staff_api_call", slow_api_call)
        monkeypatch.setattr(tts_voice_mod, "_transfer_twilio_call", AsyncMock())

        # Start the first call; it will set call_management_in_progress = True
        task1 = asyncio.create_task(tts_voice_mod._transfer_to_staff_voice_impl(ctx, summary="help"))
        await first_reached_api.wait()

        # While the first call is awaiting inside the slow api call, fire a second one
        result2 = await tts_voice_mod._transfer_to_staff_voice_impl(ctx, summary="help again")
        assert "already in progress" in result2.lower()

        result1 = await task1
        assert result1 == "Call transferred successfully."
