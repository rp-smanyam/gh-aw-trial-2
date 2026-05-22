"""Tests for TwilioHandler initial greeting interruption control."""

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from agents.realtime import RealtimeModelSendRawMessage, RealtimeSession

from agent_leasing.twilio_handler import TwilioHandler


@pytest.fixture
def mock_websocket():
    """Mock WebSocket for testing."""
    from fastapi import WebSocket

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
    return session


class TestInitialGreetingInterruptionControl:
    """Test initial greeting interruption control functionality."""

    async def test_audio_interrupted_ignored_during_greeting(self, twilio_handler):
        """Test that audio_interrupted events are ignored during initial greeting."""
        twilio_handler._is_initial_greeting = True
        twilio_handler._stream_sid = "test-stream"

        # Create audio_interrupted event
        event = Mock()
        event.type = "audio_interrupted"

        await twilio_handler._handle_realtime_event(event)

        # Should not send clear event to Twilio during greeting
        twilio_handler.twilio_websocket.send_text.assert_not_called()

        # Verify pacer queue is NOT cleared during greeting
        test_data = b"test-frame"
        twilio_handler._out_frame_q.append((test_data, ("1", "item1", 0)))
        original_queue_size = len(twilio_handler._out_frame_q)

        await twilio_handler._handle_realtime_event(event)
        assert len(twilio_handler._out_frame_q) == original_queue_size

    async def test_audio_interrupted_processed_after_greeting(self, twilio_handler):
        """Test that audio_interrupted events are processed after greeting completes."""
        twilio_handler._is_initial_greeting = False
        twilio_handler._stream_sid = "test-stream"

        # Add data to pacer queue
        test_data = b"test-frame"
        twilio_handler._out_frame_q.append((test_data, ("1", "item1", 0)))

        # Create audio_interrupted event
        event = Mock()
        event.type = "audio_interrupted"

        await twilio_handler._handle_realtime_event(event)

        # Should send clear event to Twilio
        twilio_handler.twilio_websocket.send_text.assert_called_once()

        # Verify pacer queue is cleared
        assert len(twilio_handler._out_frame_q) == 0
        assert len(twilio_handler._out_partial) == 0

    async def test_is_agent_speaking_set_on_first_audio(self, twilio_handler):
        """Test that is_agent_speaking is set when audio arrives."""
        twilio_handler._is_initial_greeting = True
        twilio_handler.is_agent_speaking = False

        # Create audio event for greeting
        event = Mock()
        event.type = "audio"
        event.audio = Mock()
        event.audio.data = b"greeting-audio-data"
        event.audio.item_id = "greeting-item-123"
        event.audio.content_index = 0

        await twilio_handler._handle_realtime_event(event)

        # Should set is_agent_speaking to True
        assert twilio_handler.is_agent_speaking is True

    async def test_response_last_mark_ids_tracks_all_items(self, twilio_handler):
        """Test that _response_last_mark_ids tracks last mark for all items."""
        twilio_handler._is_initial_greeting = False

        # Create multiple audio events
        for i in range(3):
            event = Mock()
            event.type = "audio"
            event.audio = Mock()
            event.audio.data = b"x" * 100
            event.audio.item_id = f"item-{i}"
            event.audio.content_index = 0

            await twilio_handler._handle_realtime_event(event)

        # Should track last mark for all items
        assert len(twilio_handler._response_last_mark_ids) == 3
        for i in range(3):
            assert f"item-{i}" in twilio_handler._response_last_mark_ids

    async def test_greeting_completion_callback_invoked(self, twilio_handler, mock_realtime_session):
        """Test that _on_response_completed is called when greeting completes."""
        twilio_handler.session = mock_realtime_session
        twilio_handler._is_initial_greeting = True
        twilio_handler.is_agent_speaking = True
        twilio_handler._response_last_mark_ids["greeting-item-123"] = "10"
        twilio_handler._mark_data["10"] = ("greeting-item-123", 0, 1000)

        # Add some user audio to buffer during greeting
        twilio_handler._audio_buffer.extend(b"user-audio-during-greeting")

        # Create mark event for greeting completion
        message = {"mark": {"name": "10"}}

        await twilio_handler._handle_mark_event(message)

        # Greeting flag should be cleared
        assert twilio_handler._is_initial_greeting is False
        # is_agent_speaking should be False since no more responses pending
        assert twilio_handler.is_agent_speaking is False

        # Audio buffer should be cleared
        assert len(twilio_handler._audio_buffer) == 0

    async def test_user_audio_discarded_during_greeting(self, twilio_handler, mock_realtime_session):
        """Test that user audio is discarded during initial greeting."""
        twilio_handler.session = mock_realtime_session
        twilio_handler._is_initial_greeting = True
        twilio_handler._audio_buffer.extend(b"user-audio-data")

        # Try to flush buffer during greeting
        await twilio_handler._flush_audio_buffer()

        # Should NOT send audio to OpenAI
        mock_realtime_session.send_audio.assert_not_called()

        # Buffer should remain (not cleared)
        assert len(twilio_handler._audio_buffer) > 0

    async def test_user_audio_sent_after_greeting(self, twilio_handler, mock_realtime_session):
        """Test that user audio is sent to OpenAI after greeting completes."""
        twilio_handler.session = mock_realtime_session
        twilio_handler._is_initial_greeting = False
        test_audio = b"user-audio-after-greeting"
        twilio_handler._audio_buffer.extend(test_audio)

        # Flush buffer after greeting
        await twilio_handler._flush_audio_buffer()

        # Should send audio to OpenAI
        mock_realtime_session.send_audio.assert_called_once_with(test_audio)

        # Buffer should be cleared
        assert len(twilio_handler._audio_buffer) == 0

    async def test_greeting_completion_clears_buffered_audio(self, twilio_handler, mock_realtime_session):
        """Test that buffered user audio is cleared when greeting completes."""
        twilio_handler.session = mock_realtime_session
        twilio_handler._is_initial_greeting = True
        twilio_handler.is_agent_speaking = True
        twilio_handler._response_last_mark_ids["greeting-item-456"] = "25"
        twilio_handler._mark_data["25"] = ("greeting-item-456", 0, 2000)

        # Add substantial user audio during greeting
        user_audio_during_greeting = b"x" * 5000
        twilio_handler._audio_buffer.extend(user_audio_during_greeting)

        # Verify buffer has audio
        assert len(twilio_handler._audio_buffer) == 5000

        # Simulate greeting completion via mark event
        message = {"mark": {"name": "25"}}
        await twilio_handler._handle_mark_event(message)

        # Greeting should be complete
        assert twilio_handler._is_initial_greeting is False
        # is_agent_speaking should be False since no more responses
        assert twilio_handler.is_agent_speaking is False

        # Buffer should be cleared
        assert len(twilio_handler._audio_buffer) == 0

    async def test_multiple_responses_tracked_correctly(self, twilio_handler, mock_realtime_session):
        """Test that multiple responses (greeting and subsequent) are tracked correctly."""
        twilio_handler.session = mock_realtime_session

        # First response: greeting
        twilio_handler._is_initial_greeting = True
        twilio_handler.is_agent_speaking = True
        twilio_handler._response_last_mark_ids["greeting-item"] = "5"
        twilio_handler._mark_data["5"] = ("greeting-item", 0, 1000)

        # Second response: normal response
        twilio_handler._response_last_mark_ids["response-item-1"] = "10"
        twilio_handler._mark_data["10"] = ("response-item-1", 0, 1500)

        # Process greeting completion
        message1 = {"mark": {"name": "5"}}
        await twilio_handler._handle_mark_event(message1)

        assert twilio_handler._is_initial_greeting is False
        assert "greeting-item" not in twilio_handler._response_last_mark_ids
        # is_agent_speaking should still be True because response-item-1 is still pending
        assert twilio_handler.is_agent_speaking is True

        # Process normal response completion
        message2 = {"mark": {"name": "10"}}
        await twilio_handler._handle_mark_event(message2)

        assert "response-item-1" not in twilio_handler._response_last_mark_ids
        # Now is_agent_speaking should be False since all responses completed
        assert twilio_handler.is_agent_speaking is False

    async def test_greeting_audio_not_sent_to_openai_during_playback(self, twilio_handler, mock_realtime_session):
        """Test complete flow: user speaks during greeting, audio is discarded."""
        twilio_handler.session = mock_realtime_session
        twilio_handler._is_initial_greeting = True
        twilio_handler.is_agent_speaking = True

        # Simulate user audio arriving during greeting
        for _ in range(5):
            twilio_handler._audio_buffer.extend(b"user-speaks-during-greeting")
            await twilio_handler._flush_audio_buffer()

        # No audio should be sent to OpenAI
        mock_realtime_session.send_audio.assert_not_called()

        # Now complete the greeting
        twilio_handler._response_last_mark_ids["greeting-123"] = "20"
        twilio_handler._mark_data["20"] = ("greeting-123", 0, 3000)

        message = {"mark": {"name": "20"}}
        await twilio_handler._handle_mark_event(message)

        # Greeting flag cleared, buffer cleared, agent no longer speaking
        assert twilio_handler._is_initial_greeting is False
        assert len(twilio_handler._audio_buffer) == 0
        assert twilio_handler.is_agent_speaking is False

        # Now user speaks after greeting
        twilio_handler._audio_buffer.extend(b"user-speaks-after-greeting")
        await twilio_handler._flush_audio_buffer()

        # This time audio should be sent
        mock_realtime_session.send_audio.assert_called_once()

    async def test_greeting_flag_prevents_audio_flush_but_not_buffering(self, twilio_handler, mock_realtime_session):
        """Test that greeting flag prevents flushing but allows buffering."""
        twilio_handler.session = mock_realtime_session
        twilio_handler._is_initial_greeting = True

        # Media events should still buffer audio
        media_message = {
            "media": {"payload": "dGVzdC1hdWRpbw=="}  # base64 encoded "test-audio"
        }

        await twilio_handler._handle_media_event(media_message)

        # Buffer should contain audio
        assert len(twilio_handler._audio_buffer) > 0

        # But flushing should not send to OpenAI
        await twilio_handler._flush_audio_buffer()
        mock_realtime_session.send_audio.assert_not_called()


