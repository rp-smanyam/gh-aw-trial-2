"""Tests for TwilioHandler user speaking detection and filler message control."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from agents.realtime import RealtimeSession, UserMessageItem
from fastapi import WebSocket

from agent_leasing.twilio_handler import TwilioHandler

EXPIRED_FILLER_TIME = 50.0  # past timestamp (timer has expired)
FUTURE_FILLER_TIME = 200.0  # future timestamp (timer not yet expired)
MOCK_NOW = 100.0  # current time used in all time.time() mocks


@pytest.fixture
def mock_websocket():
    """Mock WebSocket for testing."""
    websocket = Mock(spec=WebSocket)
    websocket.accept = AsyncMock()
    websocket.send_text = AsyncMock()
    websocket.receive_text = AsyncMock()
    websocket.client_state = Mock()
    websocket.client_state.name = "CONNECTED"
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
    session.close = AsyncMock()
    return session


class TestUserSpeakingDetectionOnAudioInterrupted:
    """Test user speaking detection via audio_interrupted event."""

    async def test_user_speaking_set_on_audio_interrupted(self, twilio_handler):
        """Test that is_user_speaking is set when audio_interrupted event arrives."""
        twilio_handler._is_initial_greeting = False
        twilio_handler._stream_sid = "test-stream"
        twilio_handler.is_user_speaking = False
        twilio_handler._next_filler_time = 100.0

        event = Mock()
        event.type = "audio_interrupted"

        await twilio_handler._handle_realtime_event(event)

        assert twilio_handler.is_user_speaking is True
        assert twilio_handler._next_filler_time == 100.0

    async def test_audio_interrupted_does_not_update_missing_next_filler_time(self, twilio_handler):
        """Test that audio_interrupted does not error when _next_filler_time is None."""
        twilio_handler._is_initial_greeting = False
        twilio_handler._stream_sid = "test-stream"
        twilio_handler.is_user_speaking = False
        twilio_handler._next_filler_time = None

        event = Mock()
        event.type = "audio_interrupted"

        await twilio_handler._handle_realtime_event(event)

        assert twilio_handler.is_user_speaking is True
        assert twilio_handler._next_filler_time is None

    async def test_audio_interrupted_clears_queue(self, twilio_handler):
        """Test that audio_interrupted clears the pacer queue."""
        twilio_handler._is_initial_greeting = False
        twilio_handler._stream_sid = "test-stream"

        # Add data to pacer queue
        twilio_handler._out_frame_q.append((b"frame1", ("1", "item1", 0)))
        twilio_handler._out_partial.extend(b"partial-data")

        event = Mock()
        event.type = "audio_interrupted"

        await twilio_handler._handle_realtime_event(event)

        # Queue should be cleared
        assert len(twilio_handler._out_frame_q) == 0
        assert len(twilio_handler._out_partial) == 0

    async def test_audio_interrupted_during_greeting_ignored(self, twilio_handler):
        """Test that audio_interrupted during greeting doesn't clear queue or set flag."""
        twilio_handler._is_initial_greeting = True
        twilio_handler._stream_sid = "test-stream"
        twilio_handler.is_user_speaking = False

        # Add data to pacer queue
        twilio_handler._out_frame_q.append((b"frame1", ("1", "item1", 0)))

        event = Mock()
        event.type = "audio_interrupted"

        await twilio_handler._handle_realtime_event(event)

        # Queue should NOT be cleared during greeting
        assert len(twilio_handler._out_frame_q) == 1
        # Flag should not be set during greeting
        assert twilio_handler.is_user_speaking is False


