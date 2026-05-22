"""Tests for TwilioHandler."""

import asyncio
import base64
import copy
import time
from unittest.mock import AsyncMock, Mock, patch

import orjson
import pytest
from agents import ModelBehaviorError
from agents.realtime import (
    InputAudio,
    RealtimeError,
    RealtimeModelSendInterrupt,
    RealtimeModelSendRawMessage,
    RealtimePlaybackTracker,
    RealtimeSession,
    UserMessageItem,
)
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from agent_leasing.api.model import AskRequest, examples
from agent_leasing.settings import settings
from agent_leasing.twilio_handler import TwilioHandler


class _AsyncIterator:
    """Utility async iterator for mocking RealtimeSession.__aiter__."""

    def __init__(self, items=None, exception=None):
        self._items = list(items or [])
        self._exception = exception

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._items:
            return self._items.pop(0)
        if self._exception is not None:
            exception = self._exception
            self._exception = None
            raise exception
        raise StopAsyncIteration


@pytest.fixture
def mock_websocket():
    """Mock WebSocket for testing."""
    websocket = Mock(spec=WebSocket)
    websocket.accept = AsyncMock()
    websocket.send_text = AsyncMock()
    websocket.receive_text = AsyncMock()
    return websocket


@pytest.fixture
def twilio_handler(mock_websocket):
    """Create TwilioHandler instance for testing."""
    return TwilioHandler(mock_websocket)


@pytest.fixture
def mock_realtime_session():
    """Mock RealtimeSession for testing."""
    session = Mock(spec=RealtimeSession)
    session.enter = AsyncMock()
    session.send_audio = AsyncMock()
    session.send_message = AsyncMock()
    session.__aiter__ = Mock(return_value=_AsyncIterator())
    session._history = []
    return session


class TestTwilioHandlerInit:
    """Test TwilioHandler initialization."""

    def test_init(self, mock_websocket):
        """Test TwilioHandler initialization."""
        handler = TwilioHandler(mock_websocket)

        assert handler.twilio_websocket == mock_websocket
        assert handler._message_loop_task is None
        assert handler.session is None
        assert isinstance(handler.playback_tracker, RealtimePlaybackTracker)

        # Test audio configuration
        assert handler.CHUNK_LENGTH_S == 0.05
        assert handler.SAMPLE_RATE == 8000
        assert handler.BUFFER_SIZE_BYTES == int(8000 * 0.05)

        # Test pacer configuration
        assert handler._frame_bytes == 160
        assert handler._tick_seconds == 0.020
        assert handler._prebuffer_frames == 6
        assert handler._pacer_startup_timeout_sec == 0.120

        # Test initial state
        assert handler._stream_sid is None
        assert isinstance(handler._audio_buffer, bytearray)
        assert len(handler._audio_buffer) == 0
        assert handler._mark_counter == 0
        assert handler._mark_data == {}
        assert handler._pacer_running is False
        assert len(handler._out_frame_q) == 0

        # Test initial greeting control state
        assert handler._is_initial_greeting is False
        assert handler._response_last_mark_ids == {}


class TestTwilioHandlerStart:
    """Test TwilioHandler start method."""

    @pytest.mark.serial
    async def test_start_success(self, twilio_handler):
        """Test successful start of TwilioHandler - should just set up task loops."""
        # Mock the async task creation
        with patch("asyncio.create_task") as mock_create_task:
            mock_create_task.return_value = Mock()

            await twilio_handler.start()

        # Verify WebSocket acceptance
        twilio_handler.twilio_websocket.accept.assert_called_once()

        # Verify tasks created (should create async task loops)
        assert mock_create_task.call_count == 2

        # note, the realtime session task is created in the _twilio_message_loop(),
        # after agent initialization
        # note, the inactivity monitor task is created in _setup_realtime_session,
        # after bind_contextvars so it inherits structlog context (call_sid, etc.)

        # Verify the loop tasks are stored
        assert twilio_handler._message_loop_task is not None
        assert twilio_handler._buffer_flush_task is not None
        assert twilio_handler._realtime_session_task is None
        assert twilio_handler._inactivity_monitor_task is None

    @patch("agent_leasing.twilio_handler.trace")
    @patch("agent_leasing.twilio_handler.agent_selector")
    @patch("agent_leasing.twilio_handler.RealtimeRunner")
    async def test_agent_setup_when_greeting_disabled(
        self,
        mock_realtime_runner,
        mock_agent_selector,
        mock_trace,
        twilio_handler,
        mock_realtime_session,
        monkeypatch,
    ):
        """Sequential path: full agent inits inline, RealtimeRunner gets the full agent."""
        monkeypatch.setattr(settings, "greeting_agent_enabled", False)

        test_payload = examples.ASK_REQUEST_RESIDENT_VOICE_KNCK

        twilio_handler.root_run = Mock()
        twilio_handler.root_run.to_headers.return_value = {"x-test-langsmith": "1"}

        mock_agent = Mock()
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.agent_instance = Mock()
        mock_agent_selector.return_value = mock_agent

        mock_runner = Mock()
        mock_runner.run = AsyncMock(return_value=mock_realtime_session)
        mock_realtime_runner.return_value = mock_runner

        await twilio_handler._agent_setup(test_payload)

        mock_agent.__aenter__.assert_called_once()
        assert twilio_handler.ctx.langsmith_run_tree == {"x-test-langsmith": "1"}

        mock_realtime_runner.assert_called_once_with(mock_agent.agent())
        mock_runner.run.assert_called_once()
        mock_trace.assert_called_once()

        assert twilio_handler.session == mock_realtime_session
        mock_realtime_session.enter.assert_called_once()
        assert twilio_handler._session_ready.is_set()

    @patch("agent_leasing.twilio_handler.build_parallel_greeting_agent")
    @patch("agent_leasing.twilio_handler.agent_selector")
    @patch("agent_leasing.twilio_handler.RealtimeRunner")
    async def test_agent_setup_when_greeting_enabled(
        self,
        mock_realtime_runner,
        mock_agent_selector,
        mock_build_greeting,
        twilio_handler,
        mock_realtime_session,
        monkeypatch,
    ):
        """Greeting path: greeting agent fires immediately, full agent inits in background."""
        monkeypatch.setattr(settings, "greeting_agent_enabled", True)

        test_payload = examples.ASK_REQUEST_RESIDENT_VOICE_KNCK

        twilio_handler.root_run = Mock()
        twilio_handler.root_run.to_headers.return_value = {"x-test-langsmith": "1"}
        twilio_handler.root_run.create_child.return_value = Mock()

        twilio_handler._prepare_greeting_context = AsyncMock()
        twilio_handler._init_full_agent = AsyncMock()

        greeting_agent = Mock(name="greeting_agent")
        mock_build_greeting.return_value = greeting_agent

        full_agent = Mock()
        full_agent.__aenter__ = AsyncMock(return_value=full_agent)
        mock_agent_selector.return_value = full_agent

        mock_runner = Mock()
        mock_runner.run = AsyncMock(return_value=mock_realtime_session)
        mock_realtime_runner.return_value = mock_runner

        await twilio_handler._agent_setup(test_payload)
        # Drain the background full-agent-init task created via asyncio.create_task.
        await twilio_handler._full_agent_task

        assert twilio_handler.ctx.langsmith_run_tree == {"x-test-langsmith": "1"}

        mock_build_greeting.assert_called_once_with(twilio_handler.ctx)
        mock_realtime_runner.assert_called_once_with(greeting_agent)
        mock_runner.run.assert_called_once()

        assert twilio_handler._full_agent_task is not None
        full_agent.__aenter__.assert_not_called()

        assert twilio_handler.session == mock_realtime_session
        mock_realtime_session.enter.assert_called_once()
        assert twilio_handler._session_ready.is_set()

    @patch("agent_leasing.twilio_handler.asyncio.create_task")
    @patch("agent_leasing.twilio_handler.build_parallel_greeting_agent")
    @patch("agent_leasing.twilio_handler.ensure_disabled_modules_and_tools_loaded", new_callable=AsyncMock)
    async def test_agent_setup_greeting_path_loads_context_before_building_greeting_agent(
        self,
        mock_ensure_disabled_modules_and_tools_loaded,
        mock_build_parallel_greeting_agent,
        mock_create_task,
        twilio_handler,
    ):
        ask_request = AskRequest(**examples.ASK_REQUEST_RESIDENT_VOICE_KNCK)
        twilio_handler.ctx = Mock()
        twilio_handler.ctx.disabled_modules = []
        twilio_handler.ctx.disabled_tools = []
        twilio_handler.root_run = Mock()
        twilio_handler.root_run.create_child.return_value = Mock()
        twilio_handler._configure_session = Mock(return_value={"request-id": "1"})
        twilio_handler._setup_realtime_session = AsyncMock()
        twilio_handler._enter_and_greet = AsyncMock()

        full_agent_task = Mock()
        mock_create_task.return_value = full_agent_task
        greeting_agent = Mock()

        async def load_prompt_context(ctx):
            ctx.disabled_modules = ["PACKAGES"]
            ctx.disabled_tools = ["get_residents_packages"]

        def build_greeting_agent(ctx):
            assert ctx.disabled_modules == ["PACKAGES"]
            assert ctx.disabled_tools == ["get_residents_packages"]
            return greeting_agent

        mock_ensure_disabled_modules_and_tools_loaded.side_effect = load_prompt_context
        mock_build_parallel_greeting_agent.side_effect = build_greeting_agent

        await twilio_handler._agent_setup_greeting_path(ask_request)

        mock_ensure_disabled_modules_and_tools_loaded.assert_awaited_once_with(twilio_handler.ctx)
        mock_build_parallel_greeting_agent.assert_called_once_with(twilio_handler.ctx)
        twilio_handler._setup_realtime_session.assert_awaited_once_with(greeting_agent, {"request-id": "1"})
        twilio_handler._enter_and_greet.assert_awaited_once()
        full_agent_task.add_done_callback.assert_called_once()


class TestTwilioHandlerMetadata:
    """Regression tests for voice tracing metadata shape."""

    def test_build_session_metadata_excludes_input_and_stays_within_limit(self, twilio_handler):
        ask_request = AskRequest(**examples.ASK_REQUEST_RESIDENT_VOICE_KNCK)
        twilio_handler.ctx = Mock(
            ask_request=ask_request,
            openai_group_url="https://platform.openai.com/traces/group/test",
        )
        twilio_handler.trace_id = "trace_0000000000000000000000000000abcd"

        metadata = twilio_handler._build_session_metadata(ask_request)

        assert "input" not in metadata
        assert len(metadata) <= 16


