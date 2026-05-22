"""Tests for the trace-context inheritance in ``_init_full_voice_agent``.

The background task started in ``_setup_voice_agent`` inherits the parent's
``trace()`` contextvar via ``asyncio.create_task``'s context snapshot.  Opening
a fresh ``trace()`` inside the background task is therefore redundant and
triggers the SDK's "Trace already exists. Creating a new trace, but this is
probably a mistake." warning.

The fix uses ``get_current_trace()`` to skip the inner ``trace()`` when one is
already active in the inherited context.  These tests verify both branches.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_leasing.voice.handler import VoiceHandler


def _make_handler() -> VoiceHandler:
    handler = VoiceHandler.__new__(VoiceHandler)
    handler.voice_agent = MagicMock()
    handler.voice_agent.setup = AsyncMock()
    handler._parallel_init_span = MagicMock()
    # ``attach`` returns a context manager; using a MagicMock makes both
    # ``__enter__`` and ``__exit__`` no-ops.
    handler._parallel_init_span.attach = MagicMock(return_value=MagicMock())
    handler.root_run = MagicMock()
    return handler


class TestInitFullVoiceAgentTraceContext:
    @pytest.mark.asyncio
    async def test_skips_trace_when_one_is_already_active(self):
        """Inherited trace context (from the parent task) → don't open a new one."""
        handler = _make_handler()

        with (
            patch("agent_leasing.voice.handler.get_current_trace", return_value=MagicMock()),
            patch("agent_leasing.voice.handler.trace") as trace_mock,
        ):
            await handler._init_full_voice_agent(trace_id="t_x", group_id="g_x")

        trace_mock.assert_not_called()
        handler.voice_agent.setup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_opens_trace_when_none_active(self):
        """No inherited trace (parent context exited) → open a fresh one."""
        handler = _make_handler()

        with (
            patch("agent_leasing.voice.handler.get_current_trace", return_value=None),
            patch("agent_leasing.voice.handler.trace") as trace_mock,
        ):
            await handler._init_full_voice_agent(trace_id="t_x", group_id="g_x")

        trace_mock.assert_called_once_with(
            workflow_name="Resident One Voice",
            trace_id="t_x",
            group_id="g_x",
        )
        handler.voice_agent.setup.assert_awaited_once()