class TestUserSpeakingDetectionOnHistoryUpdated:
    """Test user speaking detection via history_updated events."""

    async def test_user_speaking_cleared_on_history_completed(self, twilio_handler):
        """Test that is_user_speaking is cleared when user message status is completed."""
        twilio_handler.is_user_speaking = True
        twilio_handler.ctx = Mock()

        # Create history_updated event with completed user message
        mock_user_msg = Mock(spec=UserMessageItem)
        mock_user_msg.role = "user"
        mock_user_msg.status = "completed"
        mock_user_msg.item_id = "user-msg-1"
        mock_user_msg.content = []  # Add content attribute

        event = Mock()
        event.type = "history_updated"
        event.history = [mock_user_msg]

        await twilio_handler._handle_realtime_event(event)

        assert twilio_handler.is_user_speaking is False

    async def test_schedule_filler_called_on_user_completed(self, twilio_handler):
        """Test that _schedule_next_filler is called when user stops speaking."""
        with patch.object(twilio_handler, "_schedule_next_filler") as mock_schedule:
            twilio_handler.is_user_speaking = True
            twilio_handler.ctx = Mock()

            mock_user_msg = Mock(spec=UserMessageItem)
            mock_user_msg.role = "user"
            mock_user_msg.status = "completed"
            mock_user_msg.item_id = "user-msg-1"
            mock_user_msg.content = []  # Add content attribute

            event = Mock()
            event.type = "history_updated"
            event.history = [mock_user_msg]

            await twilio_handler._handle_realtime_event(event)

            # Should have called schedule
            mock_schedule.assert_called_once()

    async def test_user_message_without_status_attribute(self, twilio_handler):
        """Test handling user message that doesn't have status attribute."""
        twilio_handler.is_user_speaking = True
        twilio_handler.ctx = Mock()

        # Create message without status attribute
        mock_message = Mock(spec=UserMessageItem, role="user", item_id="user-msg-1")
        del mock_message.status  # Remove status attribute

        event = Mock()
        event.type = "history_updated"
        event.history = [mock_message]

        # Should not crash, and user_speaking should remain unchanged
        await twilio_handler._handle_realtime_event(event)

        assert twilio_handler.is_user_speaking is True


class TestUserSpeakingDetectionOnAudioEvent:
    """Test user speaking detection via audio events."""

    async def test_user_speaking_cleared_on_agent_audio_event(self, twilio_handler):
        """Test that is_user_speaking is cleared when agent starts speaking (audio event)."""
        twilio_handler.is_user_speaking = True
        twilio_handler.is_agent_speaking = False
        twilio_handler._stream_sid = "test-stream"

        # Create audio event from agent
        event = Mock()
        event.type = "audio"
        event.audio = Mock()
        event.audio.data = b"agent-audio-data"
        event.audio.item_id = "agent-item-1"
        event.audio.content_index = 0

        await twilio_handler._handle_realtime_event(event)

        # Agent speaking should clear user speaking
        assert twilio_handler.is_user_speaking is False
        assert twilio_handler.is_agent_speaking is True


class TestFillerMessageControl:
    """Test filler message control with user speaking detection."""

    @patch("agent_leasing.twilio_handler.settings")
    async def test_filler_not_sent_when_user_is_speaking(self, mock_settings, twilio_handler, mock_realtime_session):
        """Test that filler messages are not sent when user is currently speaking."""
        mock_settings.send_filler_messages = True

        twilio_handler.session = mock_realtime_session
        twilio_handler.agent = Mock()
        twilio_handler._session_ready.set()
        twilio_handler.is_user_speaking = True

        await twilio_handler._send_input_audio_timeout_message()

        # Should not send filler message
        mock_realtime_session.send_message.assert_not_called()

    @patch("agent_leasing.twilio_handler.settings")
    async def test_filler_sent_when_user_not_speaking(self, mock_settings, twilio_handler, mock_realtime_session):
        """Test that filler messages are sent when user is NOT speaking."""
        mock_settings.send_filler_messages = True
        mock_settings.filler_escalation_enabled = True
        mock_settings.filler_escalation_threshold = 2

        twilio_handler.session = mock_realtime_session
        twilio_handler.agent = Mock()
        twilio_handler._session_ready.set()
        twilio_handler.is_user_speaking = False
        twilio_handler.ctx = Mock()
        twilio_handler.ctx.language_code = "en"
        twilio_handler.ctx.thinker_running = False

        await twilio_handler._send_input_audio_timeout_message()

        # Should send filler message
        mock_realtime_session.send_message.assert_called_once()

    @patch("agent_leasing.twilio_handler.settings")
    async def test_filler_not_sent_when_settings_disabled(self, mock_settings, twilio_handler, mock_realtime_session):
        """Test that filler messages are not sent when disabled in settings."""
        mock_settings.send_filler_messages = False

        twilio_handler.session = mock_realtime_session
        twilio_handler.agent = Mock()
        twilio_handler._session_ready.set()
        twilio_handler.is_user_speaking = False

        await twilio_handler._send_input_audio_timeout_message()

        # Should not send filler message
        mock_realtime_session.send_message.assert_not_called()

    @patch("agent_leasing.twilio_handler.settings")
    async def test_filler_not_sent_when_session_not_ready(self, mock_settings, twilio_handler, mock_realtime_session):
        """Test that filler messages are not sent when session is not ready."""
        mock_settings.send_filler_messages = True

        twilio_handler.session = mock_realtime_session
        twilio_handler.agent = Mock()
        # Don't set session ready
        twilio_handler.is_user_speaking = False

        await twilio_handler._send_input_audio_timeout_message()

        # Should not send filler message
        mock_realtime_session.send_message.assert_not_called()

    @patch("agent_leasing.twilio_handler.settings")
    async def test_filler_not_sent_when_agent_is_none(self, mock_settings, twilio_handler, mock_realtime_session):
        """Test that filler messages are not sent when agent is None."""
        mock_settings.send_filler_messages = True

        twilio_handler.session = mock_realtime_session
        twilio_handler.agent = None  # No agent
        twilio_handler._session_ready.set()
        twilio_handler.is_user_speaking = False

        await twilio_handler._send_input_audio_timeout_message()

        # Should not send filler message
        mock_realtime_session.send_message.assert_not_called()


