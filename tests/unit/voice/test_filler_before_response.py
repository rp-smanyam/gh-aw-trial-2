"""Regression tests for issue #1641 — duplicate filler phrase playback.

Root cause: ``_handle_filler_before_response`` called ``cancel_filler()``
unconditionally, even when the filler had already finished playing. With
gpt-realtime-2, an interrupt issued when nothing is in-flight causes the model
to regenerate the most recent assistant audio (the filler phrase) as its next
response.

Scenarios covered:
1. Filler already stopped → cancel_filler NOT called (no spurious interrupt).
2. Filler still playing → cancel_filler IS called (interrupt needed to stop it).
3. schedule_filler is called after, regardless of filler state.
4. Static: thinker result string includes THINKER_RESULT_PREFIX sentinel.
5. Static: VOICE_RESPONDER.md teaches the THINKER_RESULT_PREFIX sentinel.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock

from agent_leasing.voice.config import VoiceConfig
from agent_leasing.voice.coordination.call_state import VoiceCallState
from agent_leasing.voice.thinker.tool import (
    THINKER_RESULT_PREFIX,
    _handle_filler_before_response,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
VOICE_PROMPT = REPO_ROOT / "src" / "agent_leasing" / "agent" / "resident_one_agent" / "VOICE_RESPONDER.md"
TOOL_SOURCE = REPO_ROOT / "src" / "agent_leasing" / "voice" / "thinker" / "tool.py"


def _make_config(strategy: str = "hybrid", wait_timeout: float = 0.0) -> VoiceConfig:
    return VoiceConfig(filler_handling_strategy=strategy, filler_wait_timeout_seconds=wait_timeout)


class TestFillerBeforeResponseInterruptGating:
    async def test_no_interrupt_when_filler_already_stopped(self):
        """When the filler finishes before the thinker returns, cancel_filler must
        NOT be called — a spurious interrupt causes gpt-realtime-2 to regenerate
        the prior filler audio (issue #1641 duplicate playback).
        """
        call_state = VoiceCallState()
        assert not call_state.is_agent_speaking
        assert not call_state.is_filler_playing

        callbacks = AsyncMock()
        await _handle_filler_before_response(callbacks, call_state, _make_config())

        callbacks.cancel_filler.assert_not_called()

    async def test_interrupt_fired_when_filler_still_playing(self):
        """When the filler is still playing, cancel_filler must be called to stop
        it before delivering the thinker response.
        """
        call_state = VoiceCallState()
        call_state.mark_agent_speaking_started(is_filler=True)

        callbacks = AsyncMock()
        await _handle_filler_before_response(callbacks, call_state, _make_config())

        callbacks.cancel_filler.assert_called_once()

    async def test_schedule_filler_always_called_after(self):
        """schedule_filler must be called at the end regardless of filler state."""
        call_state = VoiceCallState()
        callbacks = AsyncMock()

        await _handle_filler_before_response(callbacks, call_state, _make_config())

        assert callbacks.schedule_filler.called


class TestThinkerResultSentinel:
    def test_sentinel_constant_is_thinker_prefixed(self):
        """THINKER_RESULT_PREFIX must use the <thinker:> namespace so the prompt
        rule can match it with the same prefix check used for THINKER_NO_OUTPUT.
        """
        assert THINKER_RESULT_PREFIX.startswith("<thinker:")
        assert THINKER_RESULT_PREFIX == "<thinker:result/>"

    def test_tool_return_includes_sentinel(self):
        """The successful thinker tool-return string must start with the sentinel
        so the realtime model suppresses any carry-over filler phrase.
        """
        source = TOOL_SOURCE.read_text(encoding="utf-8")
        assert re.search(r"^\s+return\b.*THINKER_RESULT_PREFIX", source, re.MULTILINE), (
            "Expected the thinker tool to return THINKER_RESULT_PREFIX — see issue #1641."
        )

    def test_voice_responder_teaches_result_sentinel(self):
        """VOICE_RESPONDER.md must teach gpt-realtime-2 the THINKER_RESULT_PREFIX
        sentinel so it suppresses carry-over filler when delivering the response.
        """
        prompt = VOICE_PROMPT.read_text(encoding="utf-8")
        assert "<thinker:result/>" in prompt, (
            f"Expected {VOICE_PROMPT.name} to reference `<thinker:result/>` — see issue #1641."
        )
        assert "THINKER RESULT SENTINEL" in prompt, (
            f"Expected a `THINKER RESULT SENTINEL` rule in {VOICE_PROMPT.name}."
        )
