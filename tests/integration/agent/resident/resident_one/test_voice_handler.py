"""Integration test for VoiceHandler — happy-path smoke test.

Exercises the new voice handler wiring with a canned Twilio conversation
to verify that the refactored components work together end-to-end:
  1. Transport parses the start event and decodes the payload
  2. Agent is created and session is entered
  3. Greeting is triggered
  4. Cleanup completes without error

This does NOT duplicate the existing agent-level integration tests
(test_realtime.py, test_realtime_responder.py) — those exercise the
ResidentRealtimeResponderAgent directly, which VoiceAgent wraps.
"""

from __future__ import annotations

import base64
import copy
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest

from agent_leasing.api.model import examples
from agent_leasing.voice.config import voice_config_from_settings
from agent_leasing.voice.handler import VoiceHandler, VoiceHandlerManager
from agent_leasing.voice.transport.twilio import TwilioTransport, encode_object


class FakeTwilioWebSocket:
    """Replays a canned sequence of Twilio Media Stream messages.

    Simulates a minimal call: connected → start (with payload) → stop.
    """

    def __init__(self, payload: dict | None = None) -> None:
        self._payload = payload or copy.deepcopy(examples.ASK_REQUEST_RESIDENT_VOICE_KNCK)
        self._messages = self._build_messages()
        self._index = 0
        self._accepted = False

    def _build_messages(self) -> list[str]:
        encoded_payload = encode_object(self._payload)
        return [
            orjson.dumps({"event": "connected"}).decode(),
            orjson.dumps(
                {
                    "event": "start",
                    "start": {
                        "streamSid": "MZ_test_stream_sid",
                        "callSid": self._payload.get("call_sid", "CA_test_call_sid"),
                        "accountSid": "AC_test_account_sid",
                        "customParameters": {"payload": encoded_payload},
                    },
                }
            ).decode(),
            # Brief silence — two media frames
            orjson.dumps(
                {
                    "event": "media",
                    "media": {"payload": base64.b64encode(b"\xff" * 160).decode()},
                }
            ).decode(),
            orjson.dumps(
                {
                    "event": "media",
                    "media": {"payload": base64.b64encode(b"\xff" * 160).decode()},
                }
            ).decode(),
            # Call ends
            orjson.dumps({"event": "stop"}).decode(),
        ]

    async def accept(self) -> None:
        self._accepted = True

    async def receive_text(self) -> str:
        if self._index >= len(self._messages):
            # Simulate disconnect after all messages consumed
            from starlette.websockets import WebSocketDisconnect

            raise WebSocketDisconnect(code=1000)
        msg = self._messages[self._index]
        self._index += 1
        return msg

    async def send_text(self, data: str) -> None:
        pass  # Swallow outbound audio/marks/clear

    async def close(self) -> None:
        pass


@pytest.fixture
def fake_websocket():
    return FakeTwilioWebSocket()


@pytest.fixture
def voice_config():
    from agent_leasing.settings import settings

    return voice_config_from_settings(settings)


class TestVoiceHandlerHappyPath:
    """Smoke test: VoiceHandler processes a canned call without errors."""

    @pytest.mark.asyncio
    async def test_handler_processes_canned_call(self, fake_websocket, voice_config):
        """Verify the handler starts, processes events, and cleans up."""
        transport = TwilioTransport(fake_websocket)
        handler = VoiceHandler(transport, voice_config)
        handler.playback.on_response_completed = handler._on_response_completed

        with (
            # Mock agent setup to avoid real MCP connections and OpenAI calls
            patch(
                "agent_leasing.voice.agent.ResidentRealtimeResponderAgent",
            ) as mock_responder_cls,
            patch(
                "agent_leasing.voice.handler.start_recording",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "agent_leasing.services.agent_service.start_conversation_creation",
            ),
            patch(
                "agent_leasing.voice.session.manager.RealtimeRunner",
            ) as mock_runner_cls,
        ):
            # Mock the responder agent
            mock_responder = AsyncMock()
            mock_responder._thinker_agent = MagicMock()
            mock_responder._get_responder_instructions = AsyncMock(return_value="test instructions")
            mock_responder_cls.return_value = mock_responder
            mock_responder.__aenter__ = AsyncMock(return_value=mock_responder)
            mock_responder.__aexit__ = AsyncMock(return_value=None)

            # Mock the session
            mock_session = AsyncMock()
            mock_session._history = []
            mock_session._context_wrapper = MagicMock()
            mock_runner = MagicMock()
            mock_runner.run = AsyncMock(return_value=mock_session)
            mock_runner_cls.return_value = mock_runner

            # Run the handler — should process all events and exit
            await handler.start()

            # Verify key lifecycle steps occurred
            assert not handler.call_active, "call should be inactive after stop"
            assert transport.call_metadata.stream_sid == "MZ_test_stream_sid"
            assert transport.call_metadata.call_sid == self._get_call_sid(fake_websocket)

            # Verify agent was set up
            assert handler.ctx is not None, "context should have been created"
            assert handler.voice_agent is not None, "voice agent should have been created"

            # Verify session was created and entered
            mock_runner_cls.assert_called_once()
            mock_runner.run.assert_called_once()
            mock_session.enter.assert_called()

    @staticmethod
    def _get_call_sid(ws: FakeTwilioWebSocket) -> str:
        return ws._payload.get("call_sid", "CA_test_call_sid")


class TestVoiceHandlerManager:
    """Test that VoiceHandlerManager creates and tracks handlers."""

    @pytest.mark.asyncio
    async def test_new_session_creates_handler(self, fake_websocket):
        manager = VoiceHandlerManager()
        handler = await manager.new_session(fake_websocket)
        assert isinstance(handler, VoiceHandler)
        assert len(manager.active_handlers) == 1

    @pytest.mark.asyncio
    async def test_cleanup_handler_removes_from_registry(self, fake_websocket):
        manager = VoiceHandlerManager()
        handler = await manager.new_session(fake_websocket)
        handler_id = str(id(handler))
        await manager.cleanup_handler(handler_id)
        assert len(manager.active_handlers) == 0


class TestVoiceHandlerValidationFailure:
    """Test that payload validation failure triggers transfer."""

    @pytest.mark.asyncio
    async def test_invalid_payload_transfers_call(self, voice_config):
        """Handler should transfer the call if the payload is invalid."""
        invalid_payload = {"product": "invalid", "bad": "data"}
        ws = FakeTwilioWebSocket(payload=invalid_payload)
        transport = TwilioTransport(ws)
        handler = VoiceHandler(transport, voice_config)
        handler.playback.on_response_completed = handler._on_response_completed

        with patch(
            "agent_leasing.voice.handler.transfer_call_on_validation_failure",
            new_callable=AsyncMock,
        ) as mock_transfer:
            await handler.start()

            # Transfer should have been called due to validation failure
            mock_transfer.assert_called_once()
            assert not handler.call_active
