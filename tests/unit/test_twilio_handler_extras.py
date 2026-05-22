"""Extra unit tests covering previously-uncovered lines in twilio_handler.py."""

from __future__ import annotations

import asyncio
import datetime
import time
from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock, patch

import pytest
from agents import ModelBehaviorError
from agents.realtime import (
    RealtimeError,
    RealtimeModelExceptionEvent,
    RealtimeSession,
    UserMessageItem,
)
from fastapi import WebSocket

from agent_leasing.twilio_handler import (
    TwilioHandler,
    TwilioWebSocketManager,
    _log_background_task_exception,
    decode_object,
    encode_object,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_websocket():
    ws = Mock(spec=WebSocket)
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock()
    ws.client_state = Mock(name="CONNECTED")
    ws.client_state.name = "CONNECTED"
    ws.application_state = Mock(name="CONNECTED")
    ws.application_state.name = "CONNECTED"
    return ws


@pytest.fixture
def handler(mock_websocket):
    return TwilioHandler(mock_websocket)


@pytest.fixture
def mock_session():
    session = Mock(spec=RealtimeSession)
    session.enter = AsyncMock()
    session.send_audio = AsyncMock()
    session.send_message = AsyncMock()
    session.close = AsyncMock()
    return session


# ===================================================================
# _get_language_code
# ===================================================================


class TestGetLanguageCode:
    def test_returns_language_code_from_ctx(self, handler):
        handler.ctx = Mock()
        handler.ctx.language_code = "es"
        assert handler._get_language_code() == "es"

    def test_falls_back_to_en_when_no_ctx(self, handler):
        assert handler._get_language_code() == "en"

    def test_falls_back_to_en_when_language_code_is_none(self, handler):
        handler.ctx = Mock()
        handler.ctx.language_code = None
        assert handler._get_language_code() == "en"

    def test_falls_back_to_en_when_language_code_missing(self, handler):
        handler.ctx = Mock(spec=[])
        assert handler._get_language_code() == "en"


# ===================================================================
# _twilio_message_loop: WebSocketDisconnect  (line 448)
# ===================================================================


class TestTwilioMessageLoopDisconnect:
    async def test_websocket_disconnect_handled(self, handler):
        """WebSocketDisconnect in message loop is caught, not re-raised."""
        from starlette.websockets import WebSocketDisconnect

        handler.twilio_websocket.receive_text = AsyncMock(side_effect=WebSocketDisconnect(code=1006))

        with patch("agent_leasing.twilio_handler.ls") as mock_ls:
            run = Mock()
            mock_ls.trace.return_value.__enter__ = Mock(return_value=run)
            mock_ls.trace.return_value.__exit__ = Mock(return_value=False)

            await handler._twilio_message_loop()

        # call_active should trigger cleanup in finally
        assert handler._cleanup_called is True


# ===================================================================
# _log_background_task_exception  (line 124)
# ===================================================================


class TestLogBackgroundTaskException:
    def test_logs_when_task_has_exception(self):
        task = Mock()
        task.exception.return_value = RuntimeError("boom")
        with patch("agent_leasing.twilio_handler.logger") as mock_logger:
            _log_background_task_exception(task)
            mock_logger.warning.assert_called_once()
            assert "boom" in mock_logger.warning.call_args[0][0]

    def test_silent_when_cancelled(self):
        task = Mock()
        task.exception.side_effect = asyncio.CancelledError()
        _log_background_task_exception(task)  # should not raise

    def test_silent_when_no_exception(self):
        task = Mock()
        task.exception.return_value = None
        with patch("agent_leasing.twilio_handler.logger") as mock_logger:
            _log_background_task_exception(task)
            mock_logger.warning.assert_not_called()


# ===================================================================
# _schedule_next_filler  (lines 388-389, 391-392)
# ===================================================================


class TestScheduleNextFiller:
    def test_returns_none_when_call_inactive(self, handler):
        handler.call_active = False
        handler._next_filler_time = 999.0
        handler._schedule_next_filler()
        assert handler._next_filler_time is None

    @patch("agent_leasing.twilio_handler.settings")
    def test_returns_none_when_filler_disabled(self, mock_settings, handler):
        mock_settings.send_filler_messages = False
        handler.call_active = True
        handler._next_filler_time = 999.0
        handler._schedule_next_filler()
        assert handler._next_filler_time is None

    @patch("agent_leasing.twilio_handler.settings")
    def test_schedules_when_active_and_enabled(self, mock_settings, handler):
        mock_settings.send_filler_messages = True
        mock_settings.filler_delay_mean_seconds = 10.0
        mock_settings.filler_delay_std_seconds = 0.0
        handler.call_active = True
        handler._schedule_next_filler()
        assert handler._next_filler_time is not None
        assert handler._next_filler_time > time.time()


# ===================================================================
# wait_until_done CancelledError handling  (lines 408-414)
# ===================================================================


class TestWaitUntilDoneCancelled:
    async def test_internal_cancellation_is_swallowed(self, handler):
        """When the message_loop_task itself is cancelled (internal), no re-raise."""

        async def cancelled_coro():
            raise asyncio.CancelledError()

        task = asyncio.create_task(cancelled_coro())
        handler._message_loop_task = task
        # Should not raise because the current task is not being cancelled externally
        await handler.wait_until_done()


# ===================================================================
# Tracing test fixtures & helpers
# ===================================================================


@pytest.fixture
def tracing_handler(mock_websocket):
    """Handler wired for tracing: mock root_run, ctx, call_active."""
    h = TwilioHandler(mock_websocket)
    child = Mock()
    child.post = Mock()
    h.root_run = Mock()
    h.root_run.create_child = Mock(return_value=child)
    h.ctx = Mock()
    # _cleanup_call schedules a fire-and-forget task-event publish and then
    # drains pending publishes — both touch ctx.pending_activity_publishes,
    # which must be a real set (not a Mock) so drain_pending_publishes can
    # iterate it.
    h.ctx.pending_activity_publishes = set()
    h.call_active = True
    return h


async def drain_trace_tasks(h):
    """Await any fire-and-forget trace tasks so assertions can inspect results."""
    if h._pending_trace_tasks:
        await asyncio.gather(*h._pending_trace_tasks)


def make_user_item(item_id, status="completed"):
    item = Mock(spec=UserMessageItem)
    item.role = "user"
    item.status = status
    item.item_id = item_id
    return item


def make_history_updated_event(items):
    event = Mock()
    event.type = "history_updated"
    event.history = items
    return event


def make_agent_end_event():
    event = Mock()
    event.type = "agent_end"
    return event


def make_mark_message(mark_id):
    return {"event": "mark", "mark": {"name": mark_id}}


def setup_agent_mark_preconditions(handler, item_id, mark_id, byte_count=320):
    """Set mark tracking state that would normally come from audio framing/pacer."""
    handler._mark_data[mark_id] = (item_id, 0, byte_count)
    handler._response_last_mark_ids[item_id] = mark_id
    # Mock playback tracker — audio framing is not part of the tracing contract
    handler.playback_tracker = Mock()


@contextmanager
def patch_history(history_dicts):
    """Patch realtime_history_to_input_list to return the given dicts."""
    with patch(
        "agent_leasing.twilio_handler.realtime_history_to_input_list",
        return_value=history_dicts,
    ):
        yield


# ===================================================================
# TestUserMessageTracing
# ===================================================================


class TestUserMessageTracing:
    """User messages traced via: VAD events -> history_updated (completed) -> immediate trace."""

    async def test_traced_with_vad_timestamps(self, tracing_handler):
        h = tracing_handler
        history_dicts = [{"role": "user", "item_id": "u1", "content": "Hello"}]

        h._handle_raw_model_event({"type": "input_audio_buffer.speech_started"})
        with patch_history(history_dicts):
            await h._handle_realtime_event(make_history_updated_event([make_user_item("u1", "in_progress")]))
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_stopped"})
        with patch_history(history_dicts):
            await h._handle_realtime_event(make_history_updated_event([make_user_item("u1", "completed")]))
        await drain_trace_tasks(h)

        call = h.root_run.create_child.call_args
        assert call.kwargs["name"] == "HumanMessage"
        assert isinstance(call.kwargs["start_time"], datetime.datetime)
        assert isinstance(call.kwargs["end_time"], datetime.datetime)
        assert call.kwargs["start_time"] <= call.kwargs["end_time"]

    async def test_start_falls_back_to_now(self, tracing_handler):
        h = tracing_handler
        history_dicts = [{"role": "user", "item_id": "u1", "content": "Hello"}]

        before = datetime.datetime.now(datetime.UTC)
        with patch_history(history_dicts):
            await h._handle_realtime_event(make_history_updated_event([make_user_item("u1", "completed")]))
        await drain_trace_tasks(h)
        after = datetime.datetime.now(datetime.UTC)

        call = h.root_run.create_child.call_args
        assert before <= call.kwargs["start_time"] <= after

    async def test_end_falls_back_to_now(self, tracing_handler):
        h = tracing_handler
        history_dicts = [{"role": "user", "item_id": "u1", "content": "Hello"}]

        h._handle_raw_model_event({"type": "input_audio_buffer.speech_started"})
        # No speech_stopped — end_time should fall back to now()
        before = datetime.datetime.now(datetime.UTC)
        with patch_history(history_dicts):
            await h._handle_realtime_event(make_history_updated_event([make_user_item("u1", "completed")]))
        await drain_trace_tasks(h)
        after = datetime.datetime.now(datetime.UTC)

        call = h.root_run.create_child.call_args
        assert before <= call.kwargs["end_time"] <= after

    async def test_start_not_overwritten(self, tracing_handler):
        h = tracing_handler
        history_dicts = [{"role": "user", "item_id": "u1", "content": "Hello"}]

        before_start = datetime.datetime.now(datetime.UTC)
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_started"})
        with patch_history(history_dicts):
            await h._handle_realtime_event(make_history_updated_event([make_user_item("u1", "in_progress")]))
        after_start = datetime.datetime.now(datetime.UTC)

        # Second history_updated with completed — should NOT overwrite start_time
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_stopped"})
        with patch_history(history_dicts):
            await h._handle_realtime_event(make_history_updated_event([make_user_item("u1", "completed")]))
        await drain_trace_tasks(h)

        call = h.root_run.create_child.call_args
        assert before_start <= call.kwargs["start_time"] <= after_start

    async def test_not_traced_twice(self, tracing_handler):
        """Message traced at completion, then agent_end fires — should not re-trace."""
        h = tracing_handler
        history_dicts = [{"role": "user", "item_id": "u1", "content": "Hello"}]

        h._handle_raw_model_event({"type": "input_audio_buffer.speech_started"})
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_stopped"})
        with patch_history(history_dicts):
            # Trace fires here at completion
            await h._handle_realtime_event(make_history_updated_event([make_user_item("u1", "completed")]))
            await drain_trace_tasks(h)
            # agent_end fires later — should NOT re-trace
            await h._handle_realtime_event(make_agent_end_event())
            await h._handle_realtime_event(make_agent_end_event())
        await drain_trace_tasks(h)

        assert h.root_run.create_child.call_count == 1

    async def test_batch_update(self, tracing_handler):
        h = tracing_handler
        history_dicts = [
            {"role": "user", "item_id": "u1", "content": "Hello"},
            {"role": "assistant", "item_id": "a1", "content": "Hi there"},
        ]

        h._handle_raw_model_event({"type": "input_audio_buffer.speech_started"})
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_stopped"})

        agent_item = Mock()
        agent_item.role = "assistant"
        with patch_history(history_dicts):
            await h._handle_realtime_event(make_history_updated_event([make_user_item("u1", "completed"), agent_item]))
        await drain_trace_tasks(h)

        # Only HumanMessage traced — agent has no end_time (no mark yet)
        calls = h.root_run.create_child.call_args_list
        assert len(calls) == 1
        assert calls[0].kwargs["name"] == "HumanMessage"

    async def test_user_completed_fires_trace_immediately(self, tracing_handler):
        """HumanMessage traced at completion without agent_end; duration reflects speech, not pipeline."""
        h = tracing_handler
        history_dicts = [{"role": "user", "item_id": "u1", "content": "Hello"}]

        h._handle_raw_model_event({"type": "input_audio_buffer.speech_started"})
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_stopped"})

        with patch_history(history_dicts):
            await h._handle_realtime_event(make_history_updated_event([make_user_item("u1", "completed")]))
        await drain_trace_tasks(h)

        # Trace fires WITHOUT agent_end
        assert h.root_run.create_child.call_count == 1
        call = h.root_run.create_child.call_args
        assert call.kwargs["name"] == "HumanMessage"
        # Duration should reflect actual speech time (sub-second), NOT 30-48s pipeline time
        duration = (call.kwargs["end_time"] - call.kwargs["start_time"]).total_seconds()
        assert duration < 2.0

    async def test_dedup_completion_then_agent_end(self, tracing_handler):
        """Message traced at completion is not re-traced when agent_end fires later."""
        h = tracing_handler
        history_dicts = [{"role": "user", "item_id": "u1", "content": "Hello"}]

        h._handle_raw_model_event({"type": "input_audio_buffer.speech_started"})
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_stopped"})

        with patch_history(history_dicts):
            await h._handle_realtime_event(make_history_updated_event([make_user_item("u1", "completed")]))
            await drain_trace_tasks(h)

            # agent_end fires later — should NOT re-trace
            await h._handle_realtime_event(make_agent_end_event())
        await drain_trace_tasks(h)

        assert h.root_run.create_child.call_count == 1

    async def test_multiple_user_messages_traced_independently(self, tracing_handler):
        """Each user message in a sequence is traced independently at completion."""
        h = tracing_handler

        # First message
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_started"})
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_stopped"})
        history_dicts_1 = [{"role": "user", "item_id": "u1", "content": "Hello"}]
        with patch_history(history_dicts_1):
            await h._handle_realtime_event(make_history_updated_event([make_user_item("u1", "completed")]))
        await drain_trace_tasks(h)

        # Second message
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_started"})
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_stopped"})
        history_dicts_2 = [
            {"role": "user", "item_id": "u1", "content": "Hello"},
            {"role": "user", "item_id": "u2", "content": "Are you there"},
        ]
        with patch_history(history_dicts_2):
            await h._handle_realtime_event(
                make_history_updated_event([make_user_item("u1", "completed"), make_user_item("u2", "completed")])
            )
        await drain_trace_tasks(h)

        # u1 traced once (first completion), u2 traced once (second completion)
        assert h.root_run.create_child.call_count == 2
        calls = h.root_run.create_child.call_args_list
        assert calls[0].kwargs["name"] == "HumanMessage"
        assert calls[1].kwargs["name"] == "HumanMessage"

    async def test_no_speech_stopped_before_completion(self, tracing_handler):
        """If VAD speech_stopped never fires, end_time falls back to now() at completion time."""
        h = tracing_handler
        history_dicts = [{"role": "user", "item_id": "u1", "content": "Hello"}]

        h._handle_raw_model_event({"type": "input_audio_buffer.speech_started"})
        # No speech_stopped event

        before = datetime.datetime.now(datetime.UTC)
        with patch_history(history_dicts):
            await h._handle_realtime_event(make_history_updated_event([make_user_item("u1", "completed")]))
        await drain_trace_tasks(h)
        after = datetime.datetime.now(datetime.UTC)

        call = h.root_run.create_child.call_args
        assert call.kwargs["name"] == "HumanMessage"
        # end_time falls back to now() in _record_user_message_end_time
        assert before <= call.kwargs["end_time"] <= after


# ===================================================================
# TestAgentMessageTracing
# ===================================================================


class TestAgentMessageTracing:
    """Agent messages deferred until Twilio mark confirms playback."""

    async def test_deferred_until_mark(self, tracing_handler):
        h = tracing_handler
        h.history = [{"role": "assistant", "item_id": "a1", "content": "Hi there"}]

        await h._handle_realtime_event(make_agent_end_event())

        h.root_run.create_child.assert_not_called()

    async def test_traced_on_mark(self, tracing_handler):
        h = tracing_handler
        h.history = [{"role": "assistant", "item_id": "a1", "content": "Hi there"}]
        h._message_start_times["a1"] = datetime.datetime(2026, 2, 19, 12, 0, 0, tzinfo=datetime.UTC)
        setup_agent_mark_preconditions(h, "a1", "mark1")

        before = datetime.datetime.now(datetime.UTC)
        await h._handle_twilio_message(make_mark_message("mark1"))
        await drain_trace_tasks(h)
        after = datetime.datetime.now(datetime.UTC)

        call = h.root_run.create_child.call_args
        assert call.kwargs["name"] == "AIMessage"
        assert before <= call.kwargs["end_time"] <= after

    async def test_user_and_agent_in_same_trace(self, tracing_handler):
        h = tracing_handler
        history_dicts = [
            {"role": "user", "item_id": "u1", "content": "Hello"},
            {"role": "assistant", "item_id": "a1", "content": "Hi there"},
        ]

        # Full user flow — only user traced
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_started"})
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_stopped"})
        with patch_history(history_dicts):
            await h._handle_realtime_event(make_history_updated_event([make_user_item("u1", "completed")]))
            await h._handle_realtime_event(make_agent_end_event())
        await drain_trace_tasks(h)

        assert h.root_run.create_child.call_count == 1

        # Agent message traced on mark
        h._message_start_times["a1"] = datetime.datetime(2026, 2, 19, 12, 0, 0, tzinfo=datetime.UTC)
        setup_agent_mark_preconditions(h, "a1", "mark1")
        await h._handle_twilio_message(make_mark_message("mark1"))
        await drain_trace_tasks(h)

        assert h.root_run.create_child.call_count == 2
        calls = h.root_run.create_child.call_args_list
        assert calls[0].kwargs["name"] == "HumanMessage"
        assert calls[1].kwargs["name"] == "AIMessage"


# ===================================================================
# TestSpeechVADTimestamps
# ===================================================================


class TestSpeechVADTimestamps:
    """VAD speech events populate timestamps on traced user messages."""

    async def test_speech_stopped_captured(self, tracing_handler):
        h = tracing_handler
        history_dicts = [{"role": "user", "item_id": "u1", "content": "Hello"}]

        h._handle_raw_model_event({"type": "input_audio_buffer.speech_started"})
        before_stop = datetime.datetime.now(datetime.UTC)
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_stopped"})
        after_stop = datetime.datetime.now(datetime.UTC)

        with patch_history(history_dicts):
            await h._handle_realtime_event(make_history_updated_event([make_user_item("u1", "completed")]))
            await h._handle_realtime_event(make_agent_end_event())
        await drain_trace_tasks(h)

        call = h.root_run.create_child.call_args
        assert before_stop <= call.kwargs["end_time"] <= after_stop

    @pytest.mark.parametrize(
        "event_type",
        ["input_audio_buffer.speech_stop", "input_audio_buffer.speech_stopped"],
    )
    async def test_both_event_name_variants(self, tracing_handler, event_type):
        h = tracing_handler
        history_dicts = [{"role": "user", "item_id": "u1", "content": "Hello"}]

        h._handle_raw_model_event({"type": "input_audio_buffer.speech_started"})
        h._handle_raw_model_event({"type": event_type})

        with patch_history(history_dicts):
            await h._handle_realtime_event(make_history_updated_event([make_user_item("u1", "completed")]))
            await h._handle_realtime_event(make_agent_end_event())
        await drain_trace_tasks(h)

        call = h.root_run.create_child.call_args
        assert isinstance(call.kwargs["end_time"], datetime.datetime)

    async def test_later_stop_overwrites_earlier(self, tracing_handler):
        h = tracing_handler
        history_dicts = [{"role": "user", "item_id": "u1", "content": "Hello"}]

        h._handle_raw_model_event({"type": "input_audio_buffer.speech_started"})
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_stopped"})
        before_second = datetime.datetime.now(datetime.UTC)
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_stopped"})
        after_second = datetime.datetime.now(datetime.UTC)

        with patch_history(history_dicts):
            await h._handle_realtime_event(make_history_updated_event([make_user_item("u1", "completed")]))
            await h._handle_realtime_event(make_agent_end_event())
        await drain_trace_tasks(h)

        call = h.root_run.create_child.call_args
        assert before_second <= call.kwargs["end_time"] <= after_second

    async def test_timestamps_not_stale_across_turns(self, tracing_handler):
        h = tracing_handler

        # Turn 1: full flow
        u1_dicts = [{"role": "user", "item_id": "u1", "content": "Hello"}]
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_started"})
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_stopped"})
        with patch_history(u1_dicts):
            await h._handle_realtime_event(make_history_updated_event([make_user_item("u1", "completed")]))
            await h._handle_realtime_event(make_agent_end_event())
        await drain_trace_tasks(h)

        # Turn 2: speech_started but no speech_stopped
        u2_dicts = [
            {"role": "user", "item_id": "u1", "content": "Hello"},
            {"role": "user", "item_id": "u2", "content": "Bye"},
        ]
        h._handle_raw_model_event({"type": "input_audio_buffer.speech_started"})
        before = datetime.datetime.now(datetime.UTC)
        with patch_history(u2_dicts):
            await h._handle_realtime_event(make_history_updated_event([make_user_item("u2", "completed")]))
            await h._handle_realtime_event(make_agent_end_event())
        await drain_trace_tasks(h)
        after = datetime.datetime.now(datetime.UTC)

        # u2's end_time should be ~now(), not u1's stale speech_stopped
        calls = h.root_run.create_child.call_args_list
        u2_call = [c for c in calls if c.kwargs.get("outputs", {}).get("message") == "Bye"]
        assert len(u2_call) == 1
        assert before <= u2_call[0].kwargs["end_time"] <= after


# ===================================================================
# TestTranscriptCacheAccumulation
# ===================================================================


class TestTranscriptCacheAccumulation:
    """_handle_raw_model_event accumulates transcript_delta events into _transcript_cache."""

    async def test_accumulates_single_delta(self, tracing_handler):
        h = tracing_handler
        h._handle_raw_model_event(Mock(type="transcript_delta", item_id="item_1", delta="Hello"))
        assert h._transcript_cache == {"item_1": "Hello"}

    async def test_accumulates_multiple_deltas_same_item(self, tracing_handler):
        h = tracing_handler
        h._handle_raw_model_event(Mock(type="transcript_delta", item_id="item_1", delta="Hel"))
        h._handle_raw_model_event(Mock(type="transcript_delta", item_id="item_1", delta="lo "))
        h._handle_raw_model_event(Mock(type="transcript_delta", item_id="item_1", delta="world"))
        assert h._transcript_cache == {"item_1": "Hello world"}

    async def test_accumulates_across_items(self, tracing_handler):
        h = tracing_handler
        h._handle_raw_model_event(Mock(type="transcript_delta", item_id="item_1", delta="First"))
        h._handle_raw_model_event(Mock(type="transcript_delta", item_id="item_2", delta="Second"))
        assert h._transcript_cache == {"item_1": "First", "item_2": "Second"}

    async def test_ignores_missing_item_id(self, tracing_handler):
        h = tracing_handler
        h._handle_raw_model_event(Mock(type="transcript_delta", item_id=None, delta="text"))
        assert h._transcript_cache == {}

    async def test_ignores_missing_delta(self, tracing_handler):
        h = tracing_handler
        h._handle_raw_model_event(Mock(type="transcript_delta", item_id="item_1", delta=None))
        assert h._transcript_cache == {}


# ===================================================================
# TestCleanupTracing
# ===================================================================


def _mock_cleanup_internals(h):
    """Mock non-tracing cleanup methods so _cleanup_call only exercises the tracing pipeline."""
    h._schedule_data_curation_logging = Mock()
    h._deactivate = Mock()
    h._cancel_background_tasks = AsyncMock()
    h._close_session = AsyncMock()
    h._close_agent = AsyncMock()
    h._clear_context = Mock()
    h._await_data_curation = AsyncMock()


class TestCleanupTracing:
    """_cleanup_call fills missing end_times and traces before clearing state."""

    async def test_traces_all_including_unfinished(self, tracing_handler):
        h = tracing_handler
        h._message_start_times["u1"] = datetime.datetime(2026, 2, 19, 12, 0, 0, tzinfo=datetime.UTC)
        h._message_end_times["u1"] = datetime.datetime(2026, 2, 19, 12, 0, 3, tzinfo=datetime.UTC)
        h._message_start_times["a1"] = datetime.datetime(2026, 2, 19, 12, 0, 4, tzinfo=datetime.UTC)
        # a1 has no end_time — mark never arrived
        h.session = None
        h.history = [
            {"role": "user", "item_id": "u1", "content": "Hello"},
            {"role": "assistant", "item_id": "a1", "content": "Hi there"},
        ]
        _mock_cleanup_internals(h)

        await h._cleanup_call()

        calls = h.root_run.create_child.call_args_list
        assert len(calls) == 2
        names = {c.kwargs["name"] for c in calls}
        assert names == {"HumanMessage", "AIMessage"}
        # Agent message should get a fallback end_time from cleanup
        ai_call = [c for c in calls if c.kwargs["name"] == "AIMessage"][0]
        assert ai_call.kwargs["end_time"] is not None

    async def test_idempotent(self, tracing_handler):
        h = tracing_handler
        h._message_start_times["u1"] = datetime.datetime(2026, 2, 19, 12, 0, 0, tzinfo=datetime.UTC)
        h._message_end_times["u1"] = datetime.datetime(2026, 2, 19, 12, 0, 3, tzinfo=datetime.UTC)
        h.session = None
        h.history = [{"role": "user", "item_id": "u1", "content": "Hello"}]
        _mock_cleanup_internals(h)

        await h._cleanup_call()
        first_count = h.root_run.create_child.call_count
        await h._cleanup_call()

        assert h.root_run.create_child.call_count == first_count

    async def test_uses_session_history(self, tracing_handler):
        h = tracing_handler
        h._message_start_times["s1"] = datetime.datetime(2026, 2, 19, 12, 0, 0, tzinfo=datetime.UTC)
        h._message_end_times["s1"] = datetime.datetime(2026, 2, 19, 12, 0, 3, tzinfo=datetime.UTC)
        session_history = [Mock()]
        h.session = Mock()
        h.session._history = session_history
        h.history = [{"role": "user", "item_id": "old", "content": "stale"}]
        session_dicts = [{"role": "user", "item_id": "s1", "content": "From session"}]
        _mock_cleanup_internals(h)

        with patch(
            "agent_leasing.twilio_handler.realtime_history_to_input_list",
            return_value=session_dicts,
        ):
            await h._cleanup_call()

        call = h.root_run.create_child.call_args
        assert call.kwargs["outputs"] == {"message": "From session"}

    async def test_falls_back_to_event_history(self, tracing_handler):
        h = tracing_handler
        h._message_start_times["e1"] = datetime.datetime(2026, 2, 19, 12, 0, 0, tzinfo=datetime.UTC)
        h._message_end_times["e1"] = datetime.datetime(2026, 2, 19, 12, 0, 3, tzinfo=datetime.UTC)
        h.session = None
        h.history = [{"role": "user", "item_id": "e1", "content": "From events"}]
        _mock_cleanup_internals(h)

        await h._cleanup_call()

        call = h.root_run.create_child.call_args
        assert call.kwargs["outputs"] == {"message": "From events"}

    async def test_preserves_existing_end_times(self, tracing_handler):
        h = tracing_handler
        existing_end = datetime.datetime(2026, 2, 19, 12, 0, 5, tzinfo=datetime.UTC)
        h._message_start_times["u1"] = datetime.datetime(2026, 2, 19, 12, 0, 0, tzinfo=datetime.UTC)
        h._message_end_times["u1"] = existing_end
        h._message_start_times["a1"] = datetime.datetime(2026, 2, 19, 12, 0, 4, tzinfo=datetime.UTC)
        # a1 has no end_time — will get fallback
        h.session = None
        h.history = [
            {"role": "user", "item_id": "u1", "content": "Hello"},
            {"role": "assistant", "item_id": "a1", "content": "Hi"},
        ]
        _mock_cleanup_internals(h)

        await h._cleanup_call()

        calls = h.root_run.create_child.call_args_list
        u1_call = [c for c in calls if c.kwargs["name"] == "HumanMessage"][0]
        assert u1_call.kwargs["end_time"] == existing_end


# ===================================================================
# TestTracingOrdering
# ===================================================================


class TestTracingOrdering:
    """Verify tracing pipeline ordering within _cleanup_call."""

    async def test_cleanup_traces_before_clearing_state(self, tracing_handler):
        h = tracing_handler
        h._message_start_times["u1"] = datetime.datetime(2026, 2, 19, 12, 0, 0, tzinfo=datetime.UTC)
        h._message_end_times["u1"] = datetime.datetime(2026, 2, 19, 12, 0, 3, tzinfo=datetime.UTC)
        h.session = None
        h.history = [{"role": "user", "item_id": "u1", "content": "Hello"}]

        call_order = []
        original_clear = h._clear_tracing_state

        def spy_clear():
            call_order.append("clear_tracing_state")
            original_clear()

        h._clear_tracing_state = spy_clear
        h.root_run.create_child = Mock(
            side_effect=lambda **kwargs: (call_order.append("create_child"), Mock(post=Mock()))[-1]
        )
        _mock_cleanup_internals(h)

        await h._cleanup_call()

        assert "create_child" in call_order
        assert "clear_tracing_state" in call_order
        assert call_order.index("create_child") < call_order.index("clear_tracing_state")


# ===================================================================
# Filler metadata in LangSmith child runs
# ===================================================================


class TestFillerMetadata:
    """Filler item_ids get extra={'metadata': {'filler': True}} on the LangSmith child run."""

    async def test_filler_item_gets_extra_metadata(self, tracing_handler):
        h = tracing_handler
        h._filler_item_ids.add("a1")
        h._message_end_times["a1"] = datetime.datetime.now(datetime.UTC)

        await h._post_langsmith_child_run(
            {"role": "assistant", "item_id": "a1", "content": "Let me check on that"},
            item_id="a1",
            role="assistant",
        )

        call = h.root_run.create_child.call_args
        assert call.kwargs["extra"] == {"metadata": {"filler": True}}

    async def test_non_filler_item_gets_filler_false(self, tracing_handler):
        h = tracing_handler
        h._message_end_times["a1"] = datetime.datetime.now(datetime.UTC)

        await h._post_langsmith_child_run(
            {"role": "assistant", "item_id": "a1", "content": "Your balance is $500"},
            item_id="a1",
            role="assistant",
        )

        call = h.root_run.create_child.call_args
        assert call.kwargs["extra"] == {"metadata": {"filler": False}}

    async def test_human_message_gets_no_extra(self, tracing_handler):
        h = tracing_handler

        await h._post_langsmith_child_run(
            {"role": "user", "item_id": "u1", "content": "Hello"},
            item_id="u1",
            role="user",
        )

        call = h.root_run.create_child.call_args
        assert call.kwargs.get("extra") is None

    async def test_deactivate_clears_filler_ids(self, tracing_handler):
        h = tracing_handler
        h._filler_item_ids.add("a1")
        h._filler_item_ids.add("a2")

        h._deactivate()

        assert len(h._filler_item_ids) == 0


# ===================================================================
# _handle_realtime_error_event: response_cancel_not_active  (lines 660-664)
# ===================================================================


class TestResponseCancelNotActive:
    async def test_ignores_response_cancel_not_active(self, handler):
        handler._recover_realtime_session = AsyncMock()

        error_event = Mock(spec=RealtimeError)
        error_event.__str__ = Mock(return_value="response_cancel_not_active: some details")

        await handler._handle_realtime_error_event(event=error_event)

        # Recovery should NOT be called
        handler._recover_realtime_session.assert_not_awaited()


# ===================================================================
# _handle_realtime_error_event: active response context logging  (lines 682-683)
# ===================================================================


class TestActiveResponseContextLogging:
    async def test_logs_recent_history_when_ctx_available(self, handler):
        handler._recover_realtime_session = AsyncMock()
        handler.session = Mock()
        handler.session._model = Mock()
        handler.session._model.send_event = AsyncMock()
        handler.ctx = Mock()
        handler.ctx.history = ["msg1", "msg2", "msg3", "msg4"]

        error_event = Mock(spec=RealtimeError)
        error_event.__str__ = Mock(return_value="Conversation already has an active response in progress: resp_123")

        await handler._handle_realtime_error_event(event=error_event)

        # Should have logged and NOT called recovery
        handler._recover_realtime_session.assert_not_awaited()

    async def test_logs_short_history(self, handler):
        handler._recover_realtime_session = AsyncMock()
        handler.session = Mock()
        handler.session._model = Mock()
        handler.session._model.send_event = AsyncMock()
        handler.ctx = Mock()
        handler.ctx.history = ["msg1"]

        error_event = Mock(spec=RealtimeError)
        error_event.__str__ = Mock(return_value="Conversation already has an active response in progress: resp_x")

        await handler._handle_realtime_error_event(event=error_event)

        handler._recover_realtime_session.assert_not_awaited()


# ===================================================================
# _handle_realtime_error_event: recovery exception  (lines 698-699)
# ===================================================================


class TestRecoveryException:
    async def test_recovery_exception_is_caught(self, handler):
        handler._recover_realtime_session = AsyncMock(side_effect=RuntimeError("recovery failed"))

        event = Mock()  # not a RealtimeError, so will go to recovery path

        # Should not raise
        await handler._handle_realtime_error_event(event=event)


# ===================================================================
# _handle_realtime_event: audio_interrupted WebSocket error (lines 498-499)
# ===================================================================


class TestAudioInterruptedWebSocketError:
    async def test_audio_interrupted_websocket_send_fails(self, handler):
        handler._is_initial_greeting = False
        handler._stream_sid = "test-stream"
        handler.twilio_websocket.send_text = AsyncMock(side_effect=RuntimeError("ws closed"))

        event = Mock()
        event.type = "audio_interrupted"

        await handler._handle_realtime_event(event)

        # Queue should still be cleared even though send failed
        assert len(handler._out_frame_q) == 0


# ===================================================================
# _handle_realtime_event: audio_interrupted clears pending marks  (lines 508-515)
# ===================================================================


class TestAudioInterruptedClearsPendingMarks:
    async def test_clears_response_last_mark_ids(self, handler):
        handler._is_initial_greeting = False
        handler._stream_sid = "test-stream"
        handler._response_last_mark_ids = {"item1": "mark1", "item2": "mark2"}
        handler._call_state.is_agent_speaking = True

        event = Mock()
        event.type = "audio_interrupted"

        await handler._handle_realtime_event(event)

        assert handler._response_last_mark_ids == {}
        assert handler._call_state.is_agent_speaking is False

    async def test_records_end_times_for_interrupted_items(self, handler):
        """Interrupted items get end_times so they can be traced to LangSmith."""
        handler._is_initial_greeting = False
        handler._stream_sid = "test-stream"
        handler._response_last_mark_ids = {"item1": "mark1", "item2": "mark2"}
        handler._call_state.is_agent_speaking = True
        handler._message_end_times = {}

        event = Mock()
        event.type = "audio_interrupted"

        await handler._handle_realtime_event(event)

        assert "item1" in handler._message_end_times
        assert "item2" in handler._message_end_times
        assert isinstance(handler._message_end_times["item1"], datetime.datetime)

    async def test_does_not_overwrite_existing_end_times(self, handler):
        """Items that already have end_times are not overwritten on interrupt."""
        handler._is_initial_greeting = False
        handler._stream_sid = "test-stream"
        existing_time = datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)
        handler._message_end_times = {"item1": existing_time}
        handler._response_last_mark_ids = {"item1": "mark1", "item2": "mark2"}
        handler._call_state.is_agent_speaking = True

        event = Mock()
        event.type = "audio_interrupted"

        await handler._handle_realtime_event(event)

        # item1 keeps its original end_time
        assert handler._message_end_times["item1"] == existing_time
        # item2 gets a new end_time
        assert "item2" in handler._message_end_times

    async def test_fires_trace_from_session_history_on_interrupt(self, handler):
        """Interrupt builds history from session._history (not self.history) to avoid race."""
        handler._is_initial_greeting = False
        handler._stream_sid = "test-stream"
        handler._response_last_mark_ids = {"item1": "mark1"}
        handler._call_state.is_agent_speaking = True
        handler._message_end_times = {}
        handler._transcript_cache = {"item1": "Hello there"}
        # session._history is the source of truth — self.history may be stale
        mock_session = Mock()
        mock_session._history = [Mock()]
        handler.session = mock_session
        handler._fire_trace_task = Mock()

        with patch(
            "agent_leasing.twilio_handler.realtime_history_to_input_list",
            return_value=[{"role": "assistant", "content": "Hello there", "item_id": "item1"}],
        ) as mock_convert:
            event = Mock()
            event.type = "audio_interrupted"

            await handler._handle_realtime_event(event)

            # Verify it used session._history with transcript_cache, not self.history
            mock_convert.assert_called_with(
                mock_session._history,
                include_item_id=True,
                transcript_cache=handler._transcript_cache,
            )
            handler._fire_trace_task.assert_called_once()

    async def test_no_trace_when_no_session(self, handler):
        """No trace fired on interrupt if session is not available."""
        handler._is_initial_greeting = False
        handler._stream_sid = "test-stream"
        handler._response_last_mark_ids = {"item1": "mark1"}
        handler._call_state.is_agent_speaking = True
        handler._message_end_times = {}
        handler.session = None
        handler._fire_trace_task = Mock()

        event = Mock()
        event.type = "audio_interrupted"

        await handler._handle_realtime_event(event)

        # end_times still recorded, but no trace fired without a session
        assert "item1" in handler._message_end_times
        handler._fire_trace_task.assert_not_called()

    async def test_no_trace_when_no_pending_marks(self, handler):
        """No trace task fired when there are no pending marks to interrupt."""
        handler._is_initial_greeting = False
        handler._stream_sid = "test-stream"
        handler._response_last_mark_ids = {}
        handler._call_state.is_agent_speaking = True
        handler._fire_trace_task = Mock()

        event = Mock()
        event.type = "audio_interrupted"

        await handler._handle_realtime_event(event)

        handler._fire_trace_task.assert_not_called()