class TestTwilioHandlerMessageHandling:
    """Test Twilio message handling methods."""

    async def test_handle_twilio_message_connected(self, twilio_handler):
        """Test handling connected event."""
        message = {"event": "connected"}

        await twilio_handler._handle_twilio_message(message)

        # Should not raise any exceptions and log appropriately

    @patch("agent_leasing.twilio_handler.asyncio.create_task")
    @patch.object(TwilioHandler, "_agent_setup")
    async def test_handle_twilio_message_start(self, mock_agent_setup, mock_create_task, twilio_handler):
        """Test handling start event - should set stream_sid, start pacer, and call agent setup."""
        # Create a minimal valid payload with required product_info structure
        test_payload = {
            "product": "test_product",
            "product_info": {"call_sid": "original-call-sid"},
        }
        # Base64 encode the payload using orjson (matching implementation)
        import base64

        encoded_payload = base64.b64encode(orjson.dumps(test_payload)).decode()

        message = {
            "event": "start",
            "start": {
                "streamSid": "test-stream-sid",
                "callSid": "test-call-sid",
                "customParameters": {"payload": encoded_payload},
            },
        }

        # Mock the task returned by create_task
        mock_task = AsyncMock()
        mock_create_task.return_value = mock_task

        assert not twilio_handler._pacer_running

        await twilio_handler._handle_twilio_message(message)

        # Verify stream_sid, call_sid, and payload are set
        assert twilio_handler._stream_sid == "test-stream-sid"
        assert twilio_handler._call_sid == "test-call-sid"
        assert twilio_handler._payload is not None

        # Verify pacer was started
        assert twilio_handler._pacer_running is True
        mock_create_task.assert_called_once()
        call_kwargs = mock_create_task.call_args.kwargs
        assert call_kwargs.get("name") == "twilio_ulaw_pacer"
        mock_task.add_done_callback.assert_called_once()

        # Verify agent setup was called with the payload
        mock_agent_setup.assert_called_once_with(payload=twilio_handler._payload)

    async def test_handle_twilio_message_media(self, twilio_handler):
        """Test handling media event."""
        message = {
            "event": "media",
            "media": {"payload": base64.b64encode(b"test-audio").decode()},
        }

        with patch.object(twilio_handler, "_handle_media_event") as mock_handle:
            await twilio_handler._handle_twilio_message(message)
            mock_handle.assert_called_once_with(message)

    async def test_handle_twilio_message_mark(self, twilio_handler):
        """Test handling mark event."""
        message = {"event": "mark", "mark": {"name": "test-mark"}}

        with patch.object(twilio_handler, "_handle_mark_event") as mock_handle:
            await twilio_handler._handle_twilio_message(message)
            mock_handle.assert_called_once_with(message)

    async def test_handle_twilio_message_stop(self, twilio_handler):
        """Test handling stop event."""
        message = {"event": "stop"}

        await twilio_handler._handle_twilio_message(message)

        # Should not raise any exceptions and log appropriately

    async def test_handle_twilio_message_exception(self, twilio_handler):
        """Test exception handling in message processing."""
        message = {"event": "media"}

        with patch.object(twilio_handler, "_handle_media_event", side_effect=Exception("Test error")):
            # Should not raise exception, just log it
            await twilio_handler._handle_twilio_message(message)

    async def test_process_start_payload_uses_default_without_test_payload(self, twilio_handler):
        """Test fallback to default payload when no test payload is configured."""
        start_payload = {
            "streamSid": "test-stream-sid",
            "callSid": "test-call-sid",
            "customParameters": {},
        }

        with (
            patch("agent_leasing.twilio_handler.settings.twilio_test_payload", None),
            patch.object(twilio_handler, "_start_recording", new=AsyncMock()),
        ):
            stream_sid, call_sid, payload = await twilio_handler._process_start_payload(start_payload)

        expected_payload = copy.deepcopy(examples.ASK_REQUEST_RESIDENT_VOICE_KNCK)
        expected_payload["call_sid"] = "test-call-sid"
        expected_payload["product_info"]["call_sid"] = "test-call-sid"

        assert stream_sid == "test-stream-sid"
        assert call_sid == "test-call-sid"
        assert payload == expected_payload

    async def test_process_start_payload_falls_back_on_invalid_test_payload(self, twilio_handler, tmp_path):
        """Test fallback to default payload when test payload file is invalid."""
        start_payload = {
            "streamSid": "test-stream-sid",
            "callSid": "test-call-sid",
            "customParameters": {},
        }
        bad_payload_path = tmp_path / "bad_payload.json"
        bad_payload_path.write_text("not-json", encoding="utf-8")

        with (
            patch("agent_leasing.twilio_handler.settings.twilio_test_payload", str(bad_payload_path)),
            patch.object(twilio_handler, "_start_recording", new=AsyncMock()),
        ):
            stream_sid, call_sid, payload = await twilio_handler._process_start_payload(start_payload)

        expected_payload = copy.deepcopy(examples.ASK_REQUEST_RESIDENT_VOICE_KNCK)
        expected_payload["call_sid"] = "test-call-sid"
        expected_payload["product_info"]["call_sid"] = "test-call-sid"

        assert stream_sid == "test-stream-sid"
        assert call_sid == "test-call-sid"
        assert payload == expected_payload

    def test_load_test_payload_uses_default_when_unset(self):
        """Test default test payload when no path is configured."""
        with patch("agent_leasing.twilio_handler.settings.twilio_test_payload", None):
            payload = TwilioHandler._load_test_payload()

        assert payload == examples.ASK_REQUEST_RESIDENT_VOICE_KNCK

    def test_load_test_payload_falls_back_on_non_object_json(self, tmp_path):
        """Test fallback when test payload JSON is not an object."""
        bad_payload_path = tmp_path / "bad_payload.json"
        bad_payload_path.write_text('["not-an-object"]', encoding="utf-8")

        with patch("agent_leasing.twilio_handler.settings.twilio_test_payload", str(bad_payload_path)):
            payload = TwilioHandler._load_test_payload()

        assert payload == examples.ASK_REQUEST_RESIDENT_VOICE_KNCK


class TestTwilioHandlerMediaHandling:
    """Test media event handling."""

    async def test_handle_media_event_success(self, twilio_handler):
        """Test successful media event handling."""
        test_audio = b"test-audio-data"
        payload = base64.b64encode(test_audio).decode()
        message = {"media": {"payload": payload}}

        with patch.object(twilio_handler, "_flush_audio_buffer") as mock_flush:
            await twilio_handler._handle_media_event(message)

            # Audio should be added to buffer
            assert test_audio in twilio_handler._audio_buffer

            # If buffer is large enough, should flush
            if len(twilio_handler._audio_buffer) >= twilio_handler.BUFFER_SIZE_BYTES:
                mock_flush.assert_called_once()

    async def test_handle_media_event_empty_payload(self, twilio_handler):
        """Test media event with empty payload."""
        message = {"media": {"payload": ""}}

        await twilio_handler._handle_media_event(message)

        # Buffer should remain empty
        assert len(twilio_handler._audio_buffer) == 0

    async def test_handle_media_event_invalid_base64(self, twilio_handler):
        """Test media event with invalid base64."""
        message = {"media": {"payload": "invalid-base64!"}}

        # Should not raise exception, just log error
        await twilio_handler._handle_media_event(message)

    async def test_handle_media_event_buffer_flush(self, twilio_handler):
        """Test media event triggers buffer flush when full."""
        # Fill buffer to trigger flush
        large_audio = b"x" * twilio_handler.BUFFER_SIZE_BYTES
        payload = base64.b64encode(large_audio).decode()
        message = {"media": {"payload": payload}}

        with patch.object(twilio_handler, "_flush_audio_buffer") as mock_flush:
            await twilio_handler._handle_media_event(message)
            mock_flush.assert_called_once()


class TestTwilioHandlerMarkHandling:
    """Test mark event handling."""

    async def test_handle_mark_event_success(self, twilio_handler):
        """Test successful mark event handling."""
        # Setup mark data
        mark_id = "test-mark"
        item_id = "test-item"
        content_index = 5
        byte_count = 100

        twilio_handler._mark_data[mark_id] = (item_id, content_index, byte_count)

        message = {"mark": {"name": mark_id}}

        with patch.object(twilio_handler.playback_tracker, "on_play_bytes") as mock_on_play:
            await twilio_handler._handle_mark_event(message)

            # Should call playback tracker
            mock_on_play.assert_called_once()
            args = mock_on_play.call_args[0]
            assert args[0] == item_id
            assert args[1] == content_index
            assert len(args[2]) == byte_count

            # Should clean up mark data
            assert mark_id not in twilio_handler._mark_data

    async def test_handle_mark_event_unknown_mark(self, twilio_handler):
        """Test mark event with unknown mark ID."""
        message = {"mark": {"name": "unknown-mark"}}

        # Should not raise exception
        await twilio_handler._handle_mark_event(message)

    async def test_handle_mark_event_exception(self, twilio_handler):
        """Test exception handling in mark event."""
        message = {"mark": {"name": "test-mark"}}

        with patch.object(
            twilio_handler.playback_tracker,
            "on_play_bytes",
            side_effect=Exception("Test error"),
        ):
            # Should not raise exception, just log it
            await twilio_handler._handle_mark_event(message)

    async def test_send_mark_does_not_raise_when_websocket_closing(self, twilio_handler):
        """Test mark send gracefully handles websocket closing runtime error."""
        twilio_handler._stream_sid = "test-stream"
        twilio_handler.call_active = True
        twilio_handler._pacer_running = True

        connected = Mock()
        connected.name = "CONNECTED"
        twilio_handler.twilio_websocket.client_state = connected
        twilio_handler.twilio_websocket.application_state = connected
        twilio_handler.twilio_websocket.send_text = AsyncMock(
            side_effect=RuntimeError('Cannot call "send" once a close message has been sent.')
        )

        # Should not raise
        await twilio_handler._send_mark("1")
        twilio_handler.twilio_websocket.send_text.assert_called_once()


