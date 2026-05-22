"""Call state management for tracking speaking and processing states."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from agent_leasing.settings import settings

if TYPE_CHECKING:
    from agents import RunContextWrapper

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


class CallStateManager:
    """Manages speaking and processing state for a voice call.

    Tracks three key states:
    - is_user_speaking: User is actively speaking (detected via VAD)
    - is_agent_speaking: Agent is producing audio output
    - is_agent_processing: Agent is thinking/using tools (not yet speaking)

    These states are used to:
    - Control filler message scheduling (don't send during speaking/processing)
    - Manage audio interruptions (clear queue when user speaks)
    - Detect processing timeouts (agent stuck)
    """

    def __init__(self, processing_timeout_seconds: float = 30.0):
        """Initialize call state manager.

        Args:
            processing_timeout_seconds: Max seconds to wait for agent response
                before allowing filler messages (default: 30s)
        """
        self.is_agent_speaking = False
        self.is_agent_processing = False
        self.is_user_speaking = False
        self.is_filler_playing = False
        self._processing_started_at: float | None = None
        self._processing_timeout = processing_timeout_seconds
        self._send_message_fn: Callable[[str], Awaitable[None]] | None = None
        self._playback_attempts: dict[str, int] = {}
        self._last_non_filler_speech_stopped_at: float | None = None

        # Event-based notifications to replace polling in wait_for_* methods
        self._agent_speaking_started = asyncio.Event()
        self._agent_speaking_stopped = asyncio.Event()
        self._agent_speaking_stopped.set()  # Initially not speaking
        self._filler_stopped = asyncio.Event()
        self._filler_stopped.set()  # Initially no filler

    def mark_user_speaking_started(self) -> None:
        """User started speaking (from VAD or audio_interrupted event)."""
        self.is_user_speaking = True

    def mark_user_speaking_stopped(self) -> None:
        """User stopped speaking (from history_updated with status=completed)."""
        self.is_user_speaking = False

    def mark_agent_processing_started(self) -> None:
        """Agent started processing (thinking/tools) after user finished speaking."""
        self.is_agent_processing = True
        self._processing_started_at = time.time()

    def mark_agent_speaking_started(self, is_filler: bool = False) -> None:
        """Agent started speaking (first audio chunk received).

        Args:
            is_filler: True if the speech is a filler message, False for real content.
        """
        self.is_agent_speaking = True
        self.is_filler_playing = is_filler
        # Processing is complete once speaking starts
        self.is_agent_processing = False
        self._processing_started_at = None
        # If agent is speaking (non-filler), user must have stopped - clear as fallback
        # This handles edge cases where history_updated completion event is missed
        if not is_filler:
            self.is_user_speaking = False
        # Notify waiters
        self._agent_speaking_started.set()
        self._agent_speaking_stopped.clear()
        if is_filler:
            self._filler_stopped.clear()

    def mark_agent_speaking_stopped(self) -> None:
        """Agent stopped speaking (all responses completed, no pending marks)."""
        was_speaking = self.is_agent_speaking
        was_filler = self.is_filler_playing
        self.is_agent_speaking = False
        self.is_filler_playing = False
        if was_speaking and not was_filler:
            self._last_non_filler_speech_stopped_at = time.monotonic()
        # Notify waiters
        self._agent_speaking_stopped.set()
        self._agent_speaking_started.clear()
        self._filler_stopped.set()

    def is_processing_timed_out(self) -> bool:
        """Check if agent processing has exceeded timeout.

        Returns:
            True if processing has been active longer than timeout duration,
            False otherwise.
        """
        if not self.is_agent_processing or self._processing_started_at is None:
            return False

        elapsed = time.time() - self._processing_started_at
        return elapsed >= self._processing_timeout

    def can_send_filler(self) -> bool:
        """Check if filler messages are allowed based on current state.

        Fillers should NOT be sent if:
        - Agent is currently speaking
        - User is currently speaking

        Returns:
            True if filler can be sent, False otherwise.
        """
        if self.is_agent_speaking:
            return False

        if self.is_user_speaking:
            return False

        return True

    def reset(self) -> None:
        """Reset all state flags (for recovery or cleanup)."""
        self.is_agent_speaking = False
        self.is_agent_processing = False
        self.is_user_speaking = False
        self.is_filler_playing = False
        self._processing_started_at = None
        self._playback_attempts.clear()
        self._last_non_filler_speech_stopped_at = None
        # Reset events to initial state (not speaking)
        self._agent_speaking_started.clear()
        self._agent_speaking_stopped.set()
        self._filler_stopped.set()

    def _has_recent_non_filler_playback(self, window_seconds: float) -> bool:
        """Return True if non-filler speech ended within the given time window."""
        if self._last_non_filler_speech_stopped_at is None:
            return False
        elapsed = time.monotonic() - self._last_non_filler_speech_stopped_at
        return elapsed <= max(window_seconds, 0.0)

    # Helper functions

    async def wait_for_agent_speaking_started(
        self,
        timeout_seconds: float,
        poll_interval_seconds: float = 0.05,
    ) -> bool:
        """Wait for agent speech to start (event-driven, no polling)."""
        if self.is_agent_speaking:
            return True
        self._agent_speaking_started.clear()
        # Re-check after clearing to avoid race condition
        if self.is_agent_speaking:
            return True
        try:
            await asyncio.wait_for(
                self._agent_speaking_started.wait(),
                timeout=max(timeout_seconds, 0.0),
            )
            return True
        except TimeoutError:
            return self.is_agent_speaking

    async def wait_for_agent_speaking_stopped(
        self,
        timeout_seconds: float,
        poll_interval_seconds: float = 0.05,
    ) -> bool:
        """Wait for agent speech to stop (event-driven, no polling)."""
        if not self.is_agent_speaking:
            return True
        self._agent_speaking_stopped.clear()
        # Re-check after clearing to avoid race condition
        if not self.is_agent_speaking:
            return True
        try:
            await asyncio.wait_for(
                self._agent_speaking_stopped.wait(),
                timeout=max(timeout_seconds, 0.0),
            )
            return True
        except TimeoutError:
            return not self.is_agent_speaking

    async def wait_for_agent_playback(
        self,
        start_timeout_seconds: float,
        end_timeout_seconds: float,
        poll_interval_seconds: float = 0.05,
        settle_delay_seconds: float = 0.2,
    ) -> tuple[bool, bool]:
        """Wait for agent speech to start and then stop."""
        started = await self.wait_for_agent_speaking_started(
            start_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        if not started:
            return False, False

        completed = await self.wait_for_agent_speaking_stopped(
            end_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        if completed and settle_delay_seconds > 0:
            await asyncio.sleep(settle_delay_seconds)
        return True, completed

    async def wait_for_filler_to_finish(
        self,
        timeout_seconds: float = 10.0,
        poll_interval_seconds: float = 0.05,
    ) -> bool:
        """Wait for any active filler message to finish playing (event-driven, no polling).

        Args:
            timeout_seconds: Max time to wait for filler to complete.
            poll_interval_seconds: Unused, kept for API compatibility.

        Returns:
            True if filler finished (or wasn't playing), False if timeout.
        """
        if not self.is_filler_playing:
            return True
        self._filler_stopped.clear()
        # Re-check after clearing to avoid race condition
        if not self.is_filler_playing:
            return True
        try:
            await asyncio.wait_for(
                self._filler_stopped.wait(),
                timeout=max(timeout_seconds, 0.0),
            )
            return True
        except TimeoutError:
            return not self.is_filler_playing

    async def wait_for_message_playback(
        self,
        message_type: str,
        tool_name: str | None = None,
        start_timeout_seconds: float | None = None,
        end_timeout_seconds: float | None = None,
        settle_delay_seconds: float | None = None,
    ) -> PlaybackWaitResult:
        """Wait for agent message playback with structured result.

        This is a higher-level wrapper around wait_for_agent_playback that
        provides a structured result with error messages for tool use.

        If a filler message is currently playing, this will wait for it to
        complete first, then check for the actual transition/goodbye message.

        Args:
            message_type: Description of the message (e.g., "transfer", "goodbye")
                         for error messages.
            tool_name: Tool name to retry once the message is spoken (for error messages).
            start_timeout_seconds: Max time to wait for speech to start.
                                   Defaults to settings.playback_start_timeout_seconds.
            end_timeout_seconds: Max time to wait for speech to complete.
                                 Defaults to settings.playback_end_timeout_seconds.
            settle_delay_seconds: Delay after speech completes.
                                  Defaults to settings.playback_settle_delay_seconds.

        Returns:
            PlaybackWaitResult with success status and optional error message.
        """
        start_timeout = start_timeout_seconds or settings.playback_start_timeout_seconds
        end_timeout = end_timeout_seconds or settings.playback_end_timeout_seconds
        settle_delay = settle_delay_seconds or settings.playback_settle_delay_seconds

        # If a filler is currently playing, wait for it to finish first.
        # This prevents false positives where filler audio is mistaken for
        # the actual transition/goodbye message.
        if self.is_filler_playing:
            await self.wait_for_filler_to_finish(timeout_seconds=end_timeout)
            # After filler finishes, we need to wait for the REAL message to start
            # Reset expectations - agent speech has stopped (filler done)

        started, completed = await self.wait_for_agent_playback(
            start_timeout_seconds=start_timeout,
            end_timeout_seconds=end_timeout,
            settle_delay_seconds=settle_delay,
        )

        attempt = self._playback_attempts.get(message_type, 0) + 1
        self._playback_attempts[message_type] = attempt

        if not started:
            if message_type == "goodbye" and self._has_recent_non_filler_playback(
                settings.goodbye_playback_dedupe_window_seconds
            ):
                logger.info(
                    "Skipping playback inject for goodbye due to recent non-filler speech",
                    message_type=message_type,
                    tool_name=tool_name,
                )
                self._playback_attempts[message_type] = 0
                return PlaybackWaitResult(success=True, started=True, completed=True, attempt=attempt)

            if self._send_message_fn is None:
                logger.warning(
                    "Playback not detected and no send_message_fn available; proceeding",
                    message_type=message_type,
                    tool_name=tool_name,
                )
                self._playback_attempts[message_type] = 0
                return PlaybackWaitResult(success=True, started=False, completed=False, attempt=attempt)

            # Inject a prompt telling the model to speak, then wait for playback
            inject_msg = PLAYBACK_INJECT_MESSAGE.format(message_type=message_type)
            for inject_attempt in range(1, settings.max_playback_attempts + 1):
                logger.info(
                    "Injecting playback prompt",
                    message_type=message_type,
                    tool_name=tool_name,
                    inject_attempt=inject_attempt,
                )
                await self._send_message_fn(inject_msg)
                started = await self.wait_for_agent_speaking_started(start_timeout)
                if started and self.is_filler_playing:
                    # Filler triggered the event, not the real message
                    await self.wait_for_filler_to_finish(timeout_seconds=end_timeout)
                    started = False
                    continue
                if started:
                    break

            attempt += inject_attempt
            if not started:
                logger.warning(
                    "Playback not detected after direct inject; proceeding",
                    message_type=message_type,
                    tool_name=tool_name,
                    attempt=attempt,
                )
                self._playback_attempts[message_type] = 0
                return PlaybackWaitResult(success=True, started=False, completed=False, attempt=attempt)

            # Speech started — wait for it to complete
            completed = await self.wait_for_agent_speaking_stopped(end_timeout)
            if completed and settle_delay > 0:
                await asyncio.sleep(settle_delay)
            self._playback_attempts[message_type] = 0
            return PlaybackWaitResult(success=True, started=True, completed=completed, attempt=attempt)

        # Success - reset counter
        self._playback_attempts[message_type] = 0
        return PlaybackWaitResult(success=True, started=True, completed=completed, attempt=attempt)


def get_call_state_from_context(ctx: "RunContextWrapper") -> CallStateManager | None:
    """Get CallStateManager from tool context if available.

    Args:
        ctx: The run context wrapper from a tool call.

    Returns:
        CallStateManager instance if available, None otherwise.
    """
    return getattr(ctx.context, "call_state_manager", None)