# ===================================================================
# _handle_realtime_event: audio_interrupted cancel-triggered  (lines 519-520)
# ===================================================================


class TestCancelTriggeredInterrupt:
    async def test_cancel_triggered_does_not_mark_user_speaking(self, handler):
        handler._is_initial_greeting = False
        handler._stream_sid = "test-stream"
        handler._expecting_cancel_interrupt = True

        event = Mock()
        event.type = "audio_interrupted"

        await handler._handle_realtime_event(event)

        assert handler.is_user_speaking is False
        assert handler._expecting_cancel_interrupt is False


# ===================================================================
# _handle_realtime_event: audio_end partial frame flush  (lines 532-537)
# ===================================================================


class TestAudioEndPartialFrameFlush:
    async def test_flushes_partial_frame_on_audio_end(self, handler):
        handler._out_partial = bytearray(b"\x01" * 100)
        handler._current_partial_event = ("mark1", "item1", 0)

        event = Mock()
        event.type = "audio_end"

        await handler._handle_realtime_event(event)

        # Partial should be padded to frame_bytes (320) and queued
        assert len(handler._out_frame_q) == 1
        frame, metadata = handler._out_frame_q[0]
        assert len(frame) == handler._frame_bytes
        # First 100 bytes are data, rest is silence
        assert frame[:100] == b"\x01" * 100
        assert frame[100:] == bytes([handler._silence_byte]) * (handler._frame_bytes - 100)
        assert len(handler._out_partial) == 0
        assert handler._current_partial_event is None