class TestTwilioHandlerRealtimeEvents:
    """Test realtime event handling."""

    async def test_handle_realtime_event_audio(self, twilio_handler):
        """Test handling audio realtime event - should queue for pacer."""
        twilio_handler._stream_sid = "test-stream"
        twilio_handler.call_active = True

        # Create mock audio event with enough data to create at least one frame
        # Frame size is 160 bytes, so we need at least that much
        audio_data = b"x" * 160  # Exactly one frame
        event = Mock()
        event.type = "audio"
        event.audio = Mock()
        event.audio.data = audio_data
        event.audio.item_id = "test-item"
        event.audio.content_index = 5

        await twilio_handler._handle_realtime_event(event)

        # Audio should be queued for pacer, not sent directly
        assert len(twilio_handler._out_frame_q) == 1

        # Check that frame is queued with correct metadata
        frame, frame_event = twilio_handler._out_frame_q[0]
        assert len(frame) == 160
        mark_id, item_id, content_index = frame_event
        assert item_id == "test-item"
        assert content_index == 5

        # Check mark data stored (mark_id -> (item_id, content_index, byte_count))
        assert mark_id in twilio_handler._mark_data
        assert twilio_handler._mark_data[mark_id] == ("test-item", 5, len(audio_data))

        # Pacer should be started
        assert twilio_handler._pacer_running is True

    async def test_handle_realtime_event_audio_interrupted(self, twilio_handler):
        """Test handling audio_interrupted realtime event."""
        twilio_handler._stream_sid = "test-stream"
        # Add some frames to queue to test clearing
        twilio_handler._out_frame_q.append((b"x" * 320, ("mark1", "item1", 0)))
        twilio_handler._out_partial.extend(b"partial")

        event = Mock()
        event.type = "audio_interrupted"

        await twilio_handler._handle_realtime_event(event)

        # Should send clear message
        twilio_handler.twilio_websocket.send_text.assert_called_once()
        clear_message = orjson.loads(twilio_handler.twilio_websocket.send_text.call_args[0][0])
        assert clear_message["event"] == "clear"
        assert clear_message["streamSid"] == "test-stream"

        # Should clear pacer queue
        assert len(twilio_handler._out_frame_q) == 0
        assert len(twilio_handler._out_partial) == 0
        assert twilio_handler._current_partial_event is None
        assert twilio_handler._first_ulaw_rx_ts is None

    async def test_handle_realtime_event_audio_end(self, twilio_handler):
        """Test handling audio_end realtime event."""
        event = Mock()
        event.type = "audio_end"

        # Should not raise exception
        await twilio_handler._handle_realtime_event(event)

    async def test_handle_realtime_event_raw_model_event(self, twilio_handler):
        """Test handling raw_model_event realtime event."""
        event = Mock()
        event.type = "raw_model_event"

        # Should not raise exception
        await twilio_handler._handle_realtime_event(event)

    @patch("agent_leasing.twilio_handler.asyncio.create_task")
    @patch("agent_leasing.twilio_handler.log_data_curation_event_for_realtime_history")
    async def test_handle_stop_event_schedules_data_curation_logging_task(
        self,
        mock_log_data_curation_event_for_realtime_history,
        mock_create_task,
        twilio_handler,
        resident_context_voice_knck,
    ):
        message = {"event": "stop"}
        session = Mock()
        session._context_wrapper = Mock()
        session._context_wrapper.context = resident_context_voice_knck
        session.close = AsyncMock()

        session._history = [
            UserMessageItem(
                content=[InputAudio(transcript="Hi, how are you?", type="input_audio")],
                item_id="user-message-item-id",
            ),
        ]
        twilio_handler.session = session
        twilio_handler.ctx = Mock()
        twilio_handler.ctx.ask_request = Mock()
        twilio_handler.ctx.ask_request.product = "test_product"

        # Setup minimal task mocks so _cleanup_call can run
        twilio_handler._realtime_session_task = None
        twilio_handler._buffer_flush_task = None
        twilio_handler._message_loop_task = None
        twilio_handler._inactivity_monitor_task = None
        twilio_handler._pacer_task = None
        twilio_handler.agent = None

        mock_task = Mock()
        mock_task.add_done_callback = Mock()
        mock_task.done.return_value = False
        mock_create_task.return_value = mock_task

        await twilio_handler._handle_twilio_message(message)

        mock_log_data_curation_event_for_realtime_history.assert_called_once()
        history_snapshot, context = mock_log_data_curation_event_for_realtime_history.call_args[0]
        assert context is resident_context_voice_knck
        assert isinstance(history_snapshot, list)
        assert history_snapshot[0] is session._history[0]
        # Avoid "coroutine was never awaited" warnings since create_task is mocked.
        mock_create_task.call_args[0][0].close()

    async def test_handle_realtime_event_unknown_type(self, twilio_handler):
        """Test handling unknown realtime event type."""
        event = Mock()
        event.type = "unknown_type"

        # Should not raise exception
        await twilio_handler._handle_realtime_event(event)

    async def test_handle_realtime_event_error_triggers_recovery(self, twilio_handler):
        """Test that error events trigger recovery."""
        event = Mock()
        event.type = "error"
        event.error = {"message": "Tool failure"}

        twilio_handler._recover_realtime_session = AsyncMock()

        await twilio_handler._handle_realtime_event(event)

        twilio_handler._recover_realtime_session.assert_awaited_once_with(event)


class TestTwilioHandlerErrorHandling:
    """Test graceful handling of realtime/model errors."""

    async def test_handle_model_behavior_error_recovers_session(
        self,
        twilio_handler,
        mock_realtime_session,
    ):
        twilio_handler.session = mock_realtime_session
        twilio_handler._recover_realtime_session = AsyncMock()

        await twilio_handler._handle_realtime_error_event(error=ModelBehaviorError("fail"))

        twilio_handler._recover_realtime_session.assert_awaited_once_with(None)

    async def test_handle_realtime_error_event_recovers_with_event(
        self,
        twilio_handler,
        mock_realtime_session,
    ):
        twilio_handler.session = mock_realtime_session
        twilio_handler._recover_realtime_session = AsyncMock()
        event = Mock()

        await twilio_handler._handle_realtime_error_event(event=event)

        twilio_handler._recover_realtime_session.assert_awaited_once_with(event)

    @patch("agent_leasing.twilio_handler.asyncio.sleep", new_callable=AsyncMock)
    async def test_handle_active_response_error_forces_cancel_and_retries(
        self,
        mock_sleep,
        twilio_handler,
        mock_realtime_session,
    ):
        """Test that 'active response in progress' error triggers forced cancel then retries response.create.

        KNCK-38893: After canceling the conflicting response, we must retry response.create
        so the thinker tool result (already in conversation history) is not silently dropped.
        """
        # Set up mock session with _model.send_event and _ongoing_response
        mock_model = Mock()
        mock_model.send_event = AsyncMock()
        mock_model._ongoing_response = None  # Simulate cancellation completed immediately
        mock_realtime_session._model = mock_model
        twilio_handler.session = mock_realtime_session
        twilio_handler._recover_realtime_session = AsyncMock()

        # Create a mock RealtimeError with the specific error message
        error_event = Mock(spec=RealtimeError)
        error_event.__str__ = Mock(return_value="Conversation already has an active response in progress: resp_123")

        await twilio_handler._handle_realtime_error_event(event=error_event)

        # Verify both forced cancel AND response.create retry were sent
        assert mock_model.send_event.await_count == 2
        first_call = mock_model.send_event.call_args_list[0][0][0]
        assert isinstance(first_call, RealtimeModelSendInterrupt)
        assert first_call.force_response_cancel is True
        second_call = mock_model.send_event.call_args_list[1][0][0]
        assert isinstance(second_call, RealtimeModelSendRawMessage)
        assert second_call.message["type"] == "response.create"

        # Verify _expecting_cancel_interrupt was reset after the operation
        assert twilio_handler._expecting_cancel_interrupt is False

        # Verify session recovery was NOT called (we handle this error differently)
        twilio_handler._recover_realtime_session.assert_not_awaited()

    @patch("agent_leasing.twilio_handler.asyncio.sleep", new_callable=AsyncMock)
    async def test_handle_active_response_error_skips_retry_on_cancellation_timeout(
        self,
        mock_sleep,
        twilio_handler,
        mock_realtime_session,
    ):
        """Test that response.create retry is skipped when cancellation is not confirmed within timeout."""
        mock_model = Mock()
        mock_model.send_event = AsyncMock()
        # _ongoing_response stays truthy, simulating cancellation never completing
        mock_model._ongoing_response = True
        mock_realtime_session._model = mock_model
        twilio_handler.session = mock_realtime_session
        twilio_handler._recover_realtime_session = AsyncMock()

        error_event = Mock(spec=RealtimeError)
        error_event.__str__ = Mock(return_value="Conversation already has an active response in progress: resp_123")

        await twilio_handler._handle_realtime_error_event(event=error_event)

        # Only the cancel should be sent, NOT the response.create retry
        assert mock_model.send_event.await_count == 1
        first_call = mock_model.send_event.call_args_list[0][0][0]
        assert isinstance(first_call, RealtimeModelSendInterrupt)

        # _expecting_cancel_interrupt should still be reset via finally
        assert twilio_handler._expecting_cancel_interrupt is False

    async def test_handle_active_response_error_without_session(
        self,
        twilio_handler,
    ):
        """Test graceful handling when session is None during active response error."""
        twilio_handler.session = None
        twilio_handler._recover_realtime_session = AsyncMock()

        error_event = Mock(spec=RealtimeError)
        error_event.__str__ = Mock(return_value="Conversation already has an active response in progress: resp_123")

        # Should not raise an exception
        await twilio_handler._handle_realtime_error_event(event=error_event)

        # Recovery should not be called
        twilio_handler._recover_realtime_session.assert_not_awaited()

    async def test_handle_active_response_error_send_event_fails(
        self,
        twilio_handler,
        mock_realtime_session,
    ):
        """Test graceful handling when send_event fails."""
        mock_model = Mock()
        mock_model.send_event = AsyncMock(side_effect=Exception("Connection closed"))
        mock_realtime_session._model = mock_model
        twilio_handler.session = mock_realtime_session
        twilio_handler._recover_realtime_session = AsyncMock()

        error_event = Mock(spec=RealtimeError)
        error_event.__str__ = Mock(return_value="Conversation already has an active response in progress: resp_123")

        # Should not raise an exception, just log the warning
        await twilio_handler._handle_realtime_error_event(event=error_event)

        # send_event was attempted
        mock_model.send_event.assert_awaited_once()

        # Recovery should still not be called
        twilio_handler._recover_realtime_session.assert_not_awaited()


