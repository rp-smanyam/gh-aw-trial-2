"""Interaction policies — strategy pattern for phase-specific call behavior.

Adopted from VJ's branch ``InteractionPolicy`` strategy pattern.

Replaces the boolean flags ``_is_initial_greeting`` and
``_esr_suppression_active`` in twilio_handler.py with composable,
testable strategy objects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

import structlog

if TYPE_CHECKING:
    from agent_leasing.voice.coordination.call_state import VoiceCallState

logger = structlog.get_logger(__name__)


@runtime_checkable
class InteractionPolicy(Protocol):
    """Governs interrupt and audio behavior during a specific call phase."""

    def should_suppress_interrupt(self, call_state: VoiceCallState) -> bool:
        """Return True to swallow an incoming interrupt (barge-in)."""
        ...  # pragma: no cover

    def should_accept_audio(self, call_state: VoiceCallState) -> bool:
        """Return True to forward inbound audio to the session."""
        ...  # pragma: no cover

    async def on_playback_complete(self, call_state: VoiceCallState) -> InteractionPolicy:
        """Called when the current agent utterance finishes playing.

        Returns the policy to use for the next phase. This is how
        ``GreetingPolicy`` transitions to ``DefaultPolicy`` after the
        greeting finishes.
        """
        ...  # pragma: no cover


class DefaultPolicy:
    """Normal operation — allow all interrupts, accept all audio."""

    def should_suppress_interrupt(self, call_state: VoiceCallState) -> bool:
        return False

    def should_accept_audio(self, call_state: VoiceCallState) -> bool:
        return True

    async def on_playback_complete(self, call_state: VoiceCallState) -> InteractionPolicy:
        return self


class GreetingPolicy:
    """Suppress interrupts during the initial greeting.

    After the greeting finishes playing, transition to ``DefaultPolicy``.
    This prevents background noise from cutting off the welcome message —
    the same behavior Agentix achieves with ``disable_vad_first_response``.
    """

    def should_suppress_interrupt(self, call_state: VoiceCallState) -> bool:
        return True

    def should_accept_audio(self, call_state: VoiceCallState) -> bool:
        # Accept audio so it's buffered, but don't let it trigger interrupts
        return True

    async def on_playback_complete(self, call_state: VoiceCallState) -> InteractionPolicy:
        logger.debug("Greeting complete, transitioning to DefaultPolicy")
        return DefaultPolicy()


class ESRPolicy:
    """Suppress interrupts during emergency service transfer.

    After the ESR message finishes, transition back to ``DefaultPolicy``.
    """

    def should_suppress_interrupt(self, call_state: VoiceCallState) -> bool:
        return True

    def should_accept_audio(self, call_state: VoiceCallState) -> bool:
        return True

    async def on_playback_complete(self, call_state: VoiceCallState) -> InteractionPolicy:
        logger.debug("ESR message complete, transitioning to DefaultPolicy")
        return DefaultPolicy()