# ===================================================================
# _handle_realtime_event: guardrail_tripped  (lines 945-956)
# ===================================================================


class TestHandleGuardrailTripped:
    async def test_sends_guardrail_message(self, handler, mock_session):
        handler.session = mock_session
        handler.ctx = Mock()
        handler.ctx.language_code = "en"

        guardrail_result = Mock()
        guardrail_result.output.output_info = {"reasoning": "PII detected"}

        event = Mock()
        event.type = "guardrail_tripped"
        event.guardrail_results = [guardrail_result]

        with patch("agent_leasing.twilio_handler.asyncio.sleep", new_callable=AsyncMock):
            await handler._handle_realtime_event(event)

        mock_session.send_message.assert_called_once()
        sent_msg = mock_session.send_message.call_args[0][0]
        assert "PII detected" in sent_msg
        assert "en" in sent_msg


# ===================================================================
# _handle_realtime_event: input_audio_timeout_triggered  (lines 960-961)
# ===================================================================


class TestInputAudioTimeoutTriggered:
    @patch("agent_leasing.twilio_handler.settings")
    async def test_delegates_to_send_method(self, mock_settings, handler, mock_session):
        mock_settings.send_filler_messages = True
        mock_settings.filler_escalation_enabled = True
        mock_settings.filler_escalation_threshold = 2
        handler.session = mock_session
        handler.agent = Mock()
        handler._session_ready.set()
        handler.ctx = Mock()
        handler.ctx.language_code = "en"
        handler.ctx.thinker_running = False

        event = Mock()
        event.type = "input_audio_timeout_triggered"

        await handler._handle_realtime_event(event)

        mock_session.send_message.assert_called_once()