class TestTriggerInitialGreetingMessageFormat:
    """KNCK-38774: response.create must use the OpenAI Realtime GA field name `output_modalities`.

    Background: An earlier fix moved `response` inside `other_data` so the SDK's
    try_convert_raw_message would actually forward it. That surfaced a second bug —
    the inner field was still named `modalities`, which belongs to the legacy
    openai.types.beta.realtime schema. The GA schema
    (openai.types.realtime.RealtimeResponseCreateParamsParam) calls it
    `output_modalities`; OpenAI rejects the legacy name with
    "Unknown parameter: 'response.modalities'".
    """

    async def test_response_create_uses_output_modalities(self, twilio_handler, mock_realtime_session):
        mock_model = Mock()
        mock_model.send_event = AsyncMock()
        mock_realtime_session._model = mock_model
        twilio_handler.session = mock_realtime_session
        twilio_handler._schedule_next_filler = Mock()

        await twilio_handler._trigger_initial_greeting()

        assert mock_model.send_event.await_count == 1
        msg = mock_model.send_event.call_args_list[0][0][0]

        assert isinstance(msg, RealtimeModelSendRawMessage)
        assert msg.message["type"] == "response.create"
        assert "response" not in msg.message, "'response' must live under 'other_data', not at the top level"
        assert "other_data" in msg.message
        assert "response" in msg.message["other_data"]
        response_payload = msg.message["other_data"]["response"]
        assert response_payload == {"output_modalities": ["audio"]}
        assert "modalities" not in response_payload, (
            "'modalities' is the legacy beta field name; the GA API uses 'output_modalities'"
        )