class TestTwilioHandlerAudioBuffer:
    """Test audio buffer management."""

    async def test_flush_audio_buffer_success(self, twilio_handler, mock_realtime_session):
        """Test successful audio buffer flush."""
        twilio_handler.session = mock_realtime_session
        test_data = b"test-audio-data"
        twilio_handler._audio_buffer.extend(test_data)

        with patch.object(settings, "twilio_input_audio_noise_reduction_enabled", False):
            await twilio_handler._flush_audio_buffer()

        # Should send audio to session
        mock_realtime_session.send_audio.assert_called_once_with(test_data)

        # Should clear buffer
        assert len(twilio_handler._audio_buffer) == 0

        # Should update timestamp
        assert twilio_handler._last_buffer_send_time > 0

    async def test_flush_audio_buffer_with_noise_reduction_enabled(self, twilio_handler, mock_realtime_session):
        """Test audio buffer flush with noise reduction enabled."""
        twilio_handler.session = mock_realtime_session
        test_data = b"test-audio-data"
        twilio_handler._audio_buffer.extend(test_data)

        with patch.object(settings, "twilio_input_audio_noise_reduction_enabled", True):
            with patch.object(settings, "openai_audio_format", "g711_ulaw"):
                with patch("agent_leasing.twilio_handler.apply_noise_reduction", return_value=b"reduced") as mock_nr:
                    await twilio_handler._flush_audio_buffer()

        mock_nr.assert_called_once_with(test_data, "g711_ulaw")
        mock_realtime_session.send_audio.assert_called_once_with(b"reduced")

    async def test_flush_audio_buffer_no_session(self, twilio_handler):
        """Test flush when no session available."""
        twilio_handler.session = None
        twilio_handler._audio_buffer.extend(b"test-data")

        await twilio_handler._flush_audio_buffer()

        # Buffer should remain unchanged
        assert len(twilio_handler._audio_buffer) > 0

    async def test_flush_audio_buffer_empty(self, twilio_handler, mock_realtime_session):
        """Test flush with empty buffer."""
        twilio_handler.session = mock_realtime_session

        await twilio_handler._flush_audio_buffer()

        # Should not send anything
        mock_realtime_session.send_audio.assert_not_called()

    async def test_flush_audio_buffer_exception(self, twilio_handler, mock_realtime_session):
        """Test exception handling in buffer flush."""
        twilio_handler.session = mock_realtime_session
        twilio_handler._audio_buffer.extend(b"test-data")
        mock_realtime_session.send_audio.side_effect = Exception("Test error")

        # Should not raise exception, just log it
        await twilio_handler._flush_audio_buffer()

    async def test_flush_audio_buffer_during_initial_greeting(self, twilio_handler, mock_realtime_session):
        """Test that audio buffer is not flushed during initial greeting."""
        twilio_handler.session = mock_realtime_session
        twilio_handler._is_initial_greeting = True
        test_data = b"user-audio-during-greeting"
        twilio_handler._audio_buffer.extend(test_data)

        await twilio_handler._flush_audio_buffer()

        # Should NOT send audio to session during greeting
        mock_realtime_session.send_audio.assert_not_called()

        # Buffer should remain (not cleared during greeting)
        assert len(twilio_handler._audio_buffer) == len(test_data)

    async def test_flush_audio_buffer_after_greeting_completes(self, twilio_handler, mock_realtime_session):
        """Test that audio buffer is flushed normally after greeting completes."""
        twilio_handler.session = mock_realtime_session
        twilio_handler._is_initial_greeting = False
        test_data = b"user-audio-after-greeting"
        twilio_handler._audio_buffer.extend(test_data)

        with patch.object(settings, "twilio_input_audio_noise_reduction_enabled", False):
            await twilio_handler._flush_audio_buffer()

        # Should send audio to session after greeting
        mock_realtime_session.send_audio.assert_called_once_with(test_data)

        # Buffer should be cleared
        assert len(twilio_handler._audio_buffer) == 0

    async def test_flush_audio_buffer_clears_buffer_before_awaiting_send(self, twilio_handler, mock_realtime_session):
        """KNCK-39464: the buffer must be empty once send_audio is awaited.

        If the buffer is still populated when send_audio is entered, a concurrent
        flusher could snapshot the same bytes before the clear happens. Verifies
        the snapshot-then-clear ordering inside _flush_audio_buffer.
        """
        twilio_handler.session = mock_realtime_session
        test_data = b"\x7f" * 200
        twilio_handler._audio_buffer.extend(test_data)

        observed_buffer_len: list[int] = []

        async def capture_buffer_len(_data):
            observed_buffer_len.append(len(twilio_handler._audio_buffer))

        mock_realtime_session.send_audio = capture_buffer_len

        with patch.object(settings, "twilio_input_audio_noise_reduction_enabled", False):
            await twilio_handler._flush_audio_buffer()

        assert observed_buffer_len == [0]

    async def test_flush_audio_buffer_serializes_concurrent_calls(self, twilio_handler, mock_realtime_session):
        """KNCK-39464: concurrent _flush_audio_buffer calls must not double-send.

        Without mutual exclusion, the message loop and the periodic buffer flush
        loop can both enter _flush_audio_buffer, snapshot the same unflushed bytes,
        and call send_audio with identical content before either clears the buffer.
        This test forces the race by gating send_audio with an asyncio.Event so
        that the first caller parks inside the await while a second caller
        attempts to flush the same buffer.
        """
        twilio_handler.session = mock_realtime_session
        expected = b"\xff" * 400
        twilio_handler._audio_buffer.extend(expected)

        send_entered = asyncio.Event()
        release_send = asyncio.Event()
        send_calls: list[bytes] = []

        async def gated_send_audio(data):
            send_calls.append(data)
            send_entered.set()
            await release_send.wait()

        mock_realtime_session.send_audio = gated_send_audio

        with patch.object(settings, "twilio_input_audio_noise_reduction_enabled", False):
            # Task A: acquires the lock, clears the buffer under the lock, parks
            # inside send_audio awaiting release.
            task_a = asyncio.create_task(twilio_handler._flush_audio_buffer())
            await send_entered.wait()

            # Buffer is cleared under the lock before the await, so a concurrent
            # flusher that acquires the lock next will find an empty buffer.
            assert len(twilio_handler._audio_buffer) == 0

            # Task B: blocks on _flush_lock until A releases it, then finds an
            # empty buffer and returns without calling send_audio.
            task_b = asyncio.create_task(twilio_handler._flush_audio_buffer())

            # Give the scheduler ticks so B reaches the lock acquisition.
            for _ in range(5):
                await asyncio.sleep(0)

            # Release A; B will acquire the lock right after.
            release_send.set()
            await asyncio.gather(task_a, task_b)

        assert len(send_calls) == 1, f"send_audio called {len(send_calls)}x — race not closed"
        assert send_calls[0] == expected

    async def test_buffer_flush_loop_timeout(self, twilio_handler, mock_realtime_session):
        """Test buffer flush loop with timeout."""
        twilio_handler.session = mock_realtime_session
        twilio_handler._audio_buffer.extend(b"old-data")

        # Set old timestamp to trigger flush
        twilio_handler._last_buffer_send_time = time.time() - (twilio_handler.CHUNK_LENGTH_S * 3)

        with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError()]):
            with patch.object(twilio_handler, "_flush_audio_buffer") as mock_flush:
                try:
                    await twilio_handler._buffer_flush_loop()
                except asyncio.CancelledError:
                    pass

                mock_flush.assert_called_once()

    @pytest.mark.skip(reason="Flaky test - timing-sensitive mock assertions fail intermittently in CI")
    async def test_buffer_flush_loop_no_timeout(self, twilio_handler, mock_realtime_session):
        """Test buffer flush loop without timeout."""
        twilio_handler.session = mock_realtime_session
        twilio_handler._audio_buffer.extend(b"recent-data")

        # Set a future timestamp to ensure we never cross the stale-data threshold, even on slow CI.
        twilio_handler._last_buffer_send_time = time.time() + 60.0

        with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError()]):
            with patch.object(twilio_handler, "_flush_audio_buffer") as mock_flush:
                try:
                    await twilio_handler._buffer_flush_loop()
                except asyncio.CancelledError:
                    pass

                mock_flush.assert_not_called()


class TestTwilioHandlerMessageLoop:
    """Test message loop functionality."""

    async def test_twilio_message_loop_success(self, twilio_handler):
        """Test successful message loop processing."""
        messages = [
            '{"event": "connected"}',
            '{"event": "start", "start": {"streamSid": "test"}}',
        ]

        twilio_handler.twilio_websocket.receive_text.side_effect = messages + [asyncio.CancelledError()]

        with patch("agent_leasing.twilio_handler.ls.trace") as mock_ls_trace:
            run = Mock()
            mock_ls_trace.return_value.__enter__.return_value = run

            with patch.object(twilio_handler, "_handle_twilio_message") as mock_handle:
                try:
                    await twilio_handler._twilio_message_loop()
                except asyncio.CancelledError:
                    pass

                assert mock_handle.call_count == 2

    async def test_twilio_message_loop_json_error(self, twilio_handler):
        """Test message loop with JSON decode error."""
        twilio_handler.twilio_websocket.receive_text.return_value = "invalid-json"

        with patch("agent_leasing.twilio_handler.ls.trace") as mock_ls_trace:
            run = Mock()
            mock_ls_trace.return_value.__enter__.return_value = run

            # Should not raise exception, just log error (orjson.JSONDecodeError or ValueError)
            try:
                await twilio_handler._twilio_message_loop()
            except Exception:
                # If it raises, it should be caught and logged
                pass

    async def test_twilio_message_loop_general_exception(self, twilio_handler):
        """Test message loop with general exception."""
        twilio_handler.twilio_websocket.receive_text.side_effect = Exception("Test error")

        with patch("agent_leasing.twilio_handler.ls.trace") as mock_ls_trace:
            run = Mock()
            mock_ls_trace.return_value.__enter__.return_value = run

            # Should not raise exception, just log error
            await twilio_handler._twilio_message_loop()

    async def test_realtime_session_loop_success(self, twilio_handler, mock_realtime_session):
        """Test successful realtime session loop."""
        events = [Mock(), Mock()]

        mock_realtime_session.__aiter__.return_value = _AsyncIterator(items=list(events))
        twilio_handler.session = mock_realtime_session

        # Set the session ready event so the loop doesn't block
        twilio_handler._session_ready.set()

        with patch.object(twilio_handler, "_handle_realtime_event") as mock_handle:
            await twilio_handler._realtime_session_loop()

            assert mock_handle.call_count == 2

    async def test_realtime_session_loop_exception(self, twilio_handler, mock_realtime_session):
        """Test realtime session loop with exception."""
        mock_realtime_session.__aiter__.return_value = _AsyncIterator(exception=Exception("Test error"))
        twilio_handler.session = mock_realtime_session

        # Set the session ready event so the loop doesn't block
        twilio_handler._session_ready.set()

        # Should not raise exception, just log error
        await twilio_handler._realtime_session_loop()

    async def test_realtime_session_loop_model_behavior_error(self, twilio_handler, mock_realtime_session):
        """Test realtime session loop recovery on ModelBehaviorError."""
        mock_realtime_session.__aiter__.return_value = _AsyncIterator(exception=ModelBehaviorError("Tool missing"))
        twilio_handler.session = mock_realtime_session

        # Set the session ready event so the loop doesn't block
        twilio_handler._session_ready.set()

        twilio_handler._handle_realtime_error_event = AsyncMock()

        await twilio_handler._realtime_session_loop()

        twilio_handler._handle_realtime_error_event.assert_awaited_once()


class TestTwilioHandlerWaitUntilDone:
    """Test wait_until_done method."""

    async def test_wait_until_done_success(self, twilio_handler):
        """Test successful wait until done."""

        # Create a proper awaitable task
        async def dummy_task():
            return None

        mock_task = asyncio.create_task(dummy_task())
        twilio_handler._message_loop_task = mock_task

        await twilio_handler.wait_until_done()

        # Task should be completed
        assert mock_task.done()

    async def test_wait_until_done_no_task(self, twilio_handler):
        """Test wait until done with no task."""
        twilio_handler._message_loop_task = None

        with pytest.raises(AssertionError):
            await twilio_handler.wait_until_done()