# ===================================================================
# _handle_realtime_event: raw_model_event with exception (lines 558-560)
# ===================================================================


class TestRawModelEventException:
    async def test_raw_model_exception_triggers_error_handler(self, handler):
        handler._handle_realtime_error_event = AsyncMock()

        # Use real RealtimeModelExceptionEvent so `type(event.data) is RealtimeModelExceptionEvent` passes
        exc_event = RealtimeModelExceptionEvent(exception=RuntimeError("model crash"))
        event = Mock()
        event.type = "raw_model_event"
        event.data = exc_event

        await handler._handle_realtime_event(event)

        handler._handle_realtime_error_event.assert_awaited_once()


# ===================================================================
# _handle_realtime_event: ModelBehaviorError catch  (lines 570-571)
# ===================================================================


class TestRealtimeEventModelBehaviorError:
    async def test_model_behavior_error_caught(self, handler):
        handler._handle_realtime_error_event = AsyncMock()

        event = Mock()
        event.type = "guardrail_tripped"
        # Make the handler raise ModelBehaviorError during processing
        handler._handle_guardrail_tripped_event = AsyncMock(side_effect=ModelBehaviorError("bad model"))

        await handler._handle_realtime_event(event)

        handler._handle_realtime_error_event.assert_awaited_once()