class TestEdgeCasesAndFallbacks:
    """Test edge cases in user speaking detection."""

    async def test_audio_event_clears_user_speaking_as_fallback(self, twilio_handler):
        """Test that audio event clears user_speaking as a fallback mechanism."""
        twilio_handler.is_user_speaking = True
        twilio_handler.is_agent_speaking = False

        event = Mock()
        event.type = "audio"
        event.audio = Mock()
        event.audio.data = b"audio-data"
        event.audio.item_id = "item-1"
        event.audio.content_index = 0

        await twilio_handler._handle_realtime_event(event)

        # Audio event should clear user speaking flag (fallback for when history doesn't update)
        assert twilio_handler.is_user_speaking is False

    async def test_multiple_history_updates_handle_correctly(self, twilio_handler):
        """Test multiple history_updated events in sequence."""
        twilio_handler.ctx = Mock()

        # First event: user starts speaking (in_progress)
        mock_user_msg1 = Mock(spec=UserMessageItem)
        mock_user_msg1.role = "user"
        mock_user_msg1.status = "in_progress"
        mock_user_msg1.item_id = "user-1"
        mock_user_msg1.content = []

        event1 = Mock()
        event1.type = "history_updated"
        event1.history = [mock_user_msg1]

        await twilio_handler._handle_realtime_event(event1)
        # No change to flag from in_progress

        # Second event: user completes
        twilio_handler.is_user_speaking = True
        mock_user_msg2 = Mock(spec=UserMessageItem)
        mock_user_msg2.role = "user"
        mock_user_msg2.status = "completed"
        mock_user_msg2.item_id = "user-1"
        mock_user_msg2.content = []

        event2 = Mock()
        event2.type = "history_updated"
        event2.history = [mock_user_msg2]

        await twilio_handler._handle_realtime_event(event2)

        assert twilio_handler.is_user_speaking is False


