"""InterruptHandler — orchestrates the full barge-in interrupt flow.

Stale-result invalidation via turn_id adopted from Nick Lackman's ``RealtimeBridge``.

When the user speaks during AI playback, multiple components need to
react in a coordinated sequence.  This class replaces the scattered
interrupt handling in twilio_handler's ``_handle_realtime_event`` (the
``audio_interrupted`` branch) with a single orchestrated flow.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from agent_leasing.voice.audio.pacer import AudioPacer
    from agent_leasing.voice.audio.playback import PlaybackTracker
    from agent_leasing.voice.coordination.call_state import VoiceCallState
    from agent_leasing.voice.coordination.interaction_policy import InteractionPolicy
    from agent_leasing.voice.filler.manager import FillerManager
    from agent_leasing.voice.session.manager import SessionManager
    from agent_leasing.voice.session.response_gate import ResponseGate
    from agent_leasing.voice.transport.protocol import VoiceTransport

logger = structlog.get_logger(__name__)


class InterruptHandler:
    """Orchestrates the full interrupt (barge-in) sequence.

    The handler calls :meth:`handle_interrupt` when an ``audio_interrupted``
    event arrives from the OpenAI session.  The method checks the active
    ``InteractionPolicy`` and, if the interrupt is not suppressed, executes
    the coordinated teardown:

    1. Clear the audio pacer queue
    2. Tell the transport to stop playback
    3. Record end times for interrupted items (for tracing)
    4. Cancel the active response in the session
    5. Update call state
    6. Increment the turn_id (invalidates in-flight thinker results)
    7. If the interrupt was user-driven (not our own cancel), reset the
       filler dead-line counter and mark the user as speaking.
    """

    def __init__(
        self,
        pacer: AudioPacer,
        transport: VoiceTransport,
        playback: PlaybackTracker,
        session_manager: SessionManager,
        call_state: VoiceCallState,
        filler: FillerManager,
        response_gate: ResponseGate,
    ) -> None:
        self._pacer = pacer
        self._transport = transport
        self._playback = playback
        self._session = session_manager
        self._call_state = call_state
        self._filler = filler
        self._gate = response_gate

        # When True, the next audio_interrupted is from our own cancel
        # (e.g. filler cancellation) — don't mark user as speaking.
        self.expecting_cancel_interrupt = False

    async def handle_interrupt(self, policy: InteractionPolicy) -> None:
        """Process an ``audio_interrupted`` event.

        Args:
            policy: The current interaction policy (may suppress the interrupt).
        """
        # 1. Check policy — greeting / ESR may suppress
        if policy.should_suppress_interrupt(self._call_state):
            logger.debug(f"Interrupt suppressed by {type(policy).__name__}")
            return

        # 2. Clear outbound audio
        self._pacer.clear()
        try:
            await self._transport.send_clear()
        except (RuntimeError, ConnectionError, OSError):
            logger.debug("Skipped transport clear — connection closed")

        # 3. Record end times for items that will never get mark confirmations
        if self._playback.has_pending_items():
            now = datetime.datetime.now(datetime.UTC)
            for item_id in self._playback.pending_item_ids():
                if item_id not in self._playback.message_end_times:
                    self._playback.message_end_times[item_id] = now
            self._playback.clear()

        # 4. Cancel the active response
        await self._session.cancel_response()

        # 5. Update call state
        self._call_state.on_interrupt()

        # 6. Increment turn_id (invalidates in-flight thinker results)
        self._gate.on_interrupt()

        # 7. Determine if this was user speech or our own cancel.
        #    Only reset the filler dead-line counter on actual user speech —
        #    cancel-triggered interrupts (e.g. our own filler cancel) don't
        #    indicate the user is engaged. This matches the legacy handler
        #    in ``twilio_handler.py``.
        if self.expecting_cancel_interrupt:
            logger.debug("Cancel-triggered interrupt (not user speech)")
            self.expecting_cancel_interrupt = False
        else:
            logger.info("User barge-in")
            self._filler.on_interrupt()
            self._call_state.mark_user_speaking_started()