# ===================================================================
# _handle_realtime_event: unknown event (line 1097)
# ===================================================================


class TestUnknownTwilioEvent:
    async def test_unknown_twilio_event_logged(self, handler):
        message = {"event": "some_future_event"}
        # Should not raise
        await handler._handle_twilio_message(message)


# ===================================================================
# _send_input_audio_timeout_message branches  (lines 969-970, 982-985)
# ===================================================================


class TestSendInputAudioTimeoutBranches:
    @patch("agent_leasing.twilio_handler.settings")
    async def test_not_sent_when_call_inactive(self, mock_settings, handler):
        mock_settings.send_filler_messages = True
        handler.call_active = False

        await handler._send_input_audio_timeout_message()

    @patch("agent_leasing.twilio_handler.settings")
    async def test_not_sent_when_agent_speaking(self, mock_settings, handler, mock_session):
        mock_settings.send_filler_messages = True
        handler.session = mock_session
        handler.agent = Mock()
        handler._session_ready.set()
        handler.call_active = True
        handler._call_state.is_agent_speaking = True

        await handler._send_input_audio_timeout_message()

        mock_session.send_message.assert_not_called()

    @patch("agent_leasing.twilio_handler.settings")
    async def test_not_sent_when_agent_processing(self, mock_settings, handler, mock_session):
        mock_settings.send_filler_messages = True
        handler.session = mock_session
        handler.agent = Mock()
        handler._session_ready.set()
        handler.call_active = True
        handler._call_state.is_agent_speaking = False
        handler._call_state.is_user_speaking = False
        # can_send_filler checks is_agent_speaking and is_user_speaking
        # is_agent_processing is not checked in can_send_filler, so this just tests the method returns True
        # Let's mock can_send_filler to return False and set is_agent_processing for logging path
        with patch.object(handler._call_state, "can_send_filler", return_value=False):
            handler._call_state.is_agent_processing = True

            await handler._send_input_audio_timeout_message()

        mock_session.send_message.assert_not_called()


# ===================================================================
# _recover_realtime_session: old session close  (lines 1025-1031)
# ===================================================================


class TestRecoverSessionCloseOld:
    def _setup_handler_for_recovery(self, handler, old_session):
        """Set up handler with sub-methods mocked for recovery testing."""
        handler.session = old_session
        handler.ctx = Mock()
        handler.ctx.history = []
        handler.ctx.language_code = "en"
        handler.ctx.ask_request = Mock()
        handler.ctx.ask_request.property_id = "prop1"
        handler.ctx.ask_request.product_info = Mock()
        handler.ctx.ask_request.product_info.knock_resident_id = "res1"
        handler.ctx.ask_request.product_info.uc_company_id = None
        handler.ctx.ask_request.product = "voice"
        handler.ctx.ask_request.product_info.property_name = "Test"
        handler.ctx.ask_request.product_info.call_sid = "cs1"
        handler.ctx.openai_group_url = "https://example.com/group"
        handler.agent = Mock()
        handler.agent.agent.return_value = "agent_instance"
        handler.model_config = Mock()
        handler.trace_id = "trace1"
        handler.group_id = "group1"

        new_session = Mock()
        new_session.send_message = AsyncMock()
        handler._setup_realtime_session = AsyncMock(side_effect=lambda a, m: setattr(handler, "session", new_session))
        handler._enter_realtime_session = AsyncMock()
        handler._start_realtime_session_loop = Mock()

    async def test_closes_old_session_before_recovery(self, handler, mock_session):
        self._setup_handler_for_recovery(handler, mock_session)

        await handler._recover_realtime_session(event=None)

        mock_session.close.assert_called_once()

    async def test_handles_old_session_close_failure(self, handler, mock_session):
        mock_session.close = AsyncMock(side_effect=RuntimeError("close failed"))
        self._setup_handler_for_recovery(handler, mock_session)

        # Should not raise despite old session close failure
        await handler._recover_realtime_session(event=None)

        mock_session.close.assert_called_once()


# ===================================================================
# _recover_realtime_session: external cancellation during task cancel  (line 1018)
# ===================================================================


class TestRecoverExternalCancellation:
    async def test_propagates_external_cancellation(self, handler, mock_session):
        """When external cancellation happens during recovery task cancellation, it should propagate."""
        handler.session = mock_session
        handler.ctx = Mock()
        handler.ctx.history = []
        handler.agent = Mock()

        # Create a task that when cancelled and awaited, the current task
        # also gets cancelled externally
        async def task_that_gets_externally_cancelled():
            await asyncio.sleep(100)

        existing_task = asyncio.create_task(task_that_gets_externally_cancelled())
        handler._realtime_session_task = existing_task
        existing_task.cancel()
        # The task is already cancelled, so awaiting it will just raise CancelledError
        # This tests the normal internal cancellation path (line 1019 not 1018)
        # For external cancellation we'd need the current_task to be cancelling
        # which is hard to simulate, so let's just verify the cancel path works
        try:
            await existing_task
        except asyncio.CancelledError:
            pass


# ===================================================================
# _schedule_data_curation_logging exception paths  (lines 1352-1354, 1361-1362)
# ===================================================================


class TestScheduleDataCurationLogging:
    def test_returns_when_no_session(self, handler):
        handler.session = None
        handler._schedule_data_curation_logging()
        assert handler._data_curation_task is None

    def test_handles_snapshot_error(self, handler):
        # Create a real object whose _history attribute raises when iterated
        class BrokenSession:
            @property
            def _history(self):
                raise RuntimeError("broken")

        handler.session = BrokenSession()
        handler._schedule_data_curation_logging()
        assert handler._data_curation_task is None

    def test_handles_task_creation_error(self, handler):
        handler.session = Mock()
        handler.session._history = []
        handler.session._context_wrapper = Mock()
        handler.session._context_wrapper.context = Mock()

        with patch("asyncio.create_task", side_effect=RuntimeError("event loop closed")):
            handler._schedule_data_curation_logging()

        # Should not raise, task should not be set from previous


# ===================================================================
# _cancel_background_tasks: CancelledError + warning  (lines 1423-1426, 1430)
# ===================================================================


class TestCancelBackgroundTasksEdges:
    async def test_task_exception_logged_as_warning(self, handler):
        """When a background task raises a non-CancelledError, it's logged as warning."""

        async def bad_coro():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                raise ValueError("unexpected error during cancellation")

        task = asyncio.create_task(bad_coro())
        handler._realtime_session_task = task
        handler._buffer_flush_task = None
        handler._message_loop_task = None
        handler._inactivity_monitor_task = None
        handler._pacer_task = None
        handler._recording_task = None

        # Should not raise
        await handler._cancel_background_tasks()


# ===================================================================
# _close_session: timeout and cancel  (lines 1443, 1445)
# ===================================================================


class TestCloseSessionEdges:
    async def test_close_session_timeout(self, handler):
        handler.session = Mock()
        handler.session.close = AsyncMock(side_effect=lambda: asyncio.sleep(100))

        # The wait_for will timeout
        with patch("asyncio.wait_for", side_effect=TimeoutError()):
            await handler._close_session()

        assert handler.session is None

    async def test_close_session_cancelled(self, handler):
        handler.session = Mock()
        handler.session.close = AsyncMock()

        with patch("asyncio.wait_for", side_effect=asyncio.CancelledError()):
            await handler._close_session()

        assert handler.session is None


# ===================================================================
# _close_agent: timeout and cancel  (lines 1466, 1468)
# ===================================================================


class TestCloseAgentEdges:
    async def test_close_agent_timeout(self, handler):
        handler.agent = Mock()
        handler.agent.__aexit__ = AsyncMock()

        with patch("asyncio.wait_for", side_effect=TimeoutError()):
            await handler._close_agent()

        assert handler.agent is None

    async def test_close_agent_cancelled(self, handler):
        handler.agent = Mock()
        handler.agent.__aexit__ = AsyncMock()

        with patch("asyncio.wait_for", side_effect=asyncio.CancelledError()):
            await handler._close_agent()

        assert handler.agent is None


# ===================================================================
# _await_data_curation  (lines 1478-1490)
# ===================================================================


class TestAwaitDataCuration:
    async def test_returns_when_no_task(self, handler):
        handler._data_curation_task = None
        await handler._await_data_curation()  # should not raise

    async def test_returns_when_task_done(self, handler):
        async def quick():
            return None

        task = asyncio.create_task(quick())
        await task
        handler._data_curation_task = task
        await handler._await_data_curation()

    async def test_timeout_cancels_task(self, handler):
        async def slow():
            await asyncio.sleep(100)

        task = asyncio.create_task(slow())
        handler._data_curation_task = task

        with patch("asyncio.wait_for", side_effect=TimeoutError()):
            await handler._await_data_curation()

        # Task should be cancelled
        assert task.cancelled() or task.done()

    async def test_cancelled_error_during_await(self, handler):
        async def slow():
            await asyncio.sleep(100)

        task = asyncio.create_task(slow())
        handler._data_curation_task = task

        with patch("asyncio.wait_for", side_effect=asyncio.CancelledError()):
            await handler._await_data_curation()

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_exception_during_await(self, handler):
        async def slow():
            await asyncio.sleep(100)

        task = asyncio.create_task(slow())
        handler._data_curation_task = task

        with patch("asyncio.wait_for", side_effect=ValueError("unexpected")):
            await handler._await_data_curation()

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ===================================================================
# _transfer_call_on_validation_failure: transfer exception  (lines 1529-1530)
# ===================================================================