class TestTwilioHandlerVoiceCrashRecovery:
    """Test voice crash recovery functionality."""

    @pytest.fixture
    def mock_realtime_exception_event(self):
        """Mock RealtimeModelExceptionEvent for testing."""
        from agents.realtime import RealtimeModelExceptionEvent

        event = Mock(spec=RealtimeModelExceptionEvent)
        event.exception = Exception("Test exception")
        return event

    @pytest.fixture
    def mock_agent_with_recovery(self):
        """Mock agent for recovery testing."""
        agent = Mock()
        agent.agent = Mock(return_value="mock_starting_agent")
        return agent

    @pytest.fixture
    def mock_session_context(self):
        """Mock SessionScope with history."""
        ctx = Mock()
        ctx.history = "test conversation history"
        ctx.language_code = "en"
        ctx.ask_request = Mock()
        ctx.ask_request.property_id = "prop1"
        ctx.ask_request.product_info = Mock()
        ctx.ask_request.product_info.knock_resident_id = "res1"
        ctx.ask_request.product_info.uc_company_id = None
        ctx.ask_request.product = "voice"
        ctx.ask_request.product_info.property_name = "Test Property"
        ctx.ask_request.product_info.call_sid = "call-123"
        ctx.openai_group_url = "https://example.com/group"
        return ctx

    @pytest.fixture
    def handler_for_recovery(self, twilio_handler, mock_agent_with_recovery, mock_session_context):
        """Set up handler with sub-methods mocked at the seam."""
        twilio_handler.agent = mock_agent_with_recovery
        twilio_handler.ctx = mock_session_context
        twilio_handler.model_config = Mock()
        twilio_handler.trace_id = "test-trace-id"
        twilio_handler.group_id = "test-group-id"

        new_session = Mock()
        new_session.send_message = AsyncMock()

        twilio_handler._setup_realtime_session = AsyncMock(
            side_effect=lambda agent, meta: setattr(twilio_handler, "session", new_session)
        )
        twilio_handler._enter_realtime_session = AsyncMock()
        twilio_handler._start_realtime_session_loop = Mock()

        return twilio_handler, new_session

    EXPECTED_RECOVERY_MESSAGE = (
        "The agent crashed.  Here is the conversation state:\ntest conversation history\n"
        "Please continue the conversation with the user where it left off as naturally as possible. "
        "Only recognize the error when absolutely necessary (ideally, in a charismatic or disarming way). "
        "**IMPORTANT**: Do not call any tools as part of this response, as that may be the reason for the crash. "
        "**IMPORTANT**: Respond in en."
    )

    @pytest.mark.serial
    @pytest.mark.parametrize(
        "has_existing_task,task_cancelled",
        [
            (True, True),  # Has existing task that gets cancelled successfully
            (True, False),  # Has existing task but cancellation raises exception
            (False, None),  # No existing task
        ],
    )
    async def test_recover_realtime_session_task_cancellation(
        self,
        handler_for_recovery,
        mock_realtime_exception_event,
        has_existing_task,
        task_cancelled,
    ):
        """Test recovery cancels existing task before creating new session."""
        twilio_handler, new_session = handler_for_recovery

        if has_existing_task:
            if task_cancelled:

                async def cancelled_coro():
                    raise asyncio.CancelledError()

                mock_existing_task = asyncio.create_task(cancelled_coro())
                mock_existing_task.cancel()
            else:

                async def failed_coro():
                    raise Exception("Task error")

                mock_existing_task = asyncio.create_task(failed_coro())
                mock_existing_task.cancel()

            twilio_handler._realtime_session_task = mock_existing_task
        else:
            if hasattr(twilio_handler, "_realtime_session_task"):
                delattr(twilio_handler, "_realtime_session_task")

        await twilio_handler._recover_realtime_session(mock_realtime_exception_event)

        if has_existing_task:
            assert mock_existing_task.cancelled()

        assert twilio_handler.session == new_session
        new_session.send_message.assert_called_once_with(self.EXPECTED_RECOVERY_MESSAGE)

    @pytest.mark.serial
    async def test_recover_realtime_session_successful_recovery(
        self,
        handler_for_recovery,
        mock_realtime_exception_event,
    ):
        """Test recovery creates new session and sends recovery message."""
        twilio_handler, new_session = handler_for_recovery

        await twilio_handler._recover_realtime_session(mock_realtime_exception_event)

        assert twilio_handler.session == new_session
        twilio_handler._setup_realtime_session.assert_awaited_once()
        twilio_handler._enter_realtime_session.assert_awaited_once()
        twilio_handler._start_realtime_session_loop.assert_called_once()
        new_session.send_message.assert_called_once_with(self.EXPECTED_RECOVERY_MESSAGE)

    @pytest.mark.parametrize(
        "exception_type,exception_message",
        [
            (RuntimeError, "Runner failed"),
            (ConnectionError, "Connection lost"),
            (asyncio.TimeoutError, "Session timeout"),
        ],
    )
    async def test_recover_realtime_session_setup_exceptions(
        self,
        handler_for_recovery,
        mock_realtime_exception_event,
        exception_type,
        exception_message,
    ):
        """Test recovery propagates setup exceptions."""
        twilio_handler, new_session = handler_for_recovery

        twilio_handler._setup_realtime_session = AsyncMock(side_effect=exception_type(exception_message))

        with pytest.raises(exception_type, match=exception_message):
            await twilio_handler._recover_realtime_session(mock_realtime_exception_event)

        # Setup failed — enter and loop should not have been called
        twilio_handler._enter_realtime_session.assert_not_awaited()
        twilio_handler._start_realtime_session_loop.assert_not_called()

    async def test_recover_realtime_session_enter_failure(
        self,
        handler_for_recovery,
        mock_realtime_exception_event,
    ):
        """Test recovery propagates session enter failure."""
        twilio_handler, new_session = handler_for_recovery

        twilio_handler._enter_realtime_session = AsyncMock(side_effect=ConnectionError("Failed to enter session"))

        with pytest.raises(ConnectionError, match="Failed to enter session"):
            await twilio_handler._recover_realtime_session(mock_realtime_exception_event)

        # Enter failed — loop should not have been started
        twilio_handler._start_realtime_session_loop.assert_not_called()

    @pytest.mark.serial
    async def test_recover_realtime_session_send_message_failure(
        self,
        handler_for_recovery,
        mock_realtime_exception_event,
    ):
        """Test recovery propagates send_message failure."""
        twilio_handler, new_session = handler_for_recovery

        new_session.send_message = AsyncMock(side_effect=RuntimeError("Failed to send message"))

        with pytest.raises(RuntimeError, match="Failed to send message"):
            await twilio_handler._recover_realtime_session(mock_realtime_exception_event)

        # Setup completed before send_message failed
        twilio_handler._setup_realtime_session.assert_awaited_once()
        twilio_handler._enter_realtime_session.assert_awaited_once()
        twilio_handler._start_realtime_session_loop.assert_called_once()
        new_session.send_message.assert_called_once_with(self.EXPECTED_RECOVERY_MESSAGE)


