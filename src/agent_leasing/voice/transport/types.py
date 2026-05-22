"""Transport-layer types shared across the voice package."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TransportEventType(Enum):
    """Events emitted by a VoiceTransport implementation."""

    # Connection lifecycle
    CONNECTED = "connected"
    STARTED = "started"
    STOPPED = "stopped"

    # Audio
    AUDIO_RECEIVED = "audio_received"

    # Playback tracking
    PLAYBACK_MILESTONE = "playback_milestone"


@dataclass(frozen=True, slots=True)
class TransportEvent:
    """A single event from the transport layer.

    Attributes:
        type: The event category.
        data: Payload — contents depend on the event type:
            - AUDIO_RECEIVED: {"audio": bytes}
            - STARTED: CallMetadata fields
            - PLAYBACK_MILESTONE: {"notification_id": str}
            - CONNECTED / STOPPED: empty or transport-specific
    """

    type: TransportEventType
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CallMetadata:
    """Metadata extracted from the transport when a call begins.

    Twilio populates this from the ``start`` payload; other transports
    supply equivalent data from their own signalling.
    """

    stream_sid: str = ""
    call_sid: str = ""
    account_sid: str = ""
    custom_parameters: dict[str, Any] = field(default_factory=dict)