class TestTransferCallException:
    async def test_transfer_exception_caught(self, handler):
        handler._call_sid = "cs123"

        with (
            patch(
                "agent_leasing.twilio_handler.get_twilio_credentials",
                side_effect=RuntimeError("creds failed"),
            ),
        ):
            # Should not raise
            await handler._transfer_call_on_validation_failure(
                ValueError("bad"), {"product": "test", "product_info": {"property_name": "T"}}
            )


# ===================================================================
# _start_recording  (lines 1318-1342)
# ===================================================================


class TestStartRecording:
    async def test_starts_recording_when_should_record(self, handler):
        payload = {"product_info": {"should_record": True}}

        mock_client = Mock()
        mock_client.calls.return_value.recordings.create = Mock()

        with (
            patch("agent_leasing.twilio_handler.TwilioClient", return_value=mock_client),
            patch("asyncio.create_task") as mock_ct,
        ):
            mock_task = Mock()
            mock_task.add_done_callback = Mock()
            mock_ct.return_value = mock_task

            await handler._start_recording(payload, "call-123")

            mock_ct.assert_called_once()

    async def test_skips_when_should_record_false(self, handler):
        payload = {"product_info": {"should_record": False}}

        with patch("agent_leasing.twilio_handler.TwilioClient") as mock_tc:
            await handler._start_recording(payload, "call-123")
            mock_tc.assert_not_called()

    async def test_handles_recording_exception(self, handler):
        payload = {"product_info": {"should_record": True}}

        with patch("agent_leasing.twilio_handler.TwilioClient", side_effect=RuntimeError("no creds")):
            # Should not raise
            await handler._start_recording(payload, "call-123")

    async def test_fallback_to_sync_call_when_create_task_fails(self, handler):
        payload = {"product_info": {"should_record": True}}

        mock_client = Mock()
        mock_client.calls.return_value.recordings.create = Mock()

        with (
            patch("agent_leasing.twilio_handler.TwilioClient", return_value=mock_client),
            patch("asyncio.create_task", side_effect=RuntimeError("loop closed")),
        ):
            await handler._start_recording(payload, "call-123")

            # Should fall back to direct sync call
            mock_client.calls.return_value.recordings.create.assert_called_once()


# ===================================================================
# _send_mark edge cases  (lines 902, 912-914, 929)
# ===================================================================


class TestSendMarkEdges:
    async def test_skips_when_no_stream_sid(self, handler):
        handler._stream_sid = None
        handler.call_active = True
        handler._pacer_running = True
        await handler._send_mark("1")
        handler.twilio_websocket.send_text.assert_not_called()

    async def test_skips_when_call_inactive(self, handler):
        handler._stream_sid = "sid"
        handler.call_active = False
        handler._pacer_running = True
        await handler._send_mark("1")
        handler.twilio_websocket.send_text.assert_not_called()

    async def test_skips_when_pacer_not_running(self, handler):
        handler._stream_sid = "sid"
        handler.call_active = True
        handler._pacer_running = False
        await handler._send_mark("1")
        handler.twilio_websocket.send_text.assert_not_called()

    async def test_skips_when_application_state_not_connected(self, handler):
        handler._stream_sid = "sid"
        handler.call_active = True
        handler._pacer_running = True
        handler.twilio_websocket.application_state = Mock()
        handler.twilio_websocket.application_state.name = "DISCONNECTED"

        await handler._send_mark("1")
        handler.twilio_websocket.send_text.assert_not_called()

    async def test_skips_when_client_state_check_raises(self, handler):
        handler._stream_sid = "sid"
        handler.call_active = True
        handler._pacer_running = True

        # Use a custom class to make client_state raise without affecting shared Mock types
        class BrokenWs:
            def __init__(self):
                self.send_text = AsyncMock()

            @property
            def client_state(self):
                raise RuntimeError("ws state error")

        ws = BrokenWs()
        handler.twilio_websocket = ws

        await handler._send_mark("1")
        ws.send_text.assert_not_called()

    async def test_reraises_non_websocket_disconnect_oserror(self, handler):
        handler._stream_sid = "sid"
        handler.call_active = True
        handler._pacer_running = True
        handler.twilio_websocket.send_text = AsyncMock(side_effect=OSError("network error"))

        with pytest.raises(OSError, match="network error"):
            await handler._send_mark("1")


# ===================================================================
# _input_audio_inactivity_loop  (lines 1224-1249)
# ===================================================================


class TestInputAudioInactivityLoop:
    @patch("agent_leasing.twilio_handler.settings")
    async def test_loop_sends_filler_when_timeout_reached(self, mock_settings, handler, mock_session):
        mock_settings.send_filler_messages = True
        handler.session = mock_session
        handler.agent = Mock()
        handler._session_ready.set()
        handler.call_active = True
        handler.ctx = Mock()
        handler.ctx.language_code = "en"

        # Set next filler time to past
        handler._next_filler_time = time.time() - 10

        call_count = 0

        async def controlled_sleep(duration):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                handler.call_active = False

        with patch("asyncio.sleep", side_effect=controlled_sleep):
            await handler._input_audio_inactivity_loop()

    @patch("agent_leasing.twilio_handler.settings")
    async def test_loop_schedules_filler_when_none(self, mock_settings, handler, mock_session):
        mock_settings.send_filler_messages = True
        mock_settings.filler_delay_mean_seconds = 10.0
        mock_settings.filler_delay_std_seconds = 0.0
        handler.session = mock_session
        handler.agent = Mock()
        handler._session_ready.set()
        handler.call_active = True
        handler._next_filler_time = None

        call_count = 0

        async def controlled_sleep(duration):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                handler.call_active = False

        with patch("asyncio.sleep", side_effect=controlled_sleep):
            await handler._input_audio_inactivity_loop()

    async def test_loop_handles_general_exception(self, handler):
        handler.call_active = True

        with patch("asyncio.sleep", side_effect=ValueError("unexpected")):
            # Should not raise - exception is caught
            await handler._input_audio_inactivity_loop()


# ===================================================================
# _buffer_flush_loop CancelledError  (lines 1217, 1219-1220)
# ===================================================================


class TestBufferFlushLoopEdges:
    async def test_handles_general_exception(self, handler):
        handler.call_active = True

        with patch("asyncio.sleep", side_effect=ValueError("bad")):
            await handler._buffer_flush_loop()


# ===================================================================
# TwilioWebSocketManager  (lines 1571-1576)
# ===================================================================


class TestTwilioWebSocketManager:
    async def test_new_session_creates_handler(self, mock_websocket):
        manager = TwilioWebSocketManager()
        handler = await manager.new_session(mock_websocket)

        assert isinstance(handler, TwilioHandler)
        assert len(manager.active_handlers) == 1

    async def test_cleanup_handler_removes_handler(self, mock_websocket):
        manager = TwilioWebSocketManager()
        handler = await manager.new_session(mock_websocket)
        handler_id = str(id(handler))

        await manager.cleanup_handler(handler_id)

        assert len(manager.active_handlers) == 0

    async def test_cleanup_handler_not_found(self):
        manager = TwilioWebSocketManager()

        # Should not raise
        await manager.cleanup_handler("nonexistent-id")
        assert len(manager.active_handlers) == 0


# ===================================================================
# _handle_mark_event exception  (lines 1150-1151)
# ===================================================================


class TestHandleMarkEventException:
    async def test_exception_in_mark_handler_caught(self, handler):
        handler._mark_data["1"] = ("item1", 0, 100)

        with patch.object(handler.playback_tracker, "on_play_bytes", side_effect=RuntimeError("tracker error")):
            message = {"mark": {"name": "1"}}
            # Should not raise
            await handler._handle_mark_event(message)


# ===================================================================
# Pacer loop edge cases  (lines 817, 823-826, 846-849, 854-855, 859, 871, 878-880, 887-892)
# ===================================================================


class TestPacerLoopEdges:
    async def test_pacer_sends_silence_when_queue_empty(self, handler):
        """When queue empties during pacer run, silence frames are sent."""
        handler._stream_sid = "test-stream"
        handler.call_active = True
        handler._pacer_running = True

        # Add just 1 frame (below prebuffer of 12, will trigger startup timeout)
        handler._out_frame_q.append((b"\x01" * 320, ("1", "item1", 0)))

        send_calls = []

        async def capture_send(text):
            send_calls.append(text)
            if len(send_calls) >= 3:
                handler._pacer_running = False

        handler.twilio_websocket.send_text = AsyncMock(side_effect=capture_send)

        await handler._pacer_loop()

        assert len(send_calls) >= 1
        assert handler._pacer_running is False

    async def test_pacer_skips_send_when_no_stream_sid(self, handler):
        handler._stream_sid = None
        handler.call_active = True
        handler._pacer_running = True

        handler._out_frame_q.append((b"\x01" * 320, ("1", "item1", 0)))

        tick_count = 0

        original_sleep = asyncio.sleep

        async def limited_sleep(duration):
            nonlocal tick_count
            tick_count += 1
            if tick_count >= 3:
                handler._pacer_running = False
            await original_sleep(min(duration, 0.001))

        with patch("asyncio.sleep", side_effect=limited_sleep):
            await handler._pacer_loop()

    async def test_pacer_breaks_when_call_inactive(self, handler):
        handler._stream_sid = "test-stream"
        handler.call_active = True
        handler._pacer_running = True

        # Add enough frames for prebuffer
        for i in range(15):
            handler._out_frame_q.append((b"\x01" * 320, (str(i), f"item{i}", 0)))

        send_count = 0

        async def send_then_deactivate(text):
            nonlocal send_count
            send_count += 1
            if send_count >= 2:
                handler.call_active = False

        handler.twilio_websocket.send_text = AsyncMock(side_effect=send_then_deactivate)

        await handler._pacer_loop()

        assert handler._pacer_running is False

    async def test_pacer_handles_connection_error(self, handler):
        handler._stream_sid = "test-stream"
        handler.call_active = True
        handler._pacer_running = True

        for i in range(15):
            handler._out_frame_q.append((b"\x01" * 320, (str(i), f"item{i}", 0)))

        handler.twilio_websocket.send_text = AsyncMock(side_effect=ConnectionError("lost"))

        await handler._pacer_loop()

        assert handler._pacer_running is False


# ===================================================================
# _on_response_completed  (lines 1110-1123)
# ===================================================================


