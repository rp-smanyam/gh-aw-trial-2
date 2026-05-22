"""OpenAIVAD — default VAD provider using OpenAI's server-side VAD.

Configures ``turn_detection`` in the session config and delegates all
speech boundary detection to OpenAI.  ``process_audio`` returns ``None``
because audio is forwarded unconditionally — OpenAI handles turn detection.

To switch to local VAD (e.g. TEN VAD for Nick Lackman's approach),
implement the ``VADProvider`` protocol with a class that:
  1. Removes ``turn_detection`` from the session config
  2. Runs local VAD in ``process_audio`` and returns ``VADResult``
  3. Sends ``input_audio_buffer.commit`` + ``response.create`` on speech end
"""

from __future__ import annotations

from typing import Any

from agent_leasing.voice.config import VoiceConfig
from agent_leasing.voice.vad.protocol import VADResult


class OpenAIVAD:
    """Server-side VAD — configures OpenAI's built-in turn detection."""

    def __init__(self, config: VoiceConfig) -> None:
        self._config = config

    def configure_session(self, session_config: dict[str, Any]) -> dict[str, Any]:
        """Add ``turn_detection`` to the session config.

        This is called during session creation.  The turn_detection
        settings come from ``VoiceConfig`` which mirrors the existing
        ``realtime_turn_detection_*`` settings.
        """
        cfg = self._config
        session_config.setdefault("turn_detection", {})
        session_config["turn_detection"].update(
            {
                "type": cfg.turn_detection_type,
                "eagerness": cfg.turn_detection_eagerness,
                "interrupt_response": cfg.turn_detection_interrupt_response,
                "create_response": cfg.turn_detection_create_response,
            }
        )
        return session_config

    async def process_audio(self, audio: bytes) -> VADResult | None:
        """No-op — audio is forwarded unconditionally with server-side VAD."""
        return None

    def on_speech_started(self) -> None:
        """Notification from the server-side VAD event."""

    def on_speech_ended(self) -> None:
        """Notification from the server-side VAD event."""
