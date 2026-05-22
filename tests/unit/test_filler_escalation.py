"""Tests for filler message selection: thinker_running on SessionScope, three-tier filler logic,
filler deadline shutdown, and trace markers."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from agents.realtime import RealtimeSession

from agent_leasing.models.context import SessionScope
from agent_leasing.settings import settings
from agent_leasing.twilio_handler import TwilioHandler


class TestThinkerRunningOnSessionScope:
    """Test thinker_running field on SessionScope."""

    def test_default_is_false(self):
        """thinker_running defaults to False on a fresh SessionScope."""
        ctx = SessionScope()
        assert ctx.thinker_running is False

    def test_can_set_to_true(self):
        """thinker_running can be set to True."""
        ctx = SessionScope()
        ctx.thinker_running = True
        assert ctx.thinker_running is True

    def test_reset_back_to_false(self):
        """thinker_running can be toggled back to False."""
        ctx = SessionScope()
        ctx.thinker_running = True
        ctx.thinker_running = False
        assert ctx.thinker_running is False


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
def mock_websocket():
    ws = Mock()
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock()
    return ws


@pytest.fixture
def twilio_handler(mock_websocket):
    return TwilioHandler(mock_websocket)


@pytest.fixture
def mock_realtime_session():
    session = Mock(spec=RealtimeSession)
    session.send_message = AsyncMock()
    session._history = []
    return session


def _ready_handler(handler, session):
    """Put handler in a state where _send_input_audio_timeout_message will proceed."""
    handler.session = session
    handler.agent = Mock()
    handler._session_ready.set()
    handler.call_active = True
    mock_state = Mock()
    mock_state.name = "CONNECTED"
    handler.twilio_websocket.client_state = mock_state
    handler._schedule_next_filler = Mock()
    handler.ctx = SessionScope()


# -- Three-tier filler message selection --------------------------------------


class TestFillerHandoff:
    """When transfer_summary_requested is True, always send handoff filler."""

    @pytest.mark.asyncio
    async def test_handoff_sends_handoff_message(self, twilio_handler, mock_realtime_session):
        """Idle filler during handoff nudges toward transfer_to_staff_voice."""
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler.ctx.transfer_summary_requested = True
        twilio_handler._consecutive_fillers_without_user_audio = 0

        await twilio_handler._send_input_audio_timeout_message()

        sent_msg = mock_realtime_session.send_message.call_args[0][0]
        assert "transfer_to_staff_voice" in sent_msg
        assert "resident_thinker_tool" not in sent_msg

    @pytest.mark.asyncio
    async def test_handoff_overrides_escalation(self, twilio_handler, mock_realtime_session):
        """Even at escalation threshold, handoff filler takes priority."""
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler.ctx.transfer_summary_requested = True
        twilio_handler._consecutive_fillers_without_user_audio = settings.filler_escalation_threshold + 5

        await twilio_handler._send_input_audio_timeout_message()

        sent_msg = mock_realtime_session.send_message.call_args[0][0]
        assert "transfer_to_staff_voice" in sent_msg
        assert "CRITICAL" not in sent_msg
        assert "resident_thinker_tool" not in sent_msg

    @pytest.mark.asyncio
    async def test_no_handoff_normal_behavior(self, twilio_handler, mock_realtime_session):
        """When transfer_summary_requested is False, normal filler selection applies."""
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler.ctx.transfer_summary_requested = False
        twilio_handler._consecutive_fillers_without_user_audio = 0

        await twilio_handler._send_input_audio_timeout_message()

        sent_msg = mock_realtime_session.send_message.call_args[0][0]
        # Normal idle message — mentions resident_thinker_tool, not transfer
        assert "resident_thinker_tool" in sent_msg
        assert "transfer_to_staff_voice" not in sent_msg


class TestFillerThinkerActive:
    """When thinker IS running, send the 'still working' filler."""

    @pytest.mark.asyncio
    async def test_thinker_active_sends_working_message(self, twilio_handler, mock_realtime_session):
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler.ctx.thinker_running = True
        twilio_handler._consecutive_fillers_without_user_audio = 0

        await twilio_handler._send_input_audio_timeout_message()

        sent_msg = mock_realtime_session.send_message.call_args[0][0]
        assert "still working" in sent_msg
        assert "resident_thinker_tool" not in sent_msg
        assert "CRITICAL" not in sent_msg

    @pytest.mark.asyncio
    async def test_thinker_active_above_threshold_still_working(self, twilio_handler, mock_realtime_session):
        """Even above escalation threshold, thinker running = 'still working' (no escalation)."""
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler.ctx.thinker_running = True
        twilio_handler._consecutive_fillers_without_user_audio = settings.filler_escalation_threshold + 5

        await twilio_handler._send_input_audio_timeout_message()

        sent_msg = mock_realtime_session.send_message.call_args[0][0]
        assert "still working" in sent_msg
        assert "CRITICAL" not in sent_msg


class TestFillerIdle:
    """When thinker is NOT running and below threshold, send idle message with soft nudge."""

    @pytest.mark.asyncio
    async def test_idle_sends_nudge_message(self, twilio_handler, mock_realtime_session):
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler._consecutive_fillers_without_user_audio = 0

        await twilio_handler._send_input_audio_timeout_message()

        sent_msg = mock_realtime_session.send_message.call_args[0][0]
        # Soft nudge present
        assert "resident_thinker_tool" in sent_msg
        # Not escalation
        assert "CRITICAL" not in sent_msg
        # Still here fallback present
        assert "still here" in sent_msg.lower()

    @pytest.mark.asyncio
    async def test_idle_at_threshold_minus_one(self, twilio_handler, mock_realtime_session):
        """Filler just below threshold still gets idle message."""
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler._consecutive_fillers_without_user_audio = settings.filler_escalation_threshold - 2

        await twilio_handler._send_input_audio_timeout_message()

        sent_msg = mock_realtime_session.send_message.call_args[0][0]
        assert "CRITICAL" not in sent_msg
        assert "resident_thinker_tool" in sent_msg


class TestFillerEscalation:
    """When thinker is NOT running and above threshold, send escalation message."""

    @pytest.mark.asyncio
    async def test_escalation_at_threshold(self, twilio_handler, mock_realtime_session):
        _ready_handler(twilio_handler, mock_realtime_session)
        # After increment, count will equal threshold
        twilio_handler._consecutive_fillers_without_user_audio = settings.filler_escalation_threshold - 1

        await twilio_handler._send_input_audio_timeout_message()

        sent_msg = mock_realtime_session.send_message.call_args[0][0]
        assert "CRITICAL" in sent_msg
        assert "resident_thinker_tool" in sent_msg

    @pytest.mark.asyncio
    async def test_escalation_above_threshold(self, twilio_handler, mock_realtime_session):
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler._consecutive_fillers_without_user_audio = settings.filler_escalation_threshold + 5

        await twilio_handler._send_input_audio_timeout_message()

        sent_msg = mock_realtime_session.send_message.call_args[0][0]
        assert "CRITICAL" in sent_msg

    @pytest.mark.asyncio
    async def test_no_escalation_when_feature_flag_disabled(self, twilio_handler, mock_realtime_session):
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler._consecutive_fillers_without_user_audio = settings.filler_escalation_threshold + 5

        with patch.object(settings, "filler_escalation_enabled", False):
            await twilio_handler._send_input_audio_timeout_message()

        sent_msg = mock_realtime_session.send_message.call_args[0][0]
        assert "CRITICAL" not in sent_msg

    @pytest.mark.asyncio
    async def test_escalation_with_missing_thinker_running(self, twilio_handler, mock_realtime_session):
        """Graceful fallback when ctx has no thinker_running attribute."""
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler.ctx = object()  # bare object, no thinker_running
        twilio_handler._consecutive_fillers_without_user_audio = settings.filler_escalation_threshold + 5

        await twilio_handler._send_input_audio_timeout_message()

        sent_msg = mock_realtime_session.send_message.call_args[0][0]
        assert "CRITICAL" in sent_msg

    @pytest.mark.asyncio
    async def test_escalation_with_threshold_of_one(self, twilio_handler, mock_realtime_session):
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler._consecutive_fillers_without_user_audio = 0

        with patch.object(settings, "filler_escalation_threshold", 1):
            await twilio_handler._send_input_audio_timeout_message()

        sent_msg = mock_realtime_session.send_message.call_args[0][0]
        assert "CRITICAL" in sent_msg


class TestFillerCounter:
    """Counter increments only for user-silence fillers, not thinker-active fillers."""

    @pytest.mark.asyncio
    async def test_counter_increments_when_thinker_idle(self, twilio_handler, mock_realtime_session):
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler._consecutive_fillers_without_user_audio = settings.filler_escalation_threshold

        await twilio_handler._send_input_audio_timeout_message()

        assert twilio_handler._consecutive_fillers_without_user_audio == settings.filler_escalation_threshold + 1

    @pytest.mark.asyncio
    async def test_counter_does_not_increment_when_thinker_running(self, twilio_handler, mock_realtime_session):
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler.ctx.thinker_running = True
        twilio_handler._consecutive_fillers_without_user_audio = 3

        await twilio_handler._send_input_audio_timeout_message()

        assert twilio_handler._consecutive_fillers_without_user_audio == 3

    @pytest.mark.asyncio
    async def test_counter_not_reset_on_non_filler_agent_speech(self, twilio_handler, mock_realtime_session):
        """Non-filler agent speech (e.g. thinker response) must NOT reset the counter."""
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler._consecutive_fillers_without_user_audio = 5
        twilio_handler._next_speech_is_filler = False
        twilio_handler._call_state.is_agent_speaking = False

        # Simulate non-filler agent audio arriving
        event = Mock()
        event.audio = Mock()
        event.audio.item_id = "item_001"
        event.audio.content_index = 0
        event.audio.data = b"\xff" * 160  # μ-law silence frame
        await twilio_handler._handle_realtime_audio_event(event)

        assert twilio_handler._consecutive_fillers_without_user_audio == 5

    @pytest.mark.asyncio
    async def test_counter_resets_on_user_audio(self, twilio_handler, mock_realtime_session):
        """Only user audio resets the counter."""
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler._consecutive_fillers_without_user_audio = 5
        twilio_handler._is_initial_greeting = False
        twilio_handler._stream_sid = "test-stream"
        twilio_handler._expecting_cancel_interrupt = False

        event = Mock()
        event.type = "audio_interrupted"
        await twilio_handler._handle_realtime_event(event)

        assert twilio_handler._consecutive_fillers_without_user_audio == 0


# -- Trace markers on shutdown -------------------------------------------------


class TestShutdownMarker:
    """Single shutdown marker in _cleanup_call with filler context when relevant."""

    @pytest.mark.asyncio
    async def test_deadline_marker_includes_filler_count(self, twilio_handler, mock_realtime_session):
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler._consecutive_fillers_without_user_audio = settings.max_consecutive_fillers_without_user_audio
        twilio_handler._shutdown_reason = "filler_deadline"

        mock_child = Mock()
        mock_root_run = Mock()
        mock_root_run.create_child = Mock(return_value=mock_child)
        twilio_handler.root_run = mock_root_run

        await twilio_handler._cleanup_call()

        assert mock_root_run.create_child.call_count == 1
        call_kwargs = mock_root_run.create_child.call_args[1]
        assert call_kwargs["name"] == "filler_deadline"
        assert call_kwargs["inputs"]["consecutive_fillers"] == settings.max_consecutive_fillers_without_user_audio

    @pytest.mark.asyncio
    async def test_ws_disconnect_no_filler_inputs_when_zero(self, twilio_handler, mock_realtime_session):
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler._consecutive_fillers_without_user_audio = 0
        twilio_handler._shutdown_reason = "websocket_disconnect"

        mock_child = Mock()
        mock_root_run = Mock()
        mock_root_run.create_child = Mock(return_value=mock_child)
        twilio_handler.root_run = mock_root_run

        await twilio_handler._cleanup_call()

        assert mock_root_run.create_child.call_count == 1
        call_kwargs = mock_root_run.create_child.call_args[1]
        assert call_kwargs["name"] == "websocket_disconnect"
        assert call_kwargs["inputs"] == {}

    @pytest.mark.asyncio
    async def test_no_marker_without_shutdown_reason(self, twilio_handler, mock_realtime_session):
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler._consecutive_fillers_without_user_audio = 5
        twilio_handler._shutdown_reason = "filler_deadline"
        twilio_handler.root_run = None

        await twilio_handler._cleanup_call()
        # No error — root_run guard prevents marker posting


class TestShutdownReasonMarker:
    """_shutdown_reason is set at each call site, posted once in _cleanup_call."""

    @pytest.mark.asyncio
    async def test_deadline_sets_filler_deadline(self, twilio_handler, mock_realtime_session):
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler._consecutive_fillers_without_user_audio = settings.max_consecutive_fillers_without_user_audio
        twilio_handler._cleanup_call = AsyncMock()

        await twilio_handler._input_audio_inactivity_loop()

        assert twilio_handler._shutdown_reason == "filler_deadline"

    @pytest.mark.asyncio
    async def test_session_expired_sets_session_timeout(self, twilio_handler, mock_realtime_session):
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler._session_start_time = 0  # epoch = expired
        twilio_handler._cleanup_call = AsyncMock()

        await twilio_handler._input_audio_inactivity_loop()

        assert twilio_handler._shutdown_reason == "session_timeout"

    @pytest.mark.asyncio
    async def test_ws_disconnect_sets_websocket_disconnect(self, twilio_handler, mock_realtime_session):
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler.twilio_websocket.client_state.name = "DISCONNECTED"
        twilio_handler._cleanup_call = AsyncMock()

        await twilio_handler._input_audio_inactivity_loop()

        assert twilio_handler._shutdown_reason == "websocket_disconnect"

    @pytest.mark.asyncio
    async def test_first_reason_wins(self, twilio_handler, mock_realtime_session):
        _ready_handler(twilio_handler, mock_realtime_session)
        twilio_handler._shutdown_reason = "session_timeout"  # already set
        twilio_handler.twilio_websocket.client_state.name = "DISCONNECTED"
        twilio_handler._cleanup_call = AsyncMock()

        await twilio_handler._input_audio_inactivity_loop()

        # First one wins — stays session_timeout
        assert twilio_handler._shutdown_reason == "session_timeout"


class TestMessageLoopCancelledError:
    """CancelledError in _twilio_message_loop is caught gracefully."""

    @pytest.mark.asyncio
    async def test_cancelled_error_does_not_propagate(self, twilio_handler):
        twilio_handler.twilio_websocket.receive_text = AsyncMock(side_effect=asyncio.CancelledError)
        twilio_handler._cleanup_call = AsyncMock()

        with patch("langsmith.trace") as mock_trace:
            mock_run = Mock()
            mock_trace.return_value.__enter__ = Mock(return_value=mock_run)
            mock_trace.return_value.__exit__ = Mock(return_value=False)
            await twilio_handler._twilio_message_loop()

        twilio_handler._cleanup_call.assert_called_once()

    @pytest.mark.asyncio
    async def test_ws_disconnect_sets_shutdown_reason(self, twilio_handler):
        from starlette.websockets import WebSocketDisconnect

        twilio_handler.twilio_websocket.receive_text = AsyncMock(side_effect=WebSocketDisconnect)
        twilio_handler._cleanup_call = AsyncMock()

        with patch("langsmith.trace") as mock_trace:
            mock_run = Mock()
            mock_trace.return_value.__enter__ = Mock(return_value=mock_run)
            mock_trace.return_value.__exit__ = Mock(return_value=False)
            await twilio_handler._twilio_message_loop()

        assert twilio_handler._shutdown_reason == "websocket_disconnect"
