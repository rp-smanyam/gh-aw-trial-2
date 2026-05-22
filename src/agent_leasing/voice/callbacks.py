"""VoiceCallbacks — the contract between the agent/thinker and the handler.

This protocol replaces the ``ctx._session_handler`` back-channel that currently
couples ``realtime.py`` to ``twilio_handler.py``. The handler implements it;
the agent and thinker tool receive it at construction time.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class VoiceCallbacks(Protocol):
    """Agent-to-handler communication contract.

    Every method is async so implementations can coordinate with asyncio
    primitives (events, locks) without blocking the caller.
    """

    async def schedule_filler(self) -> None:
        """Schedule the next filler message using the configured delay distribution."""
        ...  # pragma: no cover

    async def cancel_filler(self) -> None:
        """Cancel any pending or active filler message.

        Only invoke when audio is actually in flight — issuing a session
        interrupt against a quiet session causes gpt-realtime-2 to regenerate
        the prior assistant audio (issue #1641 duplicate playback).
        """
        ...  # pragma: no cover

    async def on_thinker_started(self) -> None:
        """Notify the handler that a thinker invocation has begun."""
        ...  # pragma: no cover

    async def on_thinker_completed(self) -> None:
        """Notify the handler that a thinker invocation has finished."""
        ...  # pragma: no cover

    async def suppress_filler_temporarily(self, seconds: float) -> None:
        """Suppress filler scheduling for *seconds* (e.g. after thinker responds)."""
        ...  # pragma: no cover

    async def request_response(self, snapshot_turn_id: int) -> bool:
        """Request that the session create a new response.

        Goes through the ``ResponseGate`` to prevent overlapping
        ``response.create`` calls. The caller passes the ``turn_id`` it
        captured before starting work; the gate compares it against the
        current value to detect stale requests from interrupted thinkers.

        Args:
            snapshot_turn_id: The ``turn_id`` the caller observed before
                starting its work.  If it no longer matches the current
                ``turn_id``, the request is stale and returns ``False``.

        Returns:
            True if the response was created, False if the request was
            stale (turn_id changed due to an interrupt).
        """
        ...  # pragma: no cover

    @property
    def turn_id(self) -> int:
        """Current monotonic turn counter.

        Incremented on every interrupt. Thinker tasks snapshot this value
        when dispatched and compare it after completion — if it changed,
        the user interrupted and the result should be discarded.
        """
        ...  # pragma: no cover