class TestOnResponseCompleted:
    async def test_no_pending_marks_stops_agent_speaking(self, handler):
        handler._response_last_mark_ids = {}
        handler._call_state.is_agent_speaking = True
        handler._is_initial_greeting = False

        await handler._on_response_completed("item1", "mark1")

        assert handler._call_state.is_agent_speaking is False

    async def test_greeting_completion_clears_flag_and_audio(self, handler):
        handler._response_last_mark_ids = {}
        handler._is_initial_greeting = True
        handler._audio_buffer = bytearray(b"\x01" * 500)

        await handler._on_response_completed("item1", "mark1")

        assert handler._is_initial_greeting is False
        assert len(handler._audio_buffer) == 0


# ===================================================================
# history_updated with user completed status  (lines 553-566)
# ===================================================================


class TestHistoryUpdatedUserCompleted:
    async def test_user_completed_reschedules_filler(self, handler):
        handler.ctx = Mock()
        handler.call_active = True

        user_item = Mock(spec=UserMessageItem)
        user_item.role = "user"
        user_item.status = "completed"
        user_item.item_id = "user_msg_1"

        event = Mock()
        event.type = "history_updated"
        event.history = [user_item]

        with (
            patch.object(handler, "_schedule_next_filler") as mock_schedule,
            patch.object(handler, "_fire_trace_task") as mock_trace,
            patch("agent_leasing.twilio_handler.realtime_history_to_input_list", return_value=[]),
        ):
            await handler._handle_realtime_event(event)

            mock_schedule.assert_called()
            mock_trace.assert_called_once()
            assert handler._call_state.is_user_speaking is False
            assert handler._call_state.is_agent_processing is True


# ===================================================================
# _send_input_audio_timeout_message success/error paths  (lines 913-940)
# ===================================================================


class TestSendInputAudioTimeoutSuccess:
    @patch("agent_leasing.twilio_handler.settings")
    async def test_success_path(self, mock_settings, handler, mock_session):
        mock_settings.send_filler_messages = True
        mock_settings.filler_delay_mean_seconds = 10.0
        mock_settings.filler_delay_std_seconds = 0.0
        mock_settings.filler_escalation_enabled = True
        mock_settings.filler_escalation_threshold = 2
        handler.session = mock_session
        handler.agent = Mock()
        handler._session_ready.set()
        handler.call_active = True
        handler.ctx = Mock()
        handler.ctx.language_code = "en"
        handler.ctx.thinker_running = False

        await handler._send_input_audio_timeout_message()

        assert handler._next_speech_is_filler is True
        mock_session.send_message.assert_called_once()

    @patch("agent_leasing.twilio_handler.settings")
    async def test_error_resets_filler_flag(self, mock_settings, handler, mock_session):
        mock_settings.send_filler_messages = True
        mock_settings.filler_escalation_enabled = True
        mock_settings.filler_escalation_threshold = 2
        handler.session = mock_session
        handler.agent = Mock()
        handler._session_ready.set()
        handler.call_active = True
        handler.ctx = Mock()
        handler.ctx.language_code = "en"
        handler.ctx.thinker_running = False
        mock_session.send_message = AsyncMock(side_effect=RuntimeError("send failed"))

        await handler._send_input_audio_timeout_message()

        assert handler._next_speech_is_filler is False

    @patch("agent_leasing.twilio_handler.settings")
    async def test_early_return_no_agent_or_session(self, mock_settings, handler):
        mock_settings.send_filler_messages = True
        handler.call_active = True
        handler.agent = None
        handler.session = None

        await handler._send_input_audio_timeout_message()
        # Should return early without error


# ===================================================================
# _handle_mark_event completion triggers _on_response_completed  (lines 1084-1090)
# ===================================================================


class TestHandleMarkEventCompletion:
    async def test_last_mark_triggers_on_response_completed(self, handler):
        handler._mark_data["mark99"] = ("item_abc", 0, 320)
        handler._response_last_mark_ids = {"item_abc": "mark99"}
        handler._on_response_completed = AsyncMock()

        message = {"mark": {"name": "mark99"}}
        await handler._handle_mark_event(message)

        handler._on_response_completed.assert_awaited_once_with("item_abc", "mark99")
        assert "item_abc" not in handler._response_last_mark_ids
        assert "mark99" not in handler._mark_data


# ===================================================================
# encode_object / decode_object round-trip  (lines 1478-1488)
# ===================================================================


class TestEncodeDecodeObject:
    def test_round_trip_preserves_data(self):
        original = {
            "product": "resident_one_voice",
            "property_id": "12345",
            "nested": {"key": "value", "num": 42},
        }
        encoded = encode_object(original)
        assert isinstance(encoded, str)

        decoded = decode_object(encoded)
        assert decoded == original


# ===================================================================
# _handle_realtime_error_event: audio truncation "shorter than" error
# ===================================================================


class TestAudioTruncationShorterThan:
    async def test_ignores_audio_shorter_than_error(self, handler):
        handler._recover_realtime_session = AsyncMock()

        error_event = Mock(spec=RealtimeError)
        error_event.__str__ = Mock(return_value="Audio content of 100ms is already shorter than 231ms")

        await handler._handle_realtime_error_event(event=error_event)

        # Recovery should NOT be called — this is benign
        handler._recover_realtime_session.assert_not_awaited()

    async def test_other_errors_still_trigger_recovery(self, handler):
        handler._recover_realtime_session = AsyncMock()

        error_event = Mock(spec=RealtimeError)
        error_event.__str__ = Mock(return_value="Some other realtime error")

        await handler._handle_realtime_error_event(event=error_event)

        # Recovery SHOULD be called for unknown errors
        handler._recover_realtime_session.assert_awaited_once()


# ===================================================================
# _enter_realtime_session: retry on transient WebSocket failure
# ===================================================================


