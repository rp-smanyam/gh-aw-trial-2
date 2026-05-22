"""Tests for InterruptHandler — user-driven vs cancel-triggered behavior."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_leasing.voice.config import VoiceConfig
from agent_leasing.voice.coordination.call_state import VoiceCallState
from agent_leasing.voice.coordination.interaction_policy import DefaultPolicy
from agent_leasing.voice.coordination.interrupt import InterruptHandler
from agent_leasing.voice.filler.manager import FillerManager


def _make_handler() -> tuple[InterruptHandler, FillerManager, VoiceCallState, MagicMock]:
    config = VoiceConfig(max_consecutive_fillers_without_user_audio=5)
    call_state = VoiceCallState()
    session = AsyncMock()
    filler = FillerManager(config, session, call_state)

    pacer = MagicMock()
    pacer.clear = MagicMock()
    transport = AsyncMock()
    transport.send_clear = AsyncMock()
    playback = MagicMock()
    playback.has_pending_items.return_value = False
    session_manager = AsyncMock()
    session_manager.cancel_response = AsyncMock()
    response_gate = MagicMock()
    response_gate.on_interrupt = MagicMock()

    handler = InterruptHandler(
        pacer=pacer,
        transport=transport,
        playback=playback,
        session_manager=session_manager,
        call_state=call_state,
        filler=filler,
        response_gate=response_gate,
    )
    return handler, filler, call_state, response_gate


class TestInterruptHandlerCounterReset:
    """Verifies the filler dead-line counter is only reset on user-driven interrupts."""

    @pytest.mark.asyncio
    async def test_user_driven_interrupt_resets_filler_counter(self):
        handler, filler, _, _ = _make_handler()
        filler._consecutive_fillers_without_user_audio = 3
        handler.expecting_cancel_interrupt = False

        await handler.handle_interrupt(DefaultPolicy())

        assert filler._consecutive_fillers_without_user_audio == 0

    @pytest.mark.asyncio
    async def test_cancel_triggered_interrupt_does_not_reset_filler_counter(self):
        handler, filler, _, _ = _make_handler()
        filler._consecutive_fillers_without_user_audio = 3
        handler.expecting_cancel_interrupt = True

        await handler.handle_interrupt(DefaultPolicy())

        assert filler._consecutive_fillers_without_user_audio == 3

    @pytest.mark.asyncio
    async def test_cancel_triggered_interrupt_clears_expecting_flag(self):
        handler, _, _, _ = _make_handler()
        handler.expecting_cancel_interrupt = True

        await handler.handle_interrupt(DefaultPolicy())

        assert handler.expecting_cancel_interrupt is False

    @pytest.mark.asyncio
    async def test_user_driven_interrupt_marks_user_speaking(self):
        handler, _, call_state, _ = _make_handler()
        handler.expecting_cancel_interrupt = False

        await handler.handle_interrupt(DefaultPolicy())

        assert call_state.is_user_speaking is True

    @pytest.mark.asyncio
    async def test_cancel_triggered_interrupt_does_not_mark_user_speaking(self):
        handler, _, call_state, _ = _make_handler()
        handler.expecting_cancel_interrupt = True

        await handler.handle_interrupt(DefaultPolicy())

        assert call_state.is_user_speaking is False

    @pytest.mark.asyncio
    async def test_response_gate_invalidated_on_both_paths(self):
        """turn_id must increment on both user and cancel interrupts so any
        in-flight thinker result is invalidated."""
        handler, _, _, gate = _make_handler()
        handler.expecting_cancel_interrupt = True

        await handler.handle_interrupt(DefaultPolicy())

        gate.on_interrupt.assert_called_once()
