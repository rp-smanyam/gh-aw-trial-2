"""TwilioTransport — VoiceTransport implementation for Twilio Media Streams.

Protocol handling from ``twilio_handler.py``; abstracted behind ``VoiceTransport`` following VJ's ``VoiceTransportBase`` pattern.

Owns the Twilio WebSocket lifecycle: accepting the connection, parsing
JSON messages (connected, start, media, mark, stop), encoding outbound
audio as base64 JSON, and sending mark/clear events.

The rest of the voice package never touches Twilio-specific details —
it programs against the ``VoiceTransport`` protocol.
"""

from __future__ import annotations

import base64
import copy
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import orjson
import structlog
from starlette.websockets import WebSocket, WebSocketDisconnect

from agent_leasing.settings import settings
from agent_leasing.voice.transport.types import CallMetadata, TransportEvent, TransportEventType

logger = structlog.get_logger(__name__)


class TwilioTransport:
    """Bidirectional transport over a Twilio Media Stream WebSocket.

    Lifecycle (driven by the handler)::

        transport = TwilioTransport(websocket)
        async for event in transport.receive_events():
            ...  # handler processes events
        await transport.close()
    """

    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self._stream_sid: str = ""
        self._call_metadata = CallMetadata()
        self._connected = False
        self._payload: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # VoiceTransport protocol
    # ------------------------------------------------------------------

    def receive_events(self) -> AsyncIterator[TransportEvent]:
        """Yield transport events for the lifetime of the call."""
        return self._event_loop()

    async def send_audio(self, audio: bytes) -> None:
        """Send a raw mu-law audio frame to Twilio (base64 + JSON)."""
        if not self._stream_sid or not self._connected:
            return
        payload = base64.b64encode(audio).decode("utf-8")
        msg = {
            "event": "media",
            "streamSid": self._stream_sid,
            "media": {"payload": payload},
        }
        await self._send_json(msg)

    async def send_clear(self) -> None:
        """Send a clear event to stop Twilio playback immediately."""
        if not self._stream_sid or not self._connected:
            return
        await self._send_json({"event": "clear", "streamSid": self._stream_sid})

    async def request_playback_notification(self, notification_id: str) -> None:
        """Send a mark event — Twilio will echo it back when audio has played."""
        if not self._stream_sid or not self._connected:
            return
        await self._send_json(
            {
                "event": "mark",
                "streamSid": self._stream_sid,
                "mark": {"name": notification_id},
            }
        )

    @property
    def call_metadata(self) -> CallMetadata:
        return self._call_metadata

    @property
    def payload(self) -> dict[str, Any] | None:
        """The decoded payload from the Twilio start event.

        Available after the STARTED event has been yielded.
        """
        return self._payload

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def close(self) -> None:
        """Close the WebSocket connection."""
        self._connected = False
        try:
            await self._ws.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Event loop
    # ------------------------------------------------------------------

    async def _event_loop(self) -> AsyncIterator[TransportEvent]:
        """Parse Twilio WebSocket messages into TransportEvents."""
        await self._ws.accept()
        self._connected = True

        try:
            while self._connected:
                text = await self._ws.receive_text()
                message = orjson.loads(text.encode("utf-8"))
                event_type = message.get("event", "")

                if event_type == "connected":
                    logger.debug("Twilio media stream connected")
                    yield TransportEvent(type=TransportEventType.CONNECTED)

                elif event_type == "start":
                    logger.debug("Twilio start event received")
                    start_data = message.get("start", {})
                    self._stream_sid = start_data.get("streamSid", "")
                    call_sid = start_data.get("callSid", "")
                    custom_params = start_data.get("customParameters", {})

                    # Decode the B64 JSON payload
                    self._payload = self._decode_payload(custom_params)
                    # Override call_sid in payload for consistency
                    if self._payload:
                        self._payload["call_sid"] = call_sid
                        if "product_info" in self._payload:
                            self._payload["product_info"]["call_sid"] = call_sid

                    self._call_metadata = CallMetadata(
                        stream_sid=self._stream_sid,
                        call_sid=call_sid,
                        account_sid=start_data.get("accountSid", ""),
                        custom_parameters=custom_params,
                    )
                    logger.debug(f"Twilio stream started: {self._stream_sid}")
                    yield TransportEvent(type=TransportEventType.STARTED)

                elif event_type == "media":
                    media = message.get("media", {})
                    payload_b64 = media.get("payload", "")
                    if payload_b64:
                        audio = base64.b64decode(payload_b64)
                        yield TransportEvent(
                            type=TransportEventType.AUDIO_RECEIVED,
                            data={"audio": audio},
                        )

                elif event_type == "mark":
                    mark_data = message.get("mark", {})
                    notification_id = mark_data.get("name", "")
                    if notification_id:
                        yield TransportEvent(
                            type=TransportEventType.PLAYBACK_MILESTONE,
                            data={"notification_id": notification_id},
                        )

                elif event_type == "stop":
                    logger.debug("Twilio media stream stopped")
                    self._connected = False
                    yield TransportEvent(type=TransportEventType.STOPPED)
                    break

        except WebSocketDisconnect:
            logger.debug("Twilio WebSocket disconnected")
            self._connected = False
            yield TransportEvent(type=TransportEventType.STOPPED)
        except (RuntimeError, ConnectionError, OSError) as e:
            logger.debug(f"Twilio connection error: {e}")
            self._connected = False
            yield TransportEvent(type=TransportEventType.STOPPED)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _send_json(self, msg: dict[str, Any]) -> None:
        """Send a JSON message over the WebSocket."""
        if not self._connected:
            return
        try:
            await self._ws.send_text(orjson.dumps(msg).decode("utf-8"))
        except (RuntimeError, ConnectionError, OSError, WebSocketDisconnect) as e:
            if isinstance(e, RuntimeError) and "close message has been sent" in str(e):
                logger.debug("Twilio skipped send — WebSocket closing")
                return
            logger.debug(f"Twilio send error: {e}")
            self._connected = False

    def _decode_payload(self, custom_params: dict[str, Any]) -> dict[str, Any] | None:
        """Decode the B64 JSON payload from Twilio custom parameters."""
        payload_str = custom_params.get("payload")
        if payload_str is None:
            # No payload — testing mode, use default
            logger.warning("Twilio: no payload, using test default")
            return self._load_test_payload()
        return decode_object(payload_str)

    @staticmethod
    def _load_test_payload() -> dict[str, Any]:
        """Load a test payload from file or use the built-in example."""
        from agent_leasing.api.model import examples

        if settings.twilio_test_payload:
            try:
                text = Path(settings.twilio_test_payload).read_text(encoding="utf-8")
                data = json.loads(text)
                if isinstance(data, dict):
                    logger.debug(f"Twilio using test payload from file: {settings.twilio_test_payload}")
                    return data
            except Exception:
                logger.exception("Twilio failed to load test payload, using default")
        return copy.deepcopy(examples.ASK_REQUEST_RESIDENT_VOICE_KNCK)


# ------------------------------------------------------------------
# Serialization utilities (previously module-level in twilio_handler.py)
# ------------------------------------------------------------------


def encode_object(obj: dict[str, Any]) -> str:
    """Serialize a dict to a base64-encoded JSON string."""
    return base64.b64encode(orjson.dumps(obj)).decode("utf-8")


def decode_object(encoded_string: str) -> dict[str, Any]:
    """Deserialize a base64-encoded JSON string to a dict."""
    return orjson.loads(base64.b64decode(encoded_string.encode("utf-8")))