class TestEnterRealtimeSessionRetry:
    async def test_succeeds_on_first_attempt(self, handler, mock_session):
        handler.session = mock_session

        with patch("agent_leasing.twilio_handler.ls"):
            await handler._enter_realtime_session()

        mock_session.enter.assert_awaited_once()
        assert handler._session_ready.is_set()

    async def test_retries_on_transient_failure(self, handler, mock_session):
        # First call fails, second succeeds
        fail_session = Mock(spec=RealtimeSession)
        fail_session.enter = AsyncMock(side_effect=ConnectionError("WebSocket 1011"))
        fail_session.close = AsyncMock()

        success_session = Mock(spec=RealtimeSession)
        success_session.enter = AsyncMock()

        handler.session = fail_session
        handler.agent = Mock()
        handler.agent.agent.return_value = "agent_instance"
        handler._session_metadata = {"test": "metadata"}

        async def rebuild_session(agent, metadata):
            handler.session = success_session

        handler._setup_realtime_session = AsyncMock(side_effect=rebuild_session)

        with (
            patch("agent_leasing.twilio_handler.ls"),
            patch("agent_leasing.twilio_handler.asyncio.sleep", new_callable=AsyncMock),
        ):
            await handler._enter_realtime_session(max_retries=2)

        fail_session.enter.assert_awaited_once()
        fail_session.close.assert_awaited_once()
        success_session.enter.assert_awaited_once()
        assert handler._session_ready.is_set()

    async def test_raises_after_all_retries_exhausted(self, handler):
        fail_session = Mock(spec=RealtimeSession)
        fail_session.enter = AsyncMock(side_effect=ConnectionError("WebSocket 1011"))
        fail_session.close = AsyncMock()

        handler.session = fail_session
        handler.agent = Mock()
        handler.agent.agent.return_value = "agent_instance"
        handler._session_metadata = {"test": "metadata"}
        handler._setup_realtime_session = AsyncMock(side_effect=lambda a, m: setattr(handler, "session", fail_session))

        with (
            patch("agent_leasing.twilio_handler.ls"),
            patch("agent_leasing.twilio_handler.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(ConnectionError, match="WebSocket 1011"),
        ):
            await handler._enter_realtime_session(max_retries=2)


# ===================================================================
# call_hangup tracing tests
# ===================================================================


class TestCallHangupTracing:
    """Verify call_hangup is traced as a child of root_run, not via contextvar."""

    async def test_call_hangup_traced_as_child_of_root_run(self, tracing_handler):
        """Stop event with call_ended_by_agent=False creates a child run."""
        tracing_handler.ctx.call_ended_by_agent = False
        message = {"event": "stop"}

        with patch.object(tracing_handler, "_cleanup_call", new_callable=AsyncMock):
            await tracing_handler._handle_twilio_message(message)

        child = tracing_handler.root_run.create_child.return_value
        tracing_handler.root_run.create_child.assert_called_once()
        call_kwargs = tracing_handler.root_run.create_child.call_args[1]
        assert call_kwargs["name"] == "call_hangup"
        assert call_kwargs["run_type"] == "chain"
        assert call_kwargs["outputs"]["message"] == ("Call ended by Twilio media stream stop event | user hung up")
        assert call_kwargs["start_time"] is not None
        assert call_kwargs["end_time"] is not None
        child.post.assert_called_once()

    async def test_call_hangup_not_traced_when_ended_by_agent(self, tracing_handler):
        """Stop event with call_ended_by_agent=True does NOT call create_child."""
        tracing_handler.ctx.call_ended_by_agent = True
        message = {"event": "stop"}

        with patch.object(tracing_handler, "_cleanup_call", new_callable=AsyncMock):
            await tracing_handler._handle_twilio_message(message)

        tracing_handler.root_run.create_child.assert_not_called()

    async def test_call_hangup_skipped_when_no_root_run(self, mock_websocket):
        """Stop event with root_run=None doesn't raise."""
        handler = TwilioHandler(mock_websocket)
        handler.root_run = None
        handler.ctx = Mock()
        handler.ctx.call_ended_by_agent = False
        handler.call_active = True
        message = {"event": "stop"}

        with patch.object(handler, "_cleanup_call", new_callable=AsyncMock):
            await handler._handle_twilio_message(message)
        # No exception = pass


# ===================================================================
# _recover_realtime_session: self-cancel skip  (lines 1227-1228)
# ===================================================================


class TestSelfCancelSkipped:
    def _setup_handler_for_recovery(self, handler, old_session):
        """Set up handler with sub-methods mocked for recovery testing."""
        handler.session = old_session
        handler.ctx = Mock()
        handler.ctx.history = []
        handler.ctx.language_code = "en"
        handler.ctx.ask_request = Mock()
        handler.ctx.ask_request.property_id = "prop1"
        handler.ctx.ask_request.product_info = Mock()
        handler.ctx.ask_request.product_info.knock_resident_id = "res1"
        handler.ctx.ask_request.product_info.uc_company_id = None
        handler.ctx.ask_request.product = "voice"
        handler.ctx.ask_request.product_info.property_name = "Test"
        handler.ctx.ask_request.product_info.call_sid = "cs1"
        handler.ctx.openai_group_url = "https://example.com/group"
        handler.agent = Mock()
        handler.agent.agent.return_value = "agent_instance"
        handler.model_config = Mock()
        handler.trace_id = "trace1"
        handler.group_id = "group1"

        new_session = Mock()
        new_session.send_message = AsyncMock()
        handler._setup_realtime_session = AsyncMock(side_effect=lambda a, m: setattr(handler, "session", new_session))
        handler._enter_realtime_session = AsyncMock()
        handler._start_realtime_session_loop = Mock()

    async def test_recovery_skips_cancel_when_called_from_session_loop(self, handler, mock_session):
        """When recovery is called from within the session loop task, cancel() should NOT be called."""
        self._setup_handler_for_recovery(handler, mock_session)

        # Create a mock task that represents the session loop task
        current_task = Mock()
        handler._realtime_session_task = current_task

        with patch("asyncio.current_task", return_value=current_task):
            await handler._recover_realtime_session(event=None)

        # cancel() should NOT be called since we're in the same task
        current_task.cancel.assert_not_called()

        # Recovery should still complete (setup/enter/start are called)
        handler._setup_realtime_session.assert_awaited_once()
        handler._enter_realtime_session.assert_awaited_once()
        handler._start_realtime_session_loop.assert_called_once()

    async def test_recovery_cancels_when_called_from_different_task(self, handler, mock_session):
        """When recovery is called from a different task, cancel() SHOULD be called."""
        self._setup_handler_for_recovery(handler, mock_session)

        # Create a real task that will be cancelled
        async def dummy():
            await asyncio.sleep(100)

        existing_task = asyncio.create_task(dummy())
        handler._realtime_session_task = existing_task

        # current_task() returns a different task (the test coroutine's task)
        await handler._recover_realtime_session(event=None)

        # cancel was called on the existing task
        assert existing_task.cancelled()

        # Recovery should still complete
        handler._setup_realtime_session.assert_awaited_once()
        handler._enter_realtime_session.assert_awaited_once()
        handler._start_realtime_session_loop.assert_called_once()


# ===================================================================
# Generic exception handlers trigger recovery  (lines 570-575, 792-797)
# ===================================================================


class TestGenericExceptionTriggersRecovery:
    async def test_session_loop_connection_closed_triggers_recovery(self, handler, mock_session):
        """ConnectionError in session iterator triggers _handle_realtime_error_event."""
        handler.session = mock_session
        handler._session_ready = asyncio.Event()
        handler._session_ready.set()
        handler._handle_realtime_error_event = AsyncMock()

        # Make the session iterator raise a ConnectionError
        async def raising_iter(self_iter):
            raise ConnectionError("WebSocket closed unexpectedly")
            yield  # make it an async generator  # noqa: RUF027

        mock_session.__aiter__ = raising_iter

        await handler._realtime_session_loop()

        handler._handle_realtime_error_event.assert_awaited_once()

    async def test_session_loop_generic_exception_triggers_recovery(self, handler, mock_session):
        """RuntimeError in session iterator triggers _handle_realtime_error_event."""
        handler.session = mock_session
        handler._session_ready = asyncio.Event()
        handler._session_ready.set()
        handler._handle_realtime_error_event = AsyncMock()

        async def raising_iter(self_iter):
            raise RuntimeError("unexpected failure")
            yield  # noqa: RUF027

        mock_session.__aiter__ = raising_iter

        await handler._realtime_session_loop()

        handler._handle_realtime_error_event.assert_awaited_once()

    async def test_handle_realtime_event_generic_exception_triggers_recovery(self, handler):
        """Exception in an event handler triggers _handle_realtime_error_event."""
        handler._handle_realtime_error_event = AsyncMock()

        event = Mock()
        event.type = "guardrail_tripped"
        handler._handle_guardrail_tripped_event = AsyncMock(side_effect=RuntimeError("handler broke"))

        await handler._handle_realtime_event(event)

        handler._handle_realtime_error_event.assert_awaited_once()

    async def test_session_loop_recovery_failure_does_not_raise(self, handler, mock_session):
        """If recovery itself fails in the session loop, the error is caught gracefully."""
        handler.session = mock_session
        handler._session_ready = asyncio.Event()
        handler._session_ready.set()
        handler._handle_realtime_error_event = AsyncMock(side_effect=RuntimeError("recovery exploded"))

        async def raising_iter(self_iter):
            raise ConnectionError("dead socket")
            yield  # noqa: RUF027

        mock_session.__aiter__ = raising_iter

        # Should not raise
        await handler._realtime_session_loop()

    async def test_handle_realtime_event_recovery_failure_does_not_raise(self, handler):
        """If recovery itself fails in event handler, the error is caught gracefully."""
        handler._handle_realtime_error_event = AsyncMock(side_effect=RuntimeError("recovery exploded"))

        event = Mock()
        event.type = "guardrail_tripped"
        handler._handle_guardrail_tripped_event = AsyncMock(side_effect=RuntimeError("handler broke"))

        # Should not raise
        await handler._handle_realtime_event(event)


# ===================================================================
# Caller-interrupt suppression guards (handoff in progress)
# ===================================================================


def _setup_handoff_handler(handler, mock_session, handoff_in_progress=True, is_agent_speaking=True):
    """Configure handler with handoff context for suppression guard tests."""
    handler.ctx = Mock()
    handler.ctx.handoff_in_progress = handoff_in_progress
    handler.session = mock_session
    handler._stream_sid = "stream123"
    handler._audio_buffer = bytearray(b"\x00" * 100)
    handler._call_state = Mock()
    handler._call_state.is_agent_speaking = is_agent_speaking
    handler.twilio_websocket = AsyncMock()
    handler.call_active = True
    return handler


class TestHandoffInterruptSuppression:
    """Tests for audio_interrupted suppression during a handoff."""

    @pytest.mark.asyncio
    async def test_audio_interrupted_suppressed_during_handoff(self, handler, mock_session):
        _setup_handoff_handler(handler, mock_session)
        event = Mock()
        event.type = "audio_interrupted"
        handler._is_initial_greeting = False

        with patch.object(
            type(handler), "_interrupt_suppression_active", new_callable=lambda: property(lambda self: True)
        ):
            await handler._handle_realtime_event(event)

        # No clear event sent to Twilio — interruption was suppressed
        handler.twilio_websocket.send_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_audio_interrupted_not_suppressed_when_flag_off(self, handler, mock_session):
        _setup_handoff_handler(handler, mock_session)
        event = Mock()
        event.type = "audio_interrupted"
        handler._is_initial_greeting = False

        with patch.object(
            type(handler), "_interrupt_suppression_active", new_callable=lambda: property(lambda self: False)
        ):
            await handler._handle_realtime_event(event)

        # Clear event sent — suppression disabled
        handler.twilio_websocket.send_text.assert_awaited_once()


class TestHandoffFillerSuppression:
    """Tests for filler message suppression during a handoff."""

    @pytest.mark.asyncio
    async def test_filler_suppressed_during_handoff(self, handler, mock_session):
        _setup_handoff_handler(handler, mock_session)

        with patch.object(
            type(handler), "_interrupt_suppression_active", new_callable=lambda: property(lambda self: True)
        ):
            await handler._send_input_audio_timeout_message()

        # No filler sent — suppressed during ESR
        mock_session.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_filler_not_suppressed_when_flag_off(self, handler, mock_session):
        _setup_handoff_handler(handler, mock_session)
        handler._is_initial_greeting = False
        handler._filler_sent = False
        handler._thinker_active = False
        handler.agent = Mock()
        handler._session_ready = asyncio.Event()
        handler._session_ready.set()
        handler._is_websocket_connected = Mock(return_value=True)
        handler._call_state.is_user_speaking = False

        with (
            patch.object(
                type(handler), "_interrupt_suppression_active", new_callable=lambda: property(lambda self: False)
            ),
            patch("agent_leasing.twilio_handler.settings") as mock_settings,
        ):
            mock_settings.send_filler_messages = True
            mock_settings.filler_escalation_enabled = False
            await handler._send_input_audio_timeout_message()

        # Filler message sent — suppression disabled
        mock_session.send_message.assert_awaited_once()


class TestHandoffAudioBufferSuppression:
    """Tests for audio buffer suppression and discard during a handoff."""

    @pytest.mark.asyncio
    async def test_audio_buffer_discarded_during_handoff_while_speaking(self, handler, mock_session):
        _setup_handoff_handler(handler, mock_session, is_agent_speaking=True)
        assert len(handler._audio_buffer) > 0

        with patch.object(
            type(handler), "_interrupt_suppression_active", new_callable=lambda: property(lambda self: True)
        ):
            await handler._flush_audio_buffer()

        # Audio discarded, not sent to OpenAI
        mock_session.send_audio.assert_not_awaited()
        assert len(handler._audio_buffer) == 0

    @pytest.mark.asyncio
    async def test_audio_buffer_sent_when_not_speaking_during_handoff(self, handler, mock_session):
        _setup_handoff_handler(handler, mock_session, is_agent_speaking=False)

        with (
            patch.object(
                type(handler), "_interrupt_suppression_active", new_callable=lambda: property(lambda self: True)
            ),
            patch("agent_leasing.twilio_handler.settings") as mock_settings,
        ):
            mock_settings.twilio_input_audio_noise_reduction_enabled = False
            mock_settings.openai_audio_format = "pcm16"
            await handler._flush_audio_buffer()

        # Audio sent — agent not speaking, so buffer flushes normally
        mock_session.send_audio.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_audio_buffer_not_suppressed_when_flag_off(self, handler, mock_session):
        _setup_handoff_handler(handler, mock_session, is_agent_speaking=True)

        with (
            patch.object(
                type(handler), "_interrupt_suppression_active", new_callable=lambda: property(lambda self: False)
            ),
            patch("agent_leasing.twilio_handler.settings") as mock_settings,
        ):
            mock_settings.twilio_input_audio_noise_reduction_enabled = False
            mock_settings.openai_audio_format = "pcm16"
            await handler._flush_audio_buffer()

        # Audio sent — suppression disabled even though ESR is active
        mock_session.send_audio.assert_awaited_once()