class TestInactivityLoop:
    """Test _input_audio_inactivity_loop filler scheduling and firing behavior."""

    def _make_sleep_mock(self, handler):
        """Return an AsyncMock for asyncio.sleep that stops the loop after one iteration."""

        async def one_shot(_):
            handler.call_active = False

        return one_shot

    @patch("agent_leasing.twilio_handler.settings")
    @patch("agent_leasing.twilio_handler.time")
    async def test_reschedules_filler_when_user_speaking_and_timer_expired(
        self, mock_time, mock_settings, twilio_handler, mock_realtime_session
    ):
        """When user is speaking and filler timer has already expired, reschedule
        the filler instead of firing it (Option B fix)."""
        mock_settings.send_filler_messages = True
        mock_settings.max_consecutive_fillers_without_user_audio = 5
        mock_time.time.return_value = MOCK_NOW

        twilio_handler.session = mock_realtime_session
        twilio_handler.agent = Mock()
        twilio_handler._session_ready.set()
        twilio_handler.is_user_speaking = True
        twilio_handler._next_filler_time = EXPIRED_FILLER_TIME  # timer has passed

        with patch("asyncio.sleep", side_effect=self._make_sleep_mock(twilio_handler)):
            with patch.object(twilio_handler, "_schedule_next_filler") as mock_schedule:
                with patch.object(twilio_handler, "_send_input_audio_timeout_message") as mock_send:
                    await twilio_handler._input_audio_inactivity_loop()

        mock_schedule.assert_called()
        mock_send.assert_not_called()

    @patch("agent_leasing.twilio_handler.settings")
    @patch("agent_leasing.twilio_handler.time")
    async def test_fires_filler_when_timer_expired_and_user_not_speaking(
        self, mock_time, mock_settings, twilio_handler, mock_realtime_session
    ):
        """When timer expired and user is not speaking, filler should be sent."""
        mock_settings.send_filler_messages = True
        mock_settings.max_consecutive_fillers_without_user_audio = 5
        mock_time.time.return_value = MOCK_NOW

        twilio_handler.session = mock_realtime_session
        twilio_handler.agent = Mock()
        twilio_handler._session_ready.set()
        twilio_handler.is_user_speaking = False
        twilio_handler._next_filler_time = EXPIRED_FILLER_TIME

        with patch("asyncio.sleep", side_effect=self._make_sleep_mock(twilio_handler)):
            with patch.object(twilio_handler, "_send_input_audio_timeout_message") as mock_send:
                await twilio_handler._input_audio_inactivity_loop()

        mock_send.assert_called_once()

    @patch("agent_leasing.twilio_handler.settings")
    @patch("agent_leasing.twilio_handler.time")
    async def test_does_not_fire_filler_when_timer_not_yet_expired(
        self, mock_time, mock_settings, twilio_handler, mock_realtime_session
    ):
        """When the filler timer has not expired, nothing should be sent."""
        mock_settings.send_filler_messages = True
        mock_settings.max_consecutive_fillers_without_user_audio = 5
        mock_time.time.return_value = MOCK_NOW

        twilio_handler.session = mock_realtime_session
        twilio_handler.agent = Mock()
        twilio_handler._session_ready.set()
        twilio_handler.is_user_speaking = False
        twilio_handler._next_filler_time = FUTURE_FILLER_TIME  # not yet

        with patch("asyncio.sleep", side_effect=self._make_sleep_mock(twilio_handler)):
            with patch.object(twilio_handler, "_send_input_audio_timeout_message") as mock_send:
                await twilio_handler._input_audio_inactivity_loop()

        mock_send.assert_not_called()

    @patch("agent_leasing.twilio_handler.settings")
    async def test_skips_checks_when_filler_disabled(self, mock_settings, twilio_handler, mock_realtime_session):
        """When send_filler_messages is False, loop should not reschedule or send."""
        mock_settings.send_filler_messages = False
        mock_settings.max_consecutive_fillers_without_user_audio = 5

        twilio_handler.session = mock_realtime_session
        twilio_handler.agent = Mock()
        twilio_handler._session_ready.set()
        twilio_handler.is_user_speaking = True
        twilio_handler._next_filler_time = EXPIRED_FILLER_TIME

        with patch("asyncio.sleep", side_effect=self._make_sleep_mock(twilio_handler)):
            with patch.object(twilio_handler, "_schedule_next_filler") as mock_schedule:
                with patch.object(twilio_handler, "_send_input_audio_timeout_message") as mock_send:
                    await twilio_handler._input_audio_inactivity_loop()

        mock_schedule.assert_not_called()
        mock_send.assert_not_called()


class TestMediaEventPushesFillerTimer:
    """Filler timer should only reset when VAD confirms the user is speaking."""

    @patch("agent_leasing.twilio_handler.settings")
    async def test_handle_media_event_does_not_schedule_filler_when_user_silent(self, mock_settings, twilio_handler):
        """Raw media frames should NOT reset the filler timer when VAD says user is silent."""
        mock_settings.send_filler_messages = True
        twilio_handler.call_active = True
        twilio_handler.is_user_speaking = False

        message = {"media": {"payload": "dGVzdA=="}}  # base64("test")
        with patch.object(twilio_handler, "_schedule_next_filler") as mock_schedule:
            await twilio_handler._handle_media_event(message)

        mock_schedule.assert_not_called()

    @patch("agent_leasing.twilio_handler.settings")
    async def test_handle_media_event_schedules_filler_when_user_speaking(self, mock_settings, twilio_handler):
        """Media frames SHOULD reset the filler timer when VAD confirms user is speaking."""
        mock_settings.send_filler_messages = True
        twilio_handler.call_active = True
        twilio_handler.is_user_speaking = True

        message = {"media": {"payload": "dGVzdA=="}}  # base64("test")
        with patch.object(twilio_handler, "_schedule_next_filler") as mock_schedule:
            await twilio_handler._handle_media_event(message)

        mock_schedule.assert_called_once()