def _ttfar_metadata_payloads(root_run: MagicMock) -> list[dict]:
    return [c.args[0] for c in root_run.add_metadata.call_args_list if "initial_greeting_latency_ms" in c.args[0]]


class TestInitialGreetingLatencyV1:
    """v1 voice handler stamps `initial_greeting_latency_ms` on the root run
    once both the `start_event_received` and `first_utterance_sent` marks have
    been recorded on `_startup_span`. Covers both `greeting_agent_enabled`
    branches — the pacer mark site is the same regardless of the flag."""

    @pytest.fixture(autouse=True)
    def _stub_root_run(self, twilio_handler):
        twilio_handler.root_run = MagicMock()
        return twilio_handler

    @pytest.mark.parametrize("greeting_agent_enabled", [False, True])
    def test_records_latency_once_marks_present(self, twilio_handler, greeting_agent_enabled, monkeypatch):
        monkeypatch.setattr(
            "agent_leasing.twilio_handler.settings.greeting_agent_enabled",
            greeting_agent_enabled,
            raising=False,
        )
        twilio_handler._startup_span.mark("start_event_received")
        twilio_handler._startup_span.mark("first_audio_received")
        twilio_handler._startup_span.mark("first_utterance_sent")

        twilio_handler._record_initial_greeting_latency_if_ready()

        payloads = _ttfar_metadata_payloads(twilio_handler.root_run)
        assert len(payloads) == 1
        assert isinstance(payloads[0]["initial_greeting_latency_ms"], int)
        assert payloads[0]["initial_greeting_latency_ms"] >= 0

    @pytest.mark.parametrize("greeting_agent_enabled", [False, True])
    def test_records_only_once_across_repeated_calls(self, twilio_handler, greeting_agent_enabled, monkeypatch):
        monkeypatch.setattr(
            "agent_leasing.twilio_handler.settings.greeting_agent_enabled",
            greeting_agent_enabled,
            raising=False,
        )
        twilio_handler._startup_span.mark("start_event_received")
        twilio_handler._startup_span.mark("first_audio_received")
        twilio_handler._startup_span.mark("first_utterance_sent")

        # Simulating multiple pacer ticks that all mark first_utterance_sent.
        twilio_handler._record_initial_greeting_latency_if_ready()
        twilio_handler._record_initial_greeting_latency_if_ready()
        twilio_handler._record_initial_greeting_latency_if_ready()

        assert len(_ttfar_metadata_payloads(twilio_handler.root_run)) == 1

    def test_no_record_before_first_utterance(self, twilio_handler):
        twilio_handler._startup_span.mark("start_event_received")
        # first_utterance_sent not yet marked
        twilio_handler._record_initial_greeting_latency_if_ready()

        assert _ttfar_metadata_payloads(twilio_handler.root_run) == []

    def test_no_crash_when_root_run_is_none(self, twilio_handler):
        twilio_handler.root_run = None
        twilio_handler._startup_span.mark("start_event_received")
        twilio_handler._startup_span.mark("first_audio_received")
        twilio_handler._startup_span.mark("first_utterance_sent")

        # Should not raise.
        twilio_handler._record_initial_greeting_latency_if_ready()

    async def test_greeting_payload_matches_openai_ga_schema(self, twilio_handler, mock_realtime_session):
        """Round-trip the greeting message through the SDK and assert the resulting wire
        payload conforms to the OpenAI GA RealtimeClientEvent schema.

        Shape-only assertions pass while pydantic silently accepts unknown fields (e.g.
        the legacy `modalities` name) — which OpenAI's server then rejects at runtime.
        This test closes that gap by validating the serialized JSON OpenAI would receive.
        """
        import json

        from agents.realtime.openai_realtime import _ConversionHelper

        mock_model = Mock()
        mock_model.send_event = AsyncMock()
        mock_realtime_session._model = mock_model
        twilio_handler.session = mock_realtime_session
        twilio_handler._schedule_next_filler = Mock()

        await twilio_handler._trigger_initial_greeting()

        raw = mock_model.send_event.call_args_list[0][0][0]
        converted = _ConversionHelper.try_convert_raw_message(raw)
        assert converted is not None, "SDK failed to convert the greeting raw message"

        wire = json.loads(converted.model_dump_json(exclude_unset=True))
        assert wire == {
            "type": "response.create",
            "response": {"output_modalities": ["audio"]},
        }, f"Unexpected greeting payload on the wire: {wire}"


