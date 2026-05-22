"""Tests for the v2 active-response-in-progress race recovery.

Ports the v1 cancel-and-retry path from ``twilio_handler.py:1125-1190`` to
``VoiceHandler._handle_session_error`` so the heavyweight ``recover_session()``
rebuild is avoided for this specific recoverable race.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_leasing.voice.coordination.interrupt import InterruptHandler
from agent_leasing.voice.handler import VoiceHandler, _is_active_response_race


def _make_handler() -> VoiceHandler:
    handler = VoiceHandler.__new__(VoiceHandler)

    handler.ctx = MagicMock()
    handler.ctx.conversation_language = "en"

    handler.voice_agent = MagicMock()
    handler.voice_agent.agent.return_value = MagicMock()

    handler.session_manager = MagicMock()
    handler.session_manager.force_cancel_response = AsyncMock()
    handler.session_manager.create_response = AsyncMock()
    handler.session_manager.is_response_active = MagicMock(return_value=False)

    # Real InterruptHandler so we can verify the expecting_cancel_interrupt flag.
    handler.interrupt_handler = InterruptHandler.__new__(InterruptHandler)
    handler.interrupt_handler.expecting_cancel_interrupt = False

    return handler


def _active_response_error() -> SimpleNamespace:
    """A RealtimeError-shaped event for the active-response race."""
    return SimpleNamespace(
        error=Exception("Conversation already has an active response in progress: resp_xyz"),
    )


class TestActiveResponseRaceMatching:
    def test_matches_typed_code_on_realtime_error(self):
        """Prefer the OpenAI realtime ``Error.code`` field — the message string
        could change in a future SDK release, the code is the contract.
        """
        inner = SimpleNamespace(message="anything", code="conversation_already_has_active_response")
        event = SimpleNamespace(error=inner)
        assert _is_active_response_race(event) is True

    def test_matches_substring_on_realtime_error_without_code(self):
        event = SimpleNamespace(error=Exception("Conversation already has an active response in progress: resp_x"))
        assert _is_active_response_race(event) is True

    def test_matches_substring_on_raw_model_exception_event(self):
        inner = SimpleNamespace(exception=Exception("...already has an active response in progress..."))
        outer = SimpleNamespace(data=inner)
        assert _is_active_response_race(outer) is True

    def test_does_not_match_unrelated_error(self):
        event = SimpleNamespace(error=Exception("websocket closed unexpectedly"))
        assert _is_active_response_race(event) is False

    def test_does_not_match_unrelated_typed_code(self):
        inner = SimpleNamespace(message="other", code="some_other_code")
        event = SimpleNamespace(error=inner)
        assert _is_active_response_race(event) is False


class TestActiveResponseRaceRecovery:
    @pytest.mark.asyncio
    async def test_cancels_and_retries_on_active_response_error(self):
        handler = _make_handler()
        # First poll iteration sees the response cleared.
        handler.session_manager.is_response_active.return_value = False

        with _patch_recover_session() as recover:
            await handler._handle_session_error(_active_response_error())

        handler.session_manager.force_cancel_response.assert_awaited_once()
        handler.session_manager.create_response.assert_awaited_once_with(output_modalities=["audio"])
        recover.assert_not_awaited()
        assert handler.interrupt_handler.expecting_cancel_interrupt is False

    @pytest.mark.asyncio
    async def test_sets_expecting_cancel_interrupt_during_cancel(self):
        handler = _make_handler()

        observed: list[bool] = []

        async def record_flag() -> None:
            observed.append(handler.interrupt_handler.expecting_cancel_interrupt)

        handler.session_manager.force_cancel_response.side_effect = record_flag

        with _patch_recover_session():
            await handler._handle_session_error(_active_response_error())

        assert observed == [True]
        assert handler.interrupt_handler.expecting_cancel_interrupt is False

    @pytest.mark.asyncio
    async def test_skips_retry_when_cancellation_not_confirmed(self):
        handler = _make_handler()
        handler.session_manager.is_response_active.return_value = True  # never clears

        with _patch_recover_session() as recover:
            await handler._handle_session_error(_active_response_error())

        handler.session_manager.force_cancel_response.assert_awaited_once()
        handler.session_manager.create_response.assert_not_awaited()
        recover.assert_not_awaited()
        assert handler.interrupt_handler.expecting_cancel_interrupt is False

    @pytest.mark.asyncio
    async def test_force_cancel_exception_does_not_propagate(self):
        handler = _make_handler()
        handler.session_manager.force_cancel_response.side_effect = RuntimeError("boom")

        with _patch_recover_session() as recover:
            await handler._handle_session_error(_active_response_error())

        handler.session_manager.create_response.assert_not_awaited()
        recover.assert_not_awaited()
        assert handler.interrupt_handler.expecting_cancel_interrupt is False

    @pytest.mark.asyncio
    async def test_unrelated_error_falls_through_to_recover_session(self):
        handler = _make_handler()
        unrelated = SimpleNamespace(error=Exception("websocket closed unexpectedly"))

        with _patch_recover_session() as recover:
            await handler._handle_session_error(unrelated)

        recover.assert_awaited_once()
        handler.session_manager.force_cancel_response.assert_not_awaited()
        handler.session_manager.create_response.assert_not_awaited()


def _patch_recover_session():
    """Patch the ``recover_session`` symbol imported into ``handler.py``."""
    from unittest.mock import patch

    return patch("agent_leasing.voice.handler.recover_session", new_callable=AsyncMock)
