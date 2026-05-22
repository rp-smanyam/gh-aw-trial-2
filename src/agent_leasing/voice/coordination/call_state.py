"""VoiceCallState — async Event-based state machine for a voice call.

Async Event-based waiters adopted from VJ's ``VoiceCoordinator``.

Tracks speaking, processing, and filler states using ``asyncio.Event``
objects so other components can wait for state transitions without polling.

Clean-room equivalent of ``CallStateManager`` in
``agent_leasing/util/call_state_manager.py``, tailored for the new voice
package.  The existing ``CallStateManager`` is untouched — twilio_handler
continues to use it.
"""

from __future__ import annotations

import asyncio
import datetime
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from agent_leasing.settings import settings

logger = structlog.get_logger(__name__)

PLAYBACK_INJECT_MESSAGE = (
    "(DO NOT ACKNOWLEDGE THIS MESSAGE. Act natural and respond to the user "
    "as if you are a real person.) You need to say the {message_type} message "
    "to the caller now. Speak naturally — do not call any tools."
)


@dataclass
class PlaybackWaitResult:
    """Result of waiting for agent message playback."""

    success: bool
    started: bool
    completed: bool
    attempt: int = 1


class VoiceCallState:
    """Tracks the speaking / processing / filler state of a voice call.

    State transitions are driven by the handler's event dispatcher and
    the interrupt handler.  Other components (FillerManager, thinker tool,
    playback tracker) observe state via properties and ``wait_for_*`` methods.
    """

    def __init__(self, processing_timeout_seconds: float = 30.0) -> None:
        self.is_agent_speaking = False
        self.is_agent_processing = False
        self.is_user_speaking = False
        self.is_filler_playing = False

        self._processing_started_at: float | None = None
        self._processing_timeout = processing_timeout_seconds

        # VAD timestamps — used to associate accurate start/end times with
        # user message item_ids when history_updated events arrive.
        self.last_user_speaking_started_at: datetime.datetime | None = None
        self.last_user_speaking_stopped_at: datetime.datetime | None = None

        # Event-based notifications (replace polling)
        self._agent_speaking_started = asyncio.Event()
        self._agent_speaking_stopped = asyncio.Event()
        self._agent_speaking_stopped.set()  # Initially not speaking
        self._filler_stopped = asyncio.Event()
        self._filler_stopped.set()  # Initially no filler

        # Playback injection — used by wait_for_message_playback to prompt
        # the model to speak when speech doesn't start within the timeout.
        # Wired by the handler to session_manager.send_message.
        self._send_message_fn: Callable[[str], Awaitable[None]] | None = None
        self._playback_attempts: dict[str, int] = {}
        self._last_non_filler_speech_stopped_at: float | None = None

    # ------------------------------------------------------------------
    # Transition methods
    # ------------------------------------------------------------------

    def mark_user_speaking_started(self) -> None:
        self.is_user_speaking = True
        if not self.last_user_speaking_started_at:
            self.last_user_speaking_started_at = datetime.datetime.now(datetime.UTC)

    def mark_user_speaking_stopped(self) -> None:
        self.is_user_speaking = False
        self.last_user_speaking_stopped_at = datetime.datetime.now(datetime.UTC)

    def mark_agent_processing_started(self) -> None:
        self.is_agent_processing = True
        self._processing_started_at = time.time()

    def mark_agent_speaking_started(self, *, is_filler: bool = False) -> None:
        self.is_agent_speaking = True
        self.is_filler_playing = is_filler
        self.is_agent_processing = False
        self._processing_started_at = None
        # Fallback: if agent speaks (non-filler), user must have stopped
        if not is_filler:
            self.is_user_speaking = False
        # Notify waiters
        self._agent_speaking_started.set()
        self._agent_speaking_stopped.clear()
        if is_filler:
            self._filler_stopped.clear()

    def mark_agent_speaking_stopped(self) -> None:
        was_speaking = self.is_agent_speaking
        was_filler = self.is_filler_playing
        self.is_agent_speaking = False
        self.is_filler_playing = False
        if was_speaking and not was_filler:
            self._last_non_filler_speech_stopped_at = time.monotonic()
        self._agent_speaking_stopped.set()
        self._agent_speaking_started.clear()
        self._filler_stopped.set()

    def _has_recent_non_filler_playback(self, window_seconds: float) -> bool:
        """Return True if non-filler speech ended within the given time window."""
        if self._last_non_filler_speech_stopped_at is None:
            return False
        elapsed = time.monotonic() - self._last_non_filler_speech_stopped_at
        return elapsed <= max(window_seconds, 0.0)

    def on_interrupt(self) -> None:
        """Reset speaking state on barge-in."""
        if self.is_agent_speaking:
            self.mark_agent_speaking_stopped()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def is_processing_timed_out(self) -> bool:
        if not self.is_agent_processing or self._processing_started_at is None:
            return False
        return (time.time() - self._processing_started_at) >= self._processing_timeout

    def can_send_filler(self) -> bool:
        """True if filler messages are allowed right now."""
        return not self.is_agent_speaking and not self.is_user_speaking

    def consume_user_speaking_timestamps(self) -> tuple[datetime.datetime | None, datetime.datetime | None]:
        """Return and clear the VAD timestamps for the current user utterance.

        Called by the handler when a user message reaches ``completed`` status
        in ``history_updated``, to associate accurate times with the item_id.

        Returns:
            (started_at, stopped_at) — either may be None if the VAD event
            was missed.
        """
        started = self.last_user_speaking_started_at
        stopped = self.last_user_speaking_stopped_at
        self.last_user_speaking_started_at = None
        self.last_user_speaking_stopped_at = None
        return started, stopped

    # ------------------------------------------------------------------
    # Async waiters (event-driven, no polling)
    # ------------------------------------------------------------------

    async def wait_for_agent_speaking_started(self, timeout: float) -> bool:
        if self.is_agent_speaking:
            return True
        self._agent_speaking_started.clear()
        if self.is_agent_speaking:  # re-check after clear
            return True
        try:
            await asyncio.wait_for(self._agent_speaking_started.wait(), timeout=max(timeout, 0.0))
            return True
        except TimeoutError:
            return self.is_agent_speaking

    async def wait_for_agent_speaking_stopped(self, timeout: float) -> bool:
        if not self.is_agent_speaking:
            return True
        self._agent_speaking_stopped.clear()
        if not self.is_agent_speaking:
            return True
        try:
            await asyncio.wait_for(self._agent_speaking_stopped.wait(), timeout=max(timeout, 0.0))
            return True
        except TimeoutError:
            return not self.is_agent_speaking

    async def wait_for_filler_stopped(self, timeout: float) -> bool:
        if not self.is_filler_playing:
            return True
        self._filler_stopped.clear()
        if not self.is_filler_playing:
            return True
        try:
            await asyncio.wait_for(self._filler_stopped.wait(), timeout=max(timeout, 0.0))
            return True
        except TimeoutError:
            return not self.is_filler_playing

    # ------------------------------------------------------------------
    # Composite waiters (used by end_call, transfer_to_staff_voice, ESR)
    # ------------------------------------------------------------------

    async def wait_for_agent_playback(
        self,
        start_timeout_seconds: float,
        end_timeout_seconds: float,
        settle_delay_seconds: float = 0.2,
    ) -> tuple[bool, bool]:
        """Wait for agent speech to start and then stop."""
        started = await self.wait_for_agent_speaking_started(start_timeout_seconds)
        if not started:
            return False, False
        completed = await self.wait_for_agent_speaking_stopped(end_timeout_seconds)
        if completed and settle_delay_seconds > 0:
            await asyncio.sleep(settle_delay_seconds)
        return True, completed

    async def wait_for_message_playback(
        self,
        message_type: str,
        tool_name: str | None = None,
        start_timeout_seconds: float | None = None,
        end_timeout_seconds: float | None = None,
        settle_delay_seconds: float | None = None,
    ) -> PlaybackWaitResult:
        """Wait for agent message playback with structured result.

        Used by ``end_call``, ``transfer_to_staff_voice``, and
        ``emergency_service_transfer`` to wait for the goodbye/transfer
        message to finish playing before proceeding.

        If a filler is currently playing, waits for it to finish first
        so filler audio isn't mistaken for the real message.
        """
        start_timeout = start_timeout_seconds or settings.playback_start_timeout_seconds
        end_timeout = end_timeout_seconds or settings.playback_end_timeout_seconds
        settle_delay = settle_delay_seconds or settings.playback_settle_delay_seconds

        # Wait for any active filler to finish first
        if self.is_filler_playing:
            await self.wait_for_filler_stopped(timeout=end_timeout)

        started, completed = await self.wait_for_agent_playback(
            start_timeout_seconds=start_timeout,
            end_timeout_seconds=end_timeout,
            settle_delay_seconds=settle_delay,
        )

        attempt = self._playback_attempts.get(message_type, 0) + 1
        self._playback_attempts[message_type] = attempt

        if not started:
            if message_type == "goodbye" and self._has_recent_non_filler_playback(
                settings.voice_goodbye_playback_dedupe_window_seconds
            ):
                logger.info(f"Skipping playback inject for {message_type} due to recent non-filler speech")
                self._playback_attempts[message_type] = 0
                return PlaybackWaitResult(success=True, started=True, completed=True, attempt=attempt)

            if self._send_message_fn is None:
                logger.warning(f"Playback not detected for {message_type} and no send_message_fn; proceeding")
                self._playback_attempts[message_type] = 0
                return PlaybackWaitResult(success=True, started=False, completed=False, attempt=attempt)

            # Inject a prompt telling the model to speak, then wait
            inject_msg = PLAYBACK_INJECT_MESSAGE.format(message_type=message_type)
            for inject_attempt in range(1, settings.max_playback_attempts + 1):
                logger.info(f"Injecting playback prompt for {message_type} (attempt {inject_attempt})")
                await self._send_message_fn(inject_msg)
                started = await self.wait_for_agent_speaking_started(start_timeout)
                if started and self.is_filler_playing:
                    await self.wait_for_filler_stopped(timeout=end_timeout)
                    started = False
                    continue
                if started:
                    break

            attempt += inject_attempt
            if not started:
                logger.warning(f"Playback not detected for {message_type} after inject; proceeding")
                self._playback_attempts[message_type] = 0
                return PlaybackWaitResult(success=True, started=False, completed=False, attempt=attempt)

            completed = await self.wait_for_agent_speaking_stopped(end_timeout)
            if completed and settle_delay > 0:
                await asyncio.sleep(settle_delay)
            self._playback_attempts[message_type] = 0
            return PlaybackWaitResult(success=True, started=True, completed=completed, attempt=attempt)

        self._playback_attempts[message_type] = 0
        return PlaybackWaitResult(success=True, started=True, completed=completed, attempt=attempt)

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Full reset for recovery or cleanup."""
        self.is_agent_speaking = False
        self.is_agent_processing = False
        self.is_user_speaking = False
        self.is_filler_playing = False
        self._processing_started_at = None
        self.last_user_speaking_started_at = None
        self.last_user_speaking_stopped_at = None
        self._agent_speaking_started.clear()
        self._agent_speaking_stopped.set()
        self._filler_stopped.set()
        self._playback_attempts.clear()
        self._last_non_filler_speech_stopped_at = None
        self._send_message_fn = None