class TestTwilioHandlerCleanup:
    """Test _cleanup_call method for proper resource cleanup and memory leak prevention."""

    async def test_cleanup_call_sets_call_active_false(self, twilio_handler):
        """Test cleanup sets call_active to False first to prevent further processing."""
        twilio_handler.call_active = True

        # Setup minimal mocks to avoid errors
        twilio_handler.session = None
        twilio_handler._realtime_session_task = None
        twilio_handler._buffer_flush_task = None
        twilio_handler._message_loop_task = None
        twilio_handler._inactivity_monitor_task = None
        twilio_handler._pacer_task = None
        twilio_handler.agent = None

        await twilio_handler._cleanup_call()

        assert twilio_handler.call_active is False

    async def test_cleanup_call_clears_next_filler_time(self, twilio_handler):
        """Test cleanup clears _next_filler_time to prevent filler messages."""
        twilio_handler._next_filler_time = 12345.0

        # Setup minimal mocks
        twilio_handler.session = None
        twilio_handler._realtime_session_task = None
        twilio_handler._buffer_flush_task = None
        twilio_handler._message_loop_task = None
        twilio_handler._inactivity_monitor_task = None
        twilio_handler._pacer_task = None
        twilio_handler.agent = None

        await twilio_handler._cleanup_call()

        assert twilio_handler._next_filler_time is None

    async def test_cleanup_call_stops_pacer_and_clears_queues(self, twilio_handler):
        """Test cleanup stops pacer and clears all audio queues."""
        twilio_handler._pacer_running = True
        twilio_handler._out_frame_q.append((b"x" * 320, ("mark1", "item1", 0)))
        twilio_handler._out_frame_q.append((b"y" * 320, ("mark2", "item2", 0)))
        twilio_handler._out_partial.extend(b"partial_data")
        twilio_handler._current_partial_event = ("mark3", "item3", 0)
        twilio_handler._first_ulaw_rx_ts = 12345.0
        twilio_handler._audio_buffer.extend(b"audio_data")

        # Setup minimal mocks
        twilio_handler.session = None
        twilio_handler._realtime_session_task = None
        twilio_handler._buffer_flush_task = None
        twilio_handler._message_loop_task = None
        twilio_handler._inactivity_monitor_task = None
        twilio_handler._pacer_task = None
        twilio_handler.agent = None

        await twilio_handler._cleanup_call()

        assert twilio_handler._pacer_running is False
        assert len(twilio_handler._out_frame_q) == 0
        assert len(twilio_handler._out_partial) == 0
        assert twilio_handler._current_partial_event is None
        assert twilio_handler._first_ulaw_rx_ts is None
        assert len(twilio_handler._audio_buffer) == 0

    async def test_cleanup_call_clears_mark_data(self, twilio_handler):
        """Test cleanup clears _mark_data to prevent memory leak."""
        twilio_handler._mark_data = {
            "1": ("item1", 0, 100),
            "2": ("item2", 1, 200),
            "3": ("item3", 2, 300),
        }

        # Setup minimal mocks
        twilio_handler.session = None
        twilio_handler._realtime_session_task = None
        twilio_handler._buffer_flush_task = None
        twilio_handler._message_loop_task = None
        twilio_handler._inactivity_monitor_task = None
        twilio_handler._pacer_task = None
        twilio_handler.agent = None

        await twilio_handler._cleanup_call()

        assert len(twilio_handler._mark_data) == 0

    async def test_cleanup_call_clears_history(self, twilio_handler):
        """Test cleanup clears history list to prevent memory leak."""
        twilio_handler.history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        # Setup minimal mocks
        twilio_handler.session = None
        twilio_handler._realtime_session_task = None
        twilio_handler._buffer_flush_task = None
        twilio_handler._message_loop_task = None
        twilio_handler._inactivity_monitor_task = None
        twilio_handler._pacer_task = None
        twilio_handler.agent = None

        await twilio_handler._cleanup_call()

        assert len(twilio_handler.history) == 0

    async def test_cleanup_call_closes_session(self, twilio_handler, mock_realtime_session):
        """Test cleanup closes the realtime session."""
        mock_realtime_session.close = AsyncMock()
        twilio_handler.session = mock_realtime_session

        # Setup minimal mocks
        twilio_handler._realtime_session_task = None
        twilio_handler._buffer_flush_task = None
        twilio_handler._message_loop_task = None
        twilio_handler._inactivity_monitor_task = None
        twilio_handler._pacer_task = None
        twilio_handler.agent = None

        await twilio_handler._cleanup_call()

        mock_realtime_session.close.assert_called_once()
        assert twilio_handler.session is None

    async def test_cleanup_call_handles_session_close_exception(self, twilio_handler, mock_realtime_session):
        """Test cleanup handles exceptions when closing session."""
        mock_realtime_session.close = AsyncMock(side_effect=Exception("Session close failed"))
        twilio_handler.session = mock_realtime_session

        # Setup minimal mocks
        twilio_handler._realtime_session_task = None
        twilio_handler._buffer_flush_task = None
        twilio_handler._message_loop_task = None
        twilio_handler._inactivity_monitor_task = None
        twilio_handler._pacer_task = None
        twilio_handler.agent = None

        # Should not raise exception
        await twilio_handler._cleanup_call()

        # Session should still be set to None even after exception
        assert twilio_handler.session is None

    async def test_cleanup_call_cancels_and_awaits_tasks(self, twilio_handler):
        """Test cleanup cancels and awaits all async tasks."""

        # Create mock tasks that track cancellation
        async def dummy_coro():
            await asyncio.sleep(10)

        realtime_task = asyncio.create_task(dummy_coro())
        buffer_task = asyncio.create_task(dummy_coro())
        message_task = asyncio.create_task(dummy_coro())
        inactivity_task = asyncio.create_task(dummy_coro())

        twilio_handler._realtime_session_task = realtime_task
        twilio_handler._buffer_flush_task = buffer_task
        twilio_handler._message_loop_task = message_task
        twilio_handler._inactivity_monitor_task = inactivity_task

        # Setup minimal mocks
        twilio_handler.session = None
        twilio_handler._pacer_task = None
        twilio_handler.agent = None

        await twilio_handler._cleanup_call()

        # All tasks should be cancelled
        assert realtime_task.cancelled()
        assert buffer_task.cancelled()
        assert message_task.cancelled()
        assert inactivity_task.cancelled()

    async def test_cleanup_call_handles_task_already_done(self, twilio_handler):
        """Test cleanup handles tasks that are already done."""

        # Create a task that completes immediately
        async def quick_coro():
            return "done"

        done_task = asyncio.create_task(quick_coro())
        await done_task  # Wait for it to complete

        twilio_handler._realtime_session_task = done_task
        twilio_handler._buffer_flush_task = None
        twilio_handler._message_loop_task = None
        twilio_handler._inactivity_monitor_task = None

        # Setup minimal mocks
        twilio_handler.session = None
        twilio_handler._pacer_task = None
        twilio_handler.agent = None

        # Should not raise exception
        await twilio_handler._cleanup_call()

    async def test_cleanup_call_handles_task_cancellation_exception(self, twilio_handler):
        """Test cleanup handles exceptions during task cancellation."""

        # Create a task that raises an exception when cancelled
        async def error_coro():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                raise RuntimeError("Cancellation error")

        error_task = asyncio.create_task(error_coro())
        twilio_handler._realtime_session_task = error_task
        twilio_handler._buffer_flush_task = None
        twilio_handler._message_loop_task = None
        twilio_handler._inactivity_monitor_task = None

        # Setup minimal mocks
        twilio_handler.session = None
        twilio_handler._pacer_task = None
        twilio_handler.agent = None

        # Should not raise exception, cleanup should continue
        await twilio_handler._cleanup_call()

    async def test_cleanup_call_calls_agent_aexit(self, twilio_handler):
        """Test cleanup calls agent.__aexit__ to close MCP connections."""
        mock_agent = Mock()
        mock_agent.__aexit__ = AsyncMock()
        twilio_handler.agent = mock_agent

        # Setup minimal mocks
        twilio_handler.session = None
        twilio_handler._realtime_session_task = None
        twilio_handler._buffer_flush_task = None
        twilio_handler._message_loop_task = None
        twilio_handler._inactivity_monitor_task = None
        twilio_handler._pacer_task = None

        await twilio_handler._cleanup_call()

        mock_agent.__aexit__.assert_called_once_with(None, None, None)
        assert twilio_handler.agent is None

    async def test_cleanup_call_handles_agent_aexit_exception(self, twilio_handler):
        """Test cleanup handles exceptions when closing agent."""
        mock_agent = Mock()
        mock_agent.__aexit__ = AsyncMock(side_effect=Exception("Agent cleanup failed"))
        twilio_handler.agent = mock_agent

        # Setup minimal mocks
        twilio_handler.session = None
        twilio_handler._realtime_session_task = None
        twilio_handler._buffer_flush_task = None
        twilio_handler._message_loop_task = None
        twilio_handler._inactivity_monitor_task = None
        twilio_handler._pacer_task = None

        # Should not raise exception
        await twilio_handler._cleanup_call()

        # Agent should still be set to None even after exception
        assert twilio_handler.agent is None

    async def test_cleanup_call_clears_context_history(self, twilio_handler, resident_context_voice_knck):
        """Test cleanup clears ctx.history to break circular references."""
        twilio_handler.ctx = resident_context_voice_knck
        twilio_handler.ctx.history = [{"role": "user", "content": "test"}]
        twilio_handler.ctx.mcp_tool_calls = [{"tool": "test_tool"}]

        # Setup minimal mocks
        twilio_handler.session = None
        twilio_handler._realtime_session_task = None
        twilio_handler._buffer_flush_task = None
        twilio_handler._message_loop_task = None
        twilio_handler._inactivity_monitor_task = None
        twilio_handler._pacer_task = None
        twilio_handler.agent = None

        await twilio_handler._cleanup_call()

        assert twilio_handler.ctx.history == []
        assert twilio_handler.ctx.mcp_tool_calls == []

    async def test_cleanup_call_handles_missing_ctx(self, twilio_handler):
        """Test cleanup handles case when ctx doesn't exist."""
        # Remove ctx attribute if it exists
        if hasattr(twilio_handler, "ctx"):
            delattr(twilio_handler, "ctx")

        # Setup minimal mocks
        twilio_handler.session = None
        twilio_handler._realtime_session_task = None
        twilio_handler._buffer_flush_task = None
        twilio_handler._message_loop_task = None
        twilio_handler._inactivity_monitor_task = None
        twilio_handler._pacer_task = None
        twilio_handler.agent = None

        # Should not raise exception
        await twilio_handler._cleanup_call()

    async def test_cleanup_call_cancels_pacer_task(self, twilio_handler):
        """Test cleanup cancels and awaits pacer task."""

        async def pacer_coro():
            await asyncio.sleep(10)

        pacer_task = asyncio.create_task(pacer_coro())
        twilio_handler._pacer_task = pacer_task
        twilio_handler._pacer_running = True

        # Setup minimal mocks
        twilio_handler.session = None
        twilio_handler._realtime_session_task = None
        twilio_handler._buffer_flush_task = None
        twilio_handler._message_loop_task = None
        twilio_handler._inactivity_monitor_task = None
        twilio_handler.agent = None

        await twilio_handler._cleanup_call()

        assert pacer_task.cancelled()
        assert twilio_handler._pacer_running is False

    async def test_cleanup_call_full_cleanup_sequence(
        self, twilio_handler, mock_realtime_session, resident_context_voice_knck
    ):
        """Test full cleanup sequence with all components."""
        # Setup all components
        mock_realtime_session.close = AsyncMock()
        twilio_handler.session = mock_realtime_session

        mock_agent = Mock()
        mock_agent.__aexit__ = AsyncMock()
        twilio_handler.agent = mock_agent

        twilio_handler.ctx = resident_context_voice_knck
        twilio_handler.ctx.history = [{"role": "user", "content": "test"}]
        twilio_handler.ctx.mcp_tool_calls = [{"tool": "test_tool"}]
        twilio_handler.ctx.langsmith_run_tree = {"test": "headers"}

        # Setup data structures
        twilio_handler.call_active = True
        twilio_handler._next_filler_time = 12345.0
        twilio_handler._pacer_running = True
        twilio_handler._out_frame_q.append((b"x" * 320, ("mark1", "item1", 0)))
        twilio_handler._out_partial.extend(b"partial")
        twilio_handler._audio_buffer.extend(b"audio")
        twilio_handler._mark_data = {"1": ("item1", 0, 100)}
        twilio_handler.history = [{"role": "user", "content": "hello"}]

        # Create tasks
        async def dummy_coro():
            await asyncio.sleep(10)

        twilio_handler._realtime_session_task = asyncio.create_task(dummy_coro())
        twilio_handler._buffer_flush_task = asyncio.create_task(dummy_coro())
        twilio_handler._message_loop_task = asyncio.create_task(dummy_coro())
        twilio_handler._inactivity_monitor_task = asyncio.create_task(dummy_coro())
        twilio_handler._pacer_task = asyncio.create_task(dummy_coro())

        await twilio_handler._cleanup_call()

        # Verify all cleanup happened
        assert twilio_handler.call_active is False
        assert twilio_handler._next_filler_time is None
        assert twilio_handler._pacer_running is False
        assert len(twilio_handler._out_frame_q) == 0
        assert len(twilio_handler._out_partial) == 0
        assert len(twilio_handler._audio_buffer) == 0
        assert len(twilio_handler._mark_data) == 0
        assert len(twilio_handler.history) == 0
        assert twilio_handler.session is None
        assert twilio_handler.agent is None
        assert twilio_handler.ctx.history == []
        assert twilio_handler.ctx.mcp_tool_calls == []

        mock_realtime_session.close.assert_called_once()
        mock_agent.__aexit__.assert_called_once_with(None, None, None)

    async def test_cleanup_call_skips_current_task(self, twilio_handler):
        """Test cleanup does not cancel/await the current running task."""
        twilio_handler.session = None
        twilio_handler._realtime_session_task = None
        twilio_handler._buffer_flush_task = None
        twilio_handler._inactivity_monitor_task = None
        twilio_handler._pacer_task = None
        twilio_handler.agent = None

        twilio_handler._message_loop_task = asyncio.current_task()

        # Should not raise exception
        await twilio_handler._cleanup_call()

    async def test_cleanup_call_idempotent(self, twilio_handler):
        """Test cleanup can be called multiple times safely (idempotent)."""
        # Setup minimal mocks
        twilio_handler.session = None
        twilio_handler._realtime_session_task = None
        twilio_handler._buffer_flush_task = None
        twilio_handler._message_loop_task = None
        twilio_handler._inactivity_monitor_task = None
        twilio_handler._pacer_task = None
        twilio_handler.agent = None

        # Call cleanup multiple times
        await twilio_handler._cleanup_call()
        await twilio_handler._cleanup_call()
        await twilio_handler._cleanup_call()

        # Should not raise any exceptions

    async def test_cleanup_call_with_none_tasks(self, twilio_handler):
        """Test cleanup handles None tasks gracefully."""
        twilio_handler._realtime_session_task = None
        twilio_handler._buffer_flush_task = None
        twilio_handler._message_loop_task = None
        twilio_handler._inactivity_monitor_task = None
        twilio_handler._pacer_task = None
        twilio_handler.session = None
        twilio_handler.agent = None

        # Should not raise exception
        await twilio_handler._cleanup_call()


