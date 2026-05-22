"""Tests for VoiceHandler tracing on user-message completion.

Mirrors the v1 behavior added in KNCK-38992: when a user message reaches
``status="completed"`` in a ``history_updated`` event, fire the trace
immediately so HumanMessage spans reflect speech duration rather than
the full pipeline time waited until ``agent_end``.
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from agents.realtime import UserMessageItem

from agent_leasing.voice.coordination.call_state import VoiceCallState
from agent_leasing.voice.handler import VoiceHandler
from agent_leasing.voice.tracing.langsmith import VoiceTracer


def _make_user_item(item_id: str, status: str = "completed") -> MagicMock:
    item = MagicMock(spec=UserMessageItem)
    item.role = "user"
    item.status = status
    item.item_id = item_id
    return item


def _make_history_updated_event(items: list) -> SimpleNamespace:
    return SimpleNamespace(history=items)


def _make_handler(*, tracer=None) -> VoiceHandler:
    """Build a VoiceHandler with the minimum collaborators for history-updated tracing."""
    handler = VoiceHandler.__new__(VoiceHandler)

    handler.ctx = MagicMock()
    handler.ctx.history = []
    handler.ctx.rendered_system_prompt = None

    handler.session_manager = MagicMock()
    handler.session_manager.transcript_cache = {}
    handler.session_manager.on_history_updated = MagicMock()

    handler.call_state = VoiceCallState()
    handler.playback = MagicMock()
    handler.playback.message_start_times = {}
    handler.playback.message_end_times = {}
    handler.filler = MagicMock()
    handler.filler.filler_item_ids = set()
    handler.filler.schedule = MagicMock()

    handler.tracer = tracer if tracer is not None else MagicMock()
    handler.root_run = MagicMock()

    return handler


class TestUserCompletedFiresTrace:
    @pytest.mark.asyncio
    async def test_user_completed_fires_trace_task(self):
        """Fires ``tracer.fire_trace_task`` when a user item reaches completed status."""
        handler = _make_handler()
        event = _make_history_updated_event([_make_user_item("u1", "completed")])

        with patch(
            "agent_leasing.voice.handler.realtime_history_to_input_list",
            return_value=[],
        ):
            await handler._handle_history_updated(event)

        assert handler.tracer.fire_trace_task.call_count == 1
        kwargs = handler.tracer.fire_trace_task.call_args.kwargs
        assert kwargs["root_run"] is handler.root_run
        assert kwargs["filler_item_ids"] is handler.filler.filler_item_ids

    @pytest.mark.asyncio
    async def test_user_in_progress_does_not_fire_trace(self):
        """Items still in_progress do not trigger immediate tracing."""
        handler = _make_handler()
        event = _make_history_updated_event([_make_user_item("u1", "in_progress")])

        with patch(
            "agent_leasing.voice.handler.realtime_history_to_input_list",
            return_value=[],
        ):
            await handler._handle_history_updated(event)

        handler.tracer.fire_trace_task.assert_not_called()


class TestDedupAcrossEvents:
    """User message traced at completion must not be re-traced when agent_end fires."""

    @pytest.mark.asyncio
    async def test_dedup_completion_then_agent_end(self):
        tracer = VoiceTracer()
        handler = _make_handler(tracer=tracer)

        item_id = "u1"
        handler.playback.message_start_times = {item_id: datetime.datetime.now(datetime.UTC)}
        handler.playback.message_end_times = {item_id: datetime.datetime.now(datetime.UTC)}
        handler.session_manager.history = [{"role": "user", "item_id": item_id, "content": "Hello"}]
        # _get_trace_history returns a list of message dicts; mock a history list directly
        handler._get_trace_history = MagicMock(return_value=handler.session_manager.history)

        # Trace at user completion
        with patch(
            "agent_leasing.voice.handler.realtime_history_to_input_list",
            return_value=[],
        ):
            await handler._handle_history_updated(_make_history_updated_event([_make_user_item(item_id, "completed")]))
        # Drain the in-flight trace task
        await tracer.finalize(
            history=handler.session_manager.history,
            root_run=handler.root_run,
            message_start_times=handler.playback.message_start_times,
            message_end_times=handler.playback.message_end_times,
            filler_item_ids=set(),
        )

        # agent_end fires later — same item, should NOT create a second child run
        await handler._handle_agent_end(SimpleNamespace())
        # Drain again
        await tracer.finalize(
            history=handler.session_manager.history,
            root_run=handler.root_run,
            message_start_times=handler.playback.message_start_times,
            message_end_times=handler.playback.message_end_times,
            filler_item_ids=set(),
        )

        # Exactly one HumanMessage child run was created
        human_calls = [
            call for call in handler.root_run.create_child.call_args_list if call.kwargs.get("name") == "HumanMessage"
        ]
        assert len(human_calls) == 1
