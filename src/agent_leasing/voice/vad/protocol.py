"""Pluggable VAD protocol — OpenAI server-side today, local TEN VAD tomorrow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class VADResult:
    """Result from a local VAD provider after processing an audio frame.

    Only returned by local VAD implementations. Server-side VAD (OpenAI)
    never produces this — ``VADProvider.process_audio`` returns ``None``.
    """

    is_speech: bool
    speech_probability: float = 0.0
    frames_to_flush: list[bytes] = field(default_factory=list)
    speech_started: bool = False
    speech_ended: bool = False


@runtime_checkable
class VADProvider(Protocol):
    """Abstraction over voice activity detection.

    The default ``OpenAIVAD`` delegates entirely to OpenAI's server-side
    ``semantic_vad``. A future ``LocalVAD`` would disable server-side VAD
    and run TEN VAD locally, giving the backend explicit control over turn
    boundaries (matching Nick Lackman's approach).
    """

    def configure_session(self, session_config: dict[str, Any]) -> dict[str, Any]:
        """Modify the OpenAI session config to set up VAD.

        Called once during session creation. The provider adds or removes
        ``turn_detection`` fields as appropriate.

        Returns:
            The modified session config dict.
        """
        ...  # pragma: no cover

    async def process_audio(self, audio: bytes) -> VADResult | None:
        """Process an inbound audio frame through the VAD.

        Returns:
            A ``VADResult`` if the provider runs local VAD, or ``None``
            if VAD is handled server-side (audio should be forwarded
            unconditionally).
        """
        ...  # pragma: no cover

    def on_speech_started(self) -> None:
        """Called when speech onset is detected (local or server-side)."""
        ...  # pragma: no cover

    def on_speech_ended(self) -> None:
        """Called when speech offset is detected (local or server-side)."""
        ...  # pragma: no cover
