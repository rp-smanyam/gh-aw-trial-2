"""AudioPacer — frame-level output timing for voice audio.

Sends exactly one audio frame per tick to the transport at a steady cadence,
preventing jitter and ensuring smooth playback.  When the frame queue is
empty, silence frames maintain the cadence so the transport never stalls.

Design (clean-room from twilio_handler.py lines 1044-1277):
  - Anchored monotonic scheduler prevents drift across ticks.
  - Configurable prebuffer fills a small runway before the first send.
  - Low-water jitter guard briefly waits before injecting silence.
  - Frames carry event metadata so marks can be sent at event boundaries.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import structlog

from agent_leasing.voice.config import VoiceConfig

SendFrameCallback = Callable[[bytes], Coroutine[Any, Any, None]]
SendMarkCallback = Callable[[str], Coroutine[Any, Any, None]]

logger = structlog.get_logger(__name__)

# --- Constants ---
_FRAME_BYTES = 160  # 20 ms @ 8 kHz mu-law mono
_TICK_SECONDS = 0.020  # 20 ms per frame
_SILENCE_BYTE = 0xFF  # mu-law digital silence


@dataclass(slots=True)
class AudioChunk:
    """A chunk of audio received from the OpenAI session.

    The pacer slices this into exact 160-byte frames for output.
    """

    audio: bytes
    item_id: str = ""
    content_index: int = 0


class AudioPacer:
    """Paces outbound audio at a fixed frame rate.

    The handler feeds ``AudioChunk`` objects via :meth:`enqueue`; the pacer
    slices them into exact 160-byte frames and sends one per tick to the
    transport through the ``send_frame`` callback.

    Lifecycle::

        pacer = AudioPacer(config, send_frame=transport.send_audio)
        pacer.enqueue(chunk)       # from _handle_realtime_audio_event
        await pacer.run()          # started as a background task
        pacer.clear()              # on barge-in
        pacer.stop()               # on call end
    """

    def __init__(
        self,
        config: VoiceConfig,
        send_frame: SendFrameCallback,
        send_mark: SendMarkCallback,
    ) -> None:
        self._config = config
        self._send_frame = send_frame
        self._send_mark = send_mark

        self._prebuffer_frames = config.pacer_prebuffer_frames
        self._startup_timeout_sec = config.pacer_startup_timeout_seconds
        self._underrun_grace_sec = config.pacer_underrun_grace_seconds

        # Frame queue: (frame_bytes, mark_id)
        self._frame_q: deque[tuple[bytes, str]] = deque()
        self._partial = bytearray()
        self._current_mark_id: str = ""

        # Mark tracking: the last mark_id enqueued for each item_id
        self._mark_counter = 0
        self._last_mark_for_item: dict[str, str] = {}

        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, chunk: AudioChunk) -> str:
        """Slice *chunk* into frames, enqueue them, and return the mark_id.

        Returns:
            The mark_id assigned to this chunk (used for playback tracking).
        """
        if not chunk.audio:
            return ""

        self._mark_counter += 1
        mark_id = str(self._mark_counter)
        self._last_mark_for_item[chunk.item_id] = mark_id

        # Accumulate into the partial buffer and slice exact frames
        self._partial.extend(chunk.audio)
        while len(self._partial) >= _FRAME_BYTES:
            frame = bytes(self._partial[:_FRAME_BYTES])
            del self._partial[:_FRAME_BYTES]
            self._frame_q.append((frame, mark_id))

        # If partial is fully consumed, clear the current mark_id so the
        # next chunk starts with a fresh mark.
        if not self._partial:
            self._current_mark_id = ""
        else:
            self._current_mark_id = mark_id

        return mark_id

    def flush_partial(self) -> None:
        """Pad any remaining partial bytes with silence and enqueue as a final frame.

        Called when OpenAI sends ``audio_end`` — the last audio chunk may not
        align to the 160-byte frame boundary.
        """
        if not self._partial:
            return

        # Pad to a full frame with silence
        remainder = bytes(self._partial)
        self._partial.clear()
        padded = remainder + bytes([_SILENCE_BYTE]) * (_FRAME_BYTES - len(remainder))

        mark_id = self._current_mark_id or str(self._mark_counter)
        self._frame_q.append((padded, mark_id))
        self._current_mark_id = ""

    def last_mark_for_item(self, item_id: str) -> str | None:
        """Return the last mark_id enqueued for *item_id*, or None."""
        return self._last_mark_for_item.get(item_id)

    def remove_item_tracking(self, item_id: str) -> None:
        """Stop tracking the last mark for *item_id* (response completed)."""
        self._last_mark_for_item.pop(item_id, None)

    def has_pending_items(self) -> bool:
        """True if there are items still awaiting playback confirmation."""
        return bool(self._last_mark_for_item)

    def clear(self) -> None:
        """Discard all queued frames (barge-in)."""
        self._frame_q.clear()
        self._partial.clear()
        self._current_mark_id = ""

    def stop(self) -> None:
        """Signal the pacer loop to exit."""
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Pacer loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main pacer loop — call as a background task.

        Sends exactly one frame per tick.  Exits when :meth:`stop` is called
        or an unrecoverable transport error occurs.
        """
        self._running = True
        silence_frame = bytes([_SILENCE_BYTE]) * _FRAME_BYTES

        try:
            await self._wait_for_prebuffer()

            base = time.monotonic()
            tick_idx = 1
            pending_mark_id: str | None = None

            while self._running:
                # ---- Anchored sleep ----
                next_deadline = base + _TICK_SECONDS * tick_idx
                now = time.monotonic()
                if now < next_deadline:
                    await asyncio.sleep(next_deadline - now)

                # ---- Low-water jitter guard ----
                if len(self._frame_q) <= 1:
                    grace_deadline = time.monotonic() + self._underrun_grace_sec
                    while not self._frame_q and time.monotonic() < grace_deadline and self._running:
                        await asyncio.sleep(0.001)

                # ---- Pick frame ----
                if self._frame_q:
                    frame, mark_id = self._frame_q.popleft()

                    if pending_mark_id is None:
                        pending_mark_id = mark_id

                    # Send mark at event boundary (mark_id changes or queue drains)
                    next_mark_id = self._frame_q[0][1] if self._frame_q else None
                    if next_mark_id is None or next_mark_id != mark_id:
                        await self._send_mark(pending_mark_id)
                        pending_mark_id = next_mark_id
                else:
                    frame = silence_frame
                    if pending_mark_id:
                        await self._send_mark(pending_mark_id)
                        pending_mark_id = None

                # ---- Send ----
                try:
                    await self._send_frame(frame)
                except (RuntimeError, ConnectionError, OSError):
                    logger.debug("Pacer: transport closed, stopping")
                    break

                tick_idx += 1

            # Flush trailing mark
            if pending_mark_id:
                try:
                    await self._send_mark(pending_mark_id)
                except (RuntimeError, ConnectionError, OSError):
                    pass

        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise  # External cancellation — must propagate
            logger.debug("Pacer cancelled during cleanup")
        except Exception:
            logger.exception("Pacer unexpected error")
        finally:
            self._running = False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _wait_for_prebuffer(self) -> None:
        """Wait until the frame queue has enough runway or timeout elapses."""
        start = time.monotonic()
        while self._running:
            if len(self._frame_q) >= self._prebuffer_frames or (time.monotonic() - start) > self._startup_timeout_sec:
                return
            await asyncio.sleep(0.005)