class TestGreetingInitTimeoutAndExceptionPaths:
    """V1 greeting fast-path timeout/exception/transfer coverage.

    Parity with V2's `test_handler_greeting_path.py` cases. Exercises
    ``_handle_greeting_completion`` when ``_full_agent_task`` times out,
    raises, or completes — including verifying that the configurable
    ``settings.greeting_agent_init_timeout_seconds`` is honored (the
    fix for the previously-hardcoded 30s literal).
    """

    async def test_greeting_init_timeout_transfers_to_staff(self, twilio_handler, monkeypatch):
        # Force the configurable timeout very low so the slow init triggers TimeoutError.
        # With the hardcoded 30s literal still in place, the slow() task (5s sleep)
        # completes before the timeout fires and the transfer is never called.
        monkeypatch.setattr(
            "agent_leasing.twilio_handler.settings.greeting_agent_init_timeout_seconds",
            0.01,
        )
        twilio_handler._is_initial_greeting = True
        twilio_handler._transfer_call_on_init_failure = AsyncMock()

        async def slow() -> None:
            await asyncio.sleep(5)

        slow_task = asyncio.create_task(slow())
        twilio_handler._full_agent_task = slow_task

        await twilio_handler._handle_greeting_completion()

        twilio_handler._transfer_call_on_init_failure.assert_awaited_once()
        assert twilio_handler._is_initial_greeting is False
        assert twilio_handler._full_agent_task is None

        # Production code cancelled slow_task — drain it so the event loop
        # doesn't warn about an un-retrieved cancellation at test teardown.
        with contextlib.suppress(asyncio.CancelledError):
            await slow_task

    async def test_greeting_init_exception_transfers_to_staff(self, twilio_handler):
        twilio_handler._is_initial_greeting = True
        twilio_handler._transfer_call_on_init_failure = AsyncMock()

        async def boom() -> None:
            raise RuntimeError("init failed")

        twilio_handler._full_agent_task = asyncio.create_task(boom())
        # Pre-await so the task is already finished with an exception when
        # _handle_greeting_completion runs — keeps the path deterministic.
        with contextlib.suppress(RuntimeError):
            await twilio_handler._full_agent_task

        await twilio_handler._handle_greeting_completion()

        twilio_handler._transfer_call_on_init_failure.assert_awaited_once()
        assert twilio_handler._is_initial_greeting is False
        assert twilio_handler._full_agent_task is None

    async def test_greeting_init_success_swaps_agent(self, twilio_handler, mock_realtime_session):
        twilio_handler._is_initial_greeting = True
        twilio_handler.session = mock_realtime_session
        mock_realtime_session.update_agent = AsyncMock()

        full_agent = Mock()
        twilio_handler.agent = Mock()
        twilio_handler.agent.agent = Mock(return_value=full_agent)
        twilio_handler._transfer_call_on_init_failure = AsyncMock()

        async def ok() -> None:
            return None

        twilio_handler._full_agent_task = asyncio.create_task(ok())

        await twilio_handler._handle_greeting_completion()

        mock_realtime_session.update_agent.assert_awaited_once_with(full_agent)
        twilio_handler._transfer_call_on_init_failure.assert_not_awaited()
        assert twilio_handler._is_initial_greeting is False
        assert twilio_handler._full_agent_task is None
