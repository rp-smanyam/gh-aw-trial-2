"""Transport protocol — the abstraction boundary between voice infrastructure and call logic.

``VoiceTransport`` is implemented once per transport technology (Twilio today,
WebRTC tomorrow). The rest of the voice package never touches transport
internals — it programs against this protocol.

``PlaybackNotifier`` is the reverse callback: the transport calls it to report
playback progress back to the handler.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from agent_leasing.voice.transport.types import CallMetadata, TransportEvent


@runtime_checkable
class VoiceTransport(Protocol):
    """Abstract bidirectional transport for a single voice call.

    Implementations own the wire-level protocol (WebSocket, WebRTC, etc.)
    and present a uniform event stream + output API.
    """

    def receive_events(self) -> AsyncIterator[TransportEvent]:
        """Yield transport events for the lifetime of the call.

        Implementations are typically async generators::

            async def receive_events(self):
                while self._connected:
                    msg = await self._ws.receive_json()
                    yield self._parse(msg)

        The iterator ends when the call disconnects or the transport is closed.
        Events are yielded in the order they arrive from the underlying channel.
        """
        ...  # pragma: no cover

    async def send_audio(self, audio: bytes) -> None:
        """Send a single raw audio frame to the caller.

        *audio* is raw PCM / mu-law bytes (matching the configured audio
        format). The transport handles any encoding or framing required
        by the underlying channel (e.g. base64 + JSON for Twilio,
        raw RTP for WebRTC).
        """
        ...  # pragma: no cover

    async def send_clear(self) -> None:
        """Tell the transport to discard any queued outbound audio.

        Used on barge-in to stop playback immediately.
        """
        ...  # pragma: no cover

    async def request_playback_notification(self, notification_id: str) -> None:
        """Ask the transport to notify when audio up to this point has played.

        Twilio implements this with *mark* events; WebRTC can use
        ``AudioOutputStream.on_audio_drained`` or wall-clock timing.
        The transport calls ``PlaybackNotifier.on_playback_milestone``
        when the milestone is reached.
        """
        ...  # pragma: no cover

    @property
    def call_metadata(self) -> CallMetadata:
        """Metadata extracted when the call connected (call SID, etc.)."""
        ...  # pragma: no cover

    @property
    def is_connected(self) -> bool:
        """True while the underlying channel is open."""
        ...  # pragma: no cover

    async def close(self) -> None:
        """Tear down the transport and release resources."""
        ...  # pragma: no cover


@runtime_checkable
class PlaybackNotifier(Protocol):
    """Callback interface the transport uses to report playback progress.

    The handler (or its ``PlaybackTracker``) implements this and registers
    it with the transport so it can track what audio the caller actually heard.
    """

    async def on_playback_milestone(self, notification_id: str) -> None:
        """A previously-requested playback notification has been reached."""
        ...  # pragma: no cover

    async def on_audio_drained(self) -> None:
        """All queued audio has been delivered to the caller."""
        ...  # pragma: no cover