class TestTwilioHandlerMarkDataBounding:
    """Test _mark_data bounding to prevent unbounded growth."""

    async def test_mark_data_bounded_at_max_size(self, twilio_handler):
        """Test that _mark_data is bounded at max_size."""
        twilio_handler._stream_sid = "test-stream"
        twilio_handler.call_active = True

        # Fill _mark_data to just under the limit, starting after mark_counter
        # to avoid collision with new marks
        base_id = 1000  # Start high to avoid collision
        for i in range(twilio_handler._mark_data_max_size - 1):
            twilio_handler._mark_data[str(base_id + i)] = (f"item{i}", 0, 100)

        # Add one more mark via audio event
        audio_data = b"x" * 320
        event = Mock()
        event.type = "audio"
        event.audio = Mock()
        event.audio.data = audio_data
        event.audio.item_id = "test-item"
        event.audio.content_index = 0

        await twilio_handler._handle_realtime_event(event)

        # Should be at max size now
        assert len(twilio_handler._mark_data) == twilio_handler._mark_data_max_size

    async def test_mark_data_clears_oldest_when_full(self, twilio_handler):
        """Test that oldest marks are cleared when _mark_data is full."""
        twilio_handler._stream_sid = "test-stream"
        twilio_handler.call_active = True

        # Fill _mark_data to the limit
        for i in range(twilio_handler._mark_data_max_size):
            twilio_handler._mark_data[str(i)] = (f"item{i}", 0, 100)
        twilio_handler._mark_counter = twilio_handler._mark_data_max_size

        # Add one more mark via audio event
        audio_data = b"x" * 320
        event = Mock()
        event.type = "audio"
        event.audio = Mock()
        event.audio.data = audio_data
        event.audio.item_id = "new-item"
        event.audio.content_index = 0

        await twilio_handler._handle_realtime_event(event)

        # Should have cleared half and added one
        expected_size = (twilio_handler._mark_data_max_size // 2) + 1
        assert len(twilio_handler._mark_data) == expected_size

        # Oldest marks (0-499) should be gone, newest mark should exist
        assert "0" not in twilio_handler._mark_data
        assert str(twilio_handler._mark_counter) in twilio_handler._mark_data


class TestTwilioHandlerPacer:
    """Test audio pacer functionality."""

    async def test_pacer_loop_handles_websocket_disconnect(self, twilio_handler):
        """Test pacer loop gracefully handles WebSocketDisconnect."""
        twilio_handler._stream_sid = "test-stream"
        twilio_handler.call_active = True
        twilio_handler._pacer_running = True

        # Add enough frames to skip prebuffer wait (12 frames needed)
        for i in range(12):
            twilio_handler._out_frame_q.append((b"x" * 320, (f"mark{i}", f"item{i}", 0)))

        # Make send_text raise WebSocketDisconnect
        twilio_handler.twilio_websocket.send_text = AsyncMock(side_effect=WebSocketDisconnect(code=1006))

        # Start pacer loop
        pacer_task = asyncio.create_task(twilio_handler._pacer_loop())

        # Wait for the loop to process (pacer will skip prebuffer and try to send)
        await asyncio.sleep(0.1)

        # Cancel the task
        pacer_task.cancel()
        try:
            await pacer_task
        except asyncio.CancelledError:
            pass

        # Should have attempted to send
        assert twilio_handler.twilio_websocket.send_text.called
        # Pacer should be stopped
        assert twilio_handler._pacer_running is False

    async def test_send_mark_handles_websocket_disconnect(self, twilio_handler):
        """Test _send_mark gracefully handles WebSocketDisconnect."""
        twilio_handler._stream_sid = "test-stream"
        twilio_handler.call_active = True
        twilio_handler._pacer_running = True

        # Mock client_state to be CONNECTED
        mock_client_state = Mock()
        mock_client_state.name = "CONNECTED"
        twilio_handler.twilio_websocket.client_state = mock_client_state

        # Make send_text raise WebSocketDisconnect
        twilio_handler.twilio_websocket.send_text = AsyncMock(side_effect=WebSocketDisconnect(code=1006))

        # Should not raise exception
        await twilio_handler._send_mark("test-mark")

        # Should have attempted to send
        assert twilio_handler.twilio_websocket.send_text.called

    async def test_send_mark_skips_when_not_connected(self, twilio_handler):
        """Test _send_mark skips when websocket is not connected."""
        twilio_handler._stream_sid = "test-stream"
        twilio_handler.call_active = True
        twilio_handler._pacer_running = True

        # Mock client_state to be DISCONNECTED
        mock_client_state = Mock()
        mock_client_state.name = "DISCONNECTED"
        twilio_handler.twilio_websocket.client_state = mock_client_state

        await twilio_handler._send_mark("test-mark")

        # Should not attempt to send
        assert not twilio_handler.twilio_websocket.send_text.called

    async def test_send_mark_skips_when_no_mark_id(self, twilio_handler):
        """Test _send_mark skips when mark_id is None or empty."""
        twilio_handler._stream_sid = "test-stream"
        twilio_handler.call_active = True
        twilio_handler._pacer_running = True

        await twilio_handler._send_mark(None)
        await twilio_handler._send_mark("")

        # Should not attempt to send
        assert not twilio_handler.twilio_websocket.send_text.called

    async def test_handle_realtime_audio_event_partial_frame(self, twilio_handler):
        """Test handling audio event with partial frame that spans multiple events."""
        twilio_handler._stream_sid = "test-stream"
        twilio_handler.call_active = True

        # First event with partial frame (less than 160 bytes)
        audio_data1 = b"x" * 100
        event1 = Mock()
        event1.type = "audio"
        event1.audio = Mock()
        event1.audio.data = audio_data1
        event1.audio.item_id = "item1"
        event1.audio.content_index = 0

        await twilio_handler._handle_realtime_event(event1)

        # Should not have complete frames yet
        assert len(twilio_handler._out_frame_q) == 0
        assert len(twilio_handler._out_partial) == 100

        # Second event that completes the frame
        audio_data2 = b"y" * 100
        event2 = Mock()
        event2.type = "audio"
        event2.audio = Mock()
        event2.audio.data = audio_data2
        event2.audio.item_id = "item2"
        event2.audio.content_index = 0

        await twilio_handler._handle_realtime_event(event2)

        # Should have one complete frame (160 bytes)
        # and 40 bytes remaining in partial
        assert len(twilio_handler._out_frame_q) == 1
        frame, frame_event = twilio_handler._out_frame_q[0]
        assert len(frame) == 160
        # When event2 arrives, _current_partial_event is updated to event2's metadata
        # So the frame sliced from the combined data (100 from event1 + 100 from event2)
        # gets tagged with event2's metadata
        mark_id, item_id, content_index = frame_event
        assert item_id == "item2"
        assert len(twilio_handler._out_partial) == 40


class TestTwilioHandlerWSSURLConfiguration:
    """Test WebSocket URL configuration for data residency."""

    @patch("agent_leasing.twilio_handler.trace")
    @patch("agent_leasing.twilio_handler.agent_selector")
    @patch("agent_leasing.twilio_handler.RealtimeRunner")
    async def test_model_config_uses_custom_wss_url_when_set(
        self,
        mock_realtime_runner,
        mock_agent_selector,
        mock_trace,
        twilio_handler,
        mock_realtime_session,
    ):
        """Test that model_config uses custom WSS URL from settings."""
        test_payload = examples.ASK_REQUEST_RESIDENT_VOICE_KNCK

        twilio_handler.root_run = Mock()
        twilio_handler.root_run.to_headers.return_value = {"x-test-langsmith": "1"}

        # Setup mocks
        mock_agent = Mock()
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.agent_instance = Mock()
        mock_agent_selector.return_value = mock_agent

        mock_runner = Mock()
        mock_runner.run = AsyncMock(return_value=mock_realtime_session)
        mock_realtime_runner.return_value = mock_runner

        # Set custom WSS URL
        original_wss_url = settings.openai_base_wss_url
        try:
            settings.openai_base_wss_url = "wss://us.api.openai.com/v1/realtime"

            # Call agent setup which should configure model_config
            await twilio_handler._agent_setup(test_payload)

            # Verify model_config has the custom URL
            assert twilio_handler.model_config["url"] == "wss://us.api.openai.com/v1/realtime?model=gpt-realtime-2"
        finally:
            settings.openai_base_wss_url = original_wss_url

    @patch("agent_leasing.twilio_handler.trace")
    @patch("agent_leasing.twilio_handler.agent_selector")
    @patch("agent_leasing.twilio_handler.RealtimeRunner")
    async def test_model_config_uses_default_when_wss_url_not_set(
        self,
        mock_realtime_runner,
        mock_agent_selector,
        mock_trace,
        twilio_handler,
        mock_realtime_session,
    ):
        """Test that model_config uses default URL when WSS URL is not set."""
        test_payload = examples.ASK_REQUEST_RESIDENT_VOICE_KNCK

        twilio_handler.root_run = Mock()
        twilio_handler.root_run.to_headers.return_value = {"x-test-langsmith": "1"}

        # Setup mocks
        mock_agent = Mock()
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.agent_instance = Mock()
        mock_agent_selector.return_value = mock_agent

        mock_runner = Mock()
        mock_runner.run = AsyncMock(return_value=mock_realtime_session)
        mock_realtime_runner.return_value = mock_runner

        # Ensure WSS URL is not set
        original_wss_url = settings.openai_base_wss_url
        try:
            settings.openai_base_wss_url = ""

            # Call agent setup which should configure model_config
            await twilio_handler._agent_setup(test_payload)

            # Verify model_config does not have custom URL
            assert "url" not in twilio_handler.model_config or twilio_handler.model_config["url"] == ""
        finally:
            settings.openai_base_wss_url = original_wss_url


class TestValidationFailureHandling:
    """Test validation failure handling with call transfer."""

    async def test_agent_setup_transfers_on_validation_error(self, twilio_handler):
        """Test that validation failure triggers transfer."""
        twilio_handler._call_sid = "test-call-sid-123"
        invalid_payload = {"product": "invalid_product"}

        with patch.object(twilio_handler, "_transfer_call_on_validation_failure", new=AsyncMock()) as mock_transfer:
            await twilio_handler._agent_setup(invalid_payload)

            mock_transfer.assert_called_once()
            # Verify error and payload were passed
            call_args = mock_transfer.call_args[0]
            assert isinstance(call_args[0], Exception)  # error
            assert call_args[1] == invalid_payload  # payload
            assert twilio_handler.call_active is False

    async def test_agent_setup_transfers_on_value_error(self, twilio_handler):
        """Test that ValueError from model validator triggers transfer."""
        twilio_handler._call_sid = "test-call-sid-456"
        payload_missing_fields = {
            "product": "resident_one_voice",
            "request_type": "voice",
            "prompt": "",
            "product_info": {"call_sid": "test-call-sid-456"},
        }

        with patch.object(twilio_handler, "_transfer_call_on_validation_failure", new=AsyncMock()) as mock_transfer:
            await twilio_handler._agent_setup(payload_missing_fields)

            mock_transfer.assert_called_once()
            assert twilio_handler.call_active is False

    async def test_transfer_call_on_validation_failure_calls_twilio(self, twilio_handler):
        """Test that transfer function calls Twilio API correctly."""
        twilio_handler._call_sid = "test-call-sid-789"
        error = ValueError("test error")
        payload = {"product": "test", "product_info": {"property_name": "Test"}}

        mock_call = Mock()
        mock_call.status = "in-progress"
        mock_twilio_client = Mock()
        mock_twilio_client.calls.return_value.update.return_value = mock_call

        with (
            patch(
                "agent_leasing.twilio_handler.get_twilio_credentials",
                return_value=("api_key", "api_secret", "account_sid"),
            ),
            patch("agent_leasing.twilio_handler.TwilioClient", return_value=mock_twilio_client),
            patch("agent_leasing.twilio_handler._build_transfer_twiml", return_value="<Response></Response>"),
        ):
            await twilio_handler._transfer_call_on_validation_failure(error, payload)

            mock_twilio_client.calls.assert_called_once_with("test-call-sid-789")
            mock_twilio_client.calls.return_value.update.assert_called_once()

    async def test_transfer_call_on_validation_failure_handles_missing_call_sid(self, twilio_handler):
        """Test that transfer gracefully handles missing call_sid."""
        twilio_handler._call_sid = None
        error = ValueError("test error")
        payload = {"product": "test"}

        with patch("agent_leasing.twilio_handler.get_twilio_credentials") as mock_get_creds:
            await twilio_handler._transfer_call_on_validation_failure(error, payload)
            mock_get_creds.assert_not_called()

    async def test_transfer_call_on_validation_failure_posts_trace_marker(self, twilio_handler):
        """Validation failure posts a `validation_failure` trace marker with source signals (issue #1567)."""
        twilio_handler._call_sid = "CAtest123"
        twilio_handler.variant = "v1"
        mock_root_run = Mock()
        mock_child = Mock()
        mock_root_run.create_child.return_value = mock_child
        twilio_handler.root_run = mock_root_run

        error = ValueError(
            "Value error, Missing required fields for resident persona: "
            "product_info.uc_company_id, product_info.uc_property_id [type=value_error]"
        )
        payload = {
            "product": "resident_one_voice",
            "call_sid": "CAtest123",
            "product_info": {
                "call_sid": "CAtest123",
                "caller": "+15551234567",
                "account_sid": "ACtest",
                "property_name": "Test",
            },
        }

        with (
            patch("agent_leasing.twilio_handler.get_twilio_credentials", return_value=("k", "s", "a")),
            patch("agent_leasing.twilio_handler.TwilioClient"),
            patch("agent_leasing.twilio_handler._build_transfer_twiml", return_value=""),
        ):
            await twilio_handler._transfer_call_on_validation_failure(error, payload)

        mock_root_run.create_child.assert_called_once()
        kwargs = mock_root_run.create_child.call_args.kwargs
        assert kwargs["name"] == "validation_failure"
        inputs = kwargs["inputs"]
        assert inputs["validation_reason"] == "missing_required_fields"
        assert inputs["missing_fields"] == ["product_info.uc_company_id", "product_info.uc_property_id"]
        assert inputs["call_sid"] == "CAtest123"
        assert inputs["caller"] == "+15551234567"
        assert inputs["account_sid"] == "ACtest"
        assert inputs["product"] == "resident_one_voice"
        assert "property_name" in inputs["product_info_keys"]
        assert inputs["voice_handler_variant"] == "v1"
        mock_child.post.assert_called_once()


class TestFormatGuardrailOutput:
    """Tests for _format_guardrail_output handling different guardrail output types."""

    @pytest.mark.parametrize(
        ("output_info", "expected_substring"),
        [
            # Dict with pii_types_found (legacy PII guardrail format)
            (
                {"pii_types_found": ["email"], "reasoning": "Contains email address"},
                "Contains email address",
            ),
            # Dict with only reasoning
            (
                {"reasoning": "Some reasoning text"},
                "Some reasoning text",
            ),
            # Dict with safe_response only
            (
                {"safe_response": "I cannot help with that."},
                "I cannot help with that.",
            ),
        ],
    )
    def test_format_guardrail_output_dict(self, output_info, expected_substring):
        guardrail_result = Mock()
        guardrail_result.output.output_info = output_info
        result = TwilioHandler._format_guardrail_output(guardrail_result)
        assert expected_substring in result

    @pytest.mark.parametrize(
        ("reasoning", "safe_response", "expected_substring"),
        [
            (
                "Response contained unauthorized promise",
                "I'm not authorized to make that commitment.",
                "Response contained unauthorized promise",
            ),
            (
                None,
                "I'm not authorized to make that commitment.",
                "I'm not authorized to make that commitment.",
            ),
        ],
    )
    def test_format_guardrail_output_pydantic_model(self, reasoning, safe_response, expected_substring):
        from pydantic import BaseModel

        class FakeGuardrailOutput(BaseModel):
            reasoning: str | None = None
            safe_response: str = ""
            is_promise: bool = True

        output_info = FakeGuardrailOutput(reasoning=reasoning, safe_response=safe_response)
        guardrail_result = Mock()
        guardrail_result.output.output_info = output_info
        result = TwilioHandler._format_guardrail_output(guardrail_result)
        assert expected_substring in result


class TestSessionCleanupOnDisconnect:
    """Test defensive session cleanup: WebSocket disconnect, session expiry, and filler guards."""

    async def test_message_loop_cleanup_on_disconnect(self, twilio_handler):
        """Test that _twilio_message_loop triggers cleanup when WebSocket disconnects."""
        twilio_handler.twilio_websocket.receive_text = AsyncMock(side_effect=WebSocketDisconnect(code=1006))
        twilio_handler._cleanup_call = AsyncMock()

        with patch("agent_leasing.twilio_handler.ls.trace") as mock_ls_trace:
            run = Mock()
            mock_ls_trace.return_value.__enter__.return_value = run

            await twilio_handler._twilio_message_loop()

        twilio_handler._cleanup_call.assert_awaited_once()

    async def test_inactivity_loop_cleanup_on_websocket_disconnect(self, twilio_handler):
        """Test that _input_audio_inactivity_loop triggers cleanup when WebSocket disconnects."""
        twilio_handler.call_active = True
        twilio_handler._session_ready.set()
        twilio_handler._cleanup_call = AsyncMock()

        # WebSocket is disconnected
        mock_state = Mock()
        mock_state.name = "DISCONNECTED"
        twilio_handler.twilio_websocket.client_state = mock_state

        # Run one iteration then stop
        original_cleanup = twilio_handler._cleanup_call

        async def stop_after_cleanup():
            twilio_handler.call_active = False
            await original_cleanup()

        twilio_handler._cleanup_call = AsyncMock(side_effect=stop_after_cleanup)

        await twilio_handler._input_audio_inactivity_loop()

        twilio_handler._cleanup_call.assert_awaited_once()

    async def test_filler_guard_skips_when_websocket_disconnected(self, twilio_handler, mock_realtime_session):
        """Test that _send_input_audio_timeout_message skips when WebSocket is disconnected."""
        twilio_handler.session = mock_realtime_session
        twilio_handler.agent = Mock()
        twilio_handler._session_ready.set()
        twilio_handler.call_active = True

        # WebSocket is disconnected
        mock_state = Mock()
        mock_state.name = "DISCONNECTED"
        twilio_handler.twilio_websocket.client_state = mock_state

        await twilio_handler._send_input_audio_timeout_message()

        # Should not send any message
        mock_realtime_session.send_message.assert_not_called()

    def test_is_websocket_connected_true(self, twilio_handler):
        """Test _is_websocket_connected returns True when connected."""
        mock_state = Mock()
        mock_state.name = "CONNECTED"
        twilio_handler.twilio_websocket.client_state = mock_state
        assert twilio_handler._is_websocket_connected() is True

    def test_is_websocket_connected_false(self, twilio_handler):
        """Test _is_websocket_connected returns False when disconnected."""
        mock_state = Mock()
        mock_state.name = "DISCONNECTED"
        twilio_handler.twilio_websocket.client_state = mock_state
        assert twilio_handler._is_websocket_connected() is False

    def test_is_websocket_connected_no_client_state(self, twilio_handler):
        """Test _is_websocket_connected returns False when client_state missing."""
        del twilio_handler.twilio_websocket.client_state
        assert twilio_handler._is_websocket_connected() is False

    def test_is_session_expired_false_before_timeout(self, twilio_handler):
        """Test _is_session_expired returns False when session is within duration."""
        twilio_handler._session_start_time = time.time()
        assert twilio_handler._is_session_expired() is False

    def test_is_session_expired_true_after_timeout(self, twilio_handler):
        """Test _is_session_expired returns True when session exceeds max duration."""
        twilio_handler._session_start_time = time.time() - (settings.max_voice_session_duration_seconds + 1)
        assert twilio_handler._is_session_expired() is True

    def test_is_session_expired_false_when_no_start_time(self, twilio_handler):
        """Test _is_session_expired returns False when session hasn't started."""
        twilio_handler._session_start_time = None
        assert twilio_handler._is_session_expired() is False

    async def test_inactivity_loop_cleanup_on_session_expired(self, twilio_handler):
        """Test that _input_audio_inactivity_loop triggers cleanup when session expires."""
        twilio_handler.call_active = True
        twilio_handler._session_ready.set()
        twilio_handler._session_start_time = time.time() - (settings.max_voice_session_duration_seconds + 1)
        twilio_handler._cleanup_call = AsyncMock()

        original_cleanup = twilio_handler._cleanup_call

        async def stop_after_cleanup():
            twilio_handler.call_active = False
            await original_cleanup()

        twilio_handler._cleanup_call = AsyncMock(side_effect=stop_after_cleanup)

        await twilio_handler._input_audio_inactivity_loop()

        twilio_handler._cleanup_call.assert_awaited_once()


class TestDeadLineDetection:
    """Test dead line detection: filler counter tracking and threshold-based cleanup."""

    def test_counter_initialized_to_zero(self, twilio_handler):
        """Test that consecutive filler counter starts at zero."""
        assert twilio_handler._consecutive_fillers_without_user_audio == 0

    async def test_counter_reset_on_user_speech(self, twilio_handler):
        """Test that counter resets when user starts speaking."""
        twilio_handler._consecutive_fillers_without_user_audio = 3
        twilio_handler._stream_sid = "test-stream"

        event = Mock()
        event.type = "audio_interrupted"
        twilio_handler._is_initial_greeting = False

        await twilio_handler._handle_realtime_event(event)

        assert twilio_handler._consecutive_fillers_without_user_audio == 0
        assert twilio_handler._last_user_audio_time is not None

    async def test_counter_increments_on_filler(self, twilio_handler, mock_realtime_session):
        """Test that counter increments when filler message is sent."""
        twilio_handler.session = mock_realtime_session
        twilio_handler.agent = Mock()
        twilio_handler._session_ready.set()
        twilio_handler.call_active = True

        # WebSocket is connected
        mock_state = Mock()
        mock_state.name = "CONNECTED"
        twilio_handler.twilio_websocket.client_state = mock_state

        assert twilio_handler._consecutive_fillers_without_user_audio == 0

        await twilio_handler._send_input_audio_timeout_message()
        assert twilio_handler._consecutive_fillers_without_user_audio == 1

        await twilio_handler._send_input_audio_timeout_message()
        assert twilio_handler._consecutive_fillers_without_user_audio == 2

    def test_is_dead_line_false_below_threshold(self, twilio_handler):
        """Test _is_dead_line returns False below threshold."""
        twilio_handler._consecutive_fillers_without_user_audio = (
            settings.max_consecutive_fillers_without_user_audio - 1
        )
        assert twilio_handler._is_dead_line() is False

    def test_is_dead_line_true_at_threshold(self, twilio_handler):
        """Test _is_dead_line returns True at threshold."""
        twilio_handler._consecutive_fillers_without_user_audio = settings.max_consecutive_fillers_without_user_audio
        assert twilio_handler._is_dead_line() is True

    def test_is_dead_line_true_above_threshold(self, twilio_handler):
        """Test _is_dead_line returns True above threshold."""
        twilio_handler._consecutive_fillers_without_user_audio = (
            settings.max_consecutive_fillers_without_user_audio + 1
        )
        assert twilio_handler._is_dead_line() is True

    async def test_inactivity_loop_cleanup_on_dead_line(self, twilio_handler):
        """Test that _input_audio_inactivity_loop triggers cleanup on dead line."""
        twilio_handler.call_active = True
        twilio_handler._session_ready.set()
        twilio_handler._consecutive_fillers_without_user_audio = settings.max_consecutive_fillers_without_user_audio
        twilio_handler._cleanup_call = AsyncMock()

        original_cleanup = twilio_handler._cleanup_call

        async def stop_after_cleanup():
            twilio_handler.call_active = False
            await original_cleanup()

        twilio_handler._cleanup_call = AsyncMock(side_effect=stop_after_cleanup)

        await twilio_handler._input_audio_inactivity_loop()

        twilio_handler._cleanup_call.assert_awaited_once()


class TestInjectMessageFillerFlagReset:
    """Test that _inject_message resets _next_speech_is_filler before sending."""

    async def test_inject_resets_filler_flag(self, twilio_handler, mock_realtime_session):
        """A stale _next_speech_is_filler=True is cleared before the inject sends."""
        twilio_handler.session = mock_realtime_session
        twilio_handler._next_speech_is_filler = True

        await twilio_handler._inject_message("say the transfer message")

        assert twilio_handler._next_speech_is_filler is False
        mock_realtime_session.send_message.assert_awaited_once_with("say the transfer message")
