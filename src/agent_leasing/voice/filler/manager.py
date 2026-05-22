"""FillerManager — scheduling, selection, delivery, and dead-line detection.

Scheduling and dead-line detection from ``twilio_handler.py``; escalation from ``docs/FILLER_PHRASES.md``.

Owns the full filler lifecycle as a standalone component.  No filler logic
lives in the handler or agent — they interact with FillerManager through
simple method calls and the ``next_speech_is_filler`` / ``filler_item_ids``
observables.

Single toggle: ``VoiceConfig.fillers_enabled`` (maps to ``send_filler_messages``
for parity). When False, all methods are no-ops.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING

import structlog

from agent_leasing.voice.config import VoiceConfig
from agent_leasing.voice.filler.messages import (
    FILLER_ESCALATION_MESSAGE,
    FILLER_HANDOFF_MESSAGE,
    FILLER_IDLE_MESSAGE,
    FILLER_THINKER_ACTIVE_MESSAGE,
)

if TYPE_CHECKING:
    from agent_leasing.voice.coordination.call_state import VoiceCallState
    from agent_leasing.voice.session.manager import SessionManager

logger = structlog.get_logger(__name__)


class FillerManager:
    """Manages filler message scheduling, selection, and delivery.

    Lifecycle::

        fm = FillerManager(config, session_manager, call_state)
        fm.schedule()                 # after greeting, mark events, user msg
        await fm.send_if_due()        # called by the inactivity monitor loop
        fm.on_interrupt()             # on barge-in
        fm.cancel_schedule()          # cancel pending timer (no interrupt)

    Active filler cancellation (mid-speech) is handled by the handler's
    ``cancel_filler()`` VoiceCallbacks method, which coordinates
    ``cancel_schedule()`` + ``InterruptHandler.expecting_cancel_interrupt``
    + ``session_manager.send_interrupt()``.

    The handler checks ``fm.next_speech_is_filler`` when the first audio
    arrives from OpenAI to tag it as filler in call state and tracing.
    """

    def __init__(
        self,
        config: VoiceConfig,
        session_manager: SessionManager,
        call_state: VoiceCallState,
    ) -> None:
        self._config = config
        self._session = session_manager
        self._call_state = call_state

        self._enabled = config.fillers_enabled
        self._mean = max(config.filler_delay_mean_seconds, 0.0)
        self._std = max(config.filler_delay_std_seconds, 0.0)
        self._escalation_enabled = config.filler_escalation_enabled
        self._escalation_threshold = config.filler_escalation_threshold
        self._grace_seconds = config.thinker_response_grace_seconds

        # Scheduling state
        self._next_filler_time: float | None = None
        self._last_audio_time: float = time.time()
        self._consecutive_fillers_without_user_audio: int = 0

        # Observable flags (read by the handler's audio event processor)
        self.next_speech_is_filler: bool = False
        self.filler_item_ids: set[str] = set()

        # Thinker grace period — set by VoiceCallbacks.on_thinker_completed()
        self._thinker_finished_at: float | None = None

        # Suppress until — set by VoiceCallbacks.suppress_filler_temporarily()
        self._suppress_until: float | None = None

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def schedule(self) -> None:
        """Schedule the next filler using Gaussian-distributed delay.

        Called after: greeting, mark events, user message completion,
        audio_end, filler delivery.
        """
        if not self._enabled:
            self._next_filler_time = None
            return
        self._last_audio_time = time.time()
        delay = max(random.gauss(self._mean, self._std), 1.0)
        self._next_filler_time = self._last_audio_time + delay

    def cancel_schedule(self) -> None:
        """Cancel any pending filler (without resetting counters)."""
        self._next_filler_time = None

    # ------------------------------------------------------------------
    # Delivery (called by the inactivity monitor)
    # ------------------------------------------------------------------

    async def send_if_due(
        self,
        *,
        language_code: str = "en",
        thinker_running: bool = False,
        transfer_summary_flow_active: bool = False,
        destructive_handoff_in_progress: bool = False,
        call_active: bool = True,
        session_ready: bool = True,
    ) -> bool:
        """Send a filler message if the scheduled time has elapsed.

        Returns True if a filler was sent, False otherwise.
        """
        if not self._should_send(call_active=call_active, session_ready=session_ready):
            return False

        if self._next_filler_time is None:
            self.schedule()
            return False

        # Reschedule if user is speaking (raw media doesn't count — only VAD)
        if self._call_state.is_user_speaking:
            self.schedule()
            return False

        if time.time() < self._next_filler_time:
            return False

        # Skip if the SDK already has a response in flight — sending a filler
        # message would issue an overlapping ``response.create`` and trigger
        # ``RealtimeError("...already has an active response in progress...")``.
        # Reschedule and try again later instead.
        if self._session.is_response_active():
            logger.debug("Skipping filler: response already in flight")
            self.schedule()
            return False

        # Suppress during thinker response grace period
        if self._thinker_finished_at is not None and self._grace_seconds > 0:
            if time.monotonic() - self._thinker_finished_at < self._grace_seconds:
                logger.debug("Skipping filler: within thinker response grace period")
                return False

        # Suppress during temporary suppression window
        if self._suppress_until is not None and time.monotonic() < self._suppress_until:
            return False

        # Once a destructive handoff tool is actively running, suppress generic
        # fillers entirely. The transfer-summary subflow is the exception: keep
        # using the transfer-specific filler until the handoff completes.
        if destructive_handoff_in_progress and not transfer_summary_flow_active:
            logger.debug("Skipping filler: handoff in progress")
            return False

        message = self._select_message(
            language_code=language_code,
            thinker_running=thinker_running,
            transfer_summary_flow_active=transfer_summary_flow_active,
        )

        try:
            self.next_speech_is_filler = True
            await self._session.send_message(message)
            self.schedule()
            return True
        except Exception:
            self.next_speech_is_filler = False
            logger.debug("Filler: error sending message", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # State updates
    # ------------------------------------------------------------------

    def on_interrupt(self) -> None:
        """Reset on barge-in — user is engaged, reset dead-line counter."""
        self._consecutive_fillers_without_user_audio = 0
        self._next_filler_time = None
        self.next_speech_is_filler = False

    def on_thinker_completed(self) -> None:
        """Record when the thinker finishes (for grace period suppression)."""
        self._thinker_finished_at = time.monotonic()

    def suppress_temporarily(self, seconds: float) -> None:
        """Suppress filler scheduling for *seconds*."""
        self._suppress_until = time.monotonic() + seconds

    def mark_filler_item(self, item_id: str) -> None:
        """Record that *item_id* is from a filler (for tracing)."""
        self.filler_item_ids.add(item_id)

    # ------------------------------------------------------------------
    # Dead-line detection
    # ------------------------------------------------------------------

    @property
    def consecutive_fillers_without_user_audio(self) -> int:
        return self._consecutive_fillers_without_user_audio

    def is_dead_line(self) -> bool:
        """True if too many fillers fired without any user audio."""
        return self._consecutive_fillers_without_user_audio >= self._config.max_consecutive_fillers_without_user_audio

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Full reset for recovery or cleanup."""
        self._next_filler_time = None
        self._consecutive_fillers_without_user_audio = 0
        self.next_speech_is_filler = False
        self.filler_item_ids.clear()
        self._thinker_finished_at = None
        self._suppress_until = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _should_send(self, *, call_active: bool, session_ready: bool) -> bool:
        """Guard checks — return False if fillers should not fire."""
        if not self._enabled:
            return False
        if not call_active or not session_ready:
            return False
        if not self._call_state.can_send_filler():
            return False
        return True

    def _select_message(
        self,
        *,
        language_code: str,
        thinker_running: bool,
        transfer_summary_flow_active: bool,
    ) -> str:
        """Pick the right filler template based on current state."""
        # Count toward dead-line only for user-silence fillers
        if not thinker_running:
            self._consecutive_fillers_without_user_audio += 1

        should_escalate = (
            self._escalation_enabled
            and self._consecutive_fillers_without_user_audio >= self._escalation_threshold
            and not thinker_running
        )

        if transfer_summary_flow_active:
            message = FILLER_HANDOFF_MESSAGE.format(language_code=language_code)
        elif should_escalate:
            logger.warning(
                f"Filler escalation triggered ({self._consecutive_fillers_without_user_audio} consecutive fillers, thinker_running={thinker_running})"
            )
            message = FILLER_ESCALATION_MESSAGE.format(language_code=language_code)
        elif thinker_running:
            message = FILLER_THINKER_ACTIVE_MESSAGE.format(language_code=language_code)
        else:
            message = FILLER_IDLE_MESSAGE.format(language_code=language_code)

        logger.info(
            f"Sending filler (consecutive={self._consecutive_fillers_without_user_audio}, escalated={should_escalate and not transfer_summary_flow_active}, thinker_running={thinker_running})"
        )
        return message
