"""AudioBuffer — input buffering for audio from the transport to OpenAI.

Accumulates inbound audio from the transport and flushes it to the OpenAI
session in size- or time-based batches.  Optional noise reduction is applied
at flush time.

Design (clean-room from twilio_handler.py lines 1627-1674):
  - Audio is appended as raw bytes from the transport.
  - Flushed when the buffer reaches a size threshold or a time interval elapses.
  - Flush is gated by an ``InteractionPolicy`` (e.g. suppressed during greeting).
  - Noise reduction is applied if enabled in config.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from agent_leasing.voice.config import VoiceConfig

logger = structlog.get_logger(__name__)

SendAudioCallback = Callable[[bytes], Coroutine[Any, Any, None]]


class AudioBuffer:
    """Buffers inbound audio and flushes to the OpenAI session.

    Lifecycle::

        buf = AudioBuffer(config, send_audio=session_manager.send_audio)
        buf.append(raw_bytes)           # from transport AUDIO_RECEIVED events
        await buf.run()                 # started as a background task
        buf.clear()                     # on greeting discard / cleanup
        buf.stop()                      # on call end
    """

    def __init__(
        self,
        config: VoiceConfig,
        send_audio: SendAudioCallback,
    ) -> None:
        self._config = config
        self._send_audio = send_audio

        self._chunk_seconds = config.buffer_chunk_seconds
        self._sample_rate = config.buffer_sample_rate
        self._buffer_size_bytes = int(self._sample_rate * self._chunk_seconds)
        self._noise_reduction_enabled = config.noise_reduction_enabled
        self._audio_format = config.audio_format

        self._buffer = bytearray()
        self._last_flush_time = time.time()
        self._running = False

        # Externally toggled: when True, flush is suppressed and the buffer
        # is discarded instead.  Set by the handler based on InteractionPolicy.
        self.suppress_flush = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, audio: bytes) -> bool:
        """Add raw audio bytes to the buffer.

        Returns:
            True if the buffer has reached the size threshold and the caller
            should ``await flush()``.  This matches the original behaviour
            where ``_handle_media_event`` flushes immediately on size.
        """
        self._buffer.extend(audio)
        return len(self._buffer) >= self._buffer_size_bytes

    @property
    def pending_bytes(self) -> int:
        return len(self._buffer)

    def clear(self) -> None:
        """Discard all buffered audio."""
        self._buffer.clear()

    def stop(self) -> None:
        """Signal the flush loop to exit."""
        self._running = False

    async def flush(self) -> None:
        """Immediately flush the buffer to the session (if not suppressed)."""
        if not self._buffer:
            return

        if self.suppress_flush:
            self._buffer.clear()
            return

        buffer_data = bytes(self._buffer)
        self._buffer.clear()

        if self._noise_reduction_enabled:
            from agent_leasing.util.audio_noise_reduction import apply_noise_reduction

            buffer_data = apply_noise_reduction(buffer_data, self._audio_format)

        try:
            await self._send_audio(buffer_data)
            self._last_flush_time = time.time()
        except Exception:
            logger.debug("Audio buffer: error sending audio to session", exc_info=True)

    # ------------------------------------------------------------------
    # Flush loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Periodic flush loop — call as a background task.

        Checks every ``chunk_seconds`` and flushes if the buffer has data
        and enough time has elapsed since the last flush.
        """
        self._running = True
        try:
            while self._running:
                await asyncio.sleep(self._chunk_seconds)

                if self._buffer and time.time() - self._last_flush_time > self._chunk_seconds * 2:
                    await self.flush()

        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise  # External cancellation — must propagate
            logger.debug("Audio buffer cancelled during cleanup")
        except Exception:
            logger.debug("Audio buffer flush loop error", exc_info=True)
        finally:
            self._running = False
