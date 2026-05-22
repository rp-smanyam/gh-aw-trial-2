"""ResponseGate — serializes ``response.create`` calls to OpenAI.

Prevents the common error where multiple overlapping ``response.create``
calls confuse the Realtime API.  Also provides a monotonic ``turn_id``
counter that thinker tasks use to detect stale results after a user
interrupts.

Design adopted from:
  - Nick Lackman's ``_response_create_lock`` + ``_response_done`` event
  - Agentix's ``_response_active`` flag + ``_response_done_event``
"""

from __future__ import annotations

import asyncio

import structlog

logger = structlog.get_logger(__name__)


class ResponseGate:
    """Ensures at most one ``response.create`` is in flight at a time.

    Usage by the handler::

        gate = ResponseGate()

        # Before creating a response:
        ok = await gate.acquire(snapshot_turn_id)
        if ok:
            await session.create_response(...)

        # When a response completes (response.done event):
        gate.on_response_completed()

        # On user interrupt (barge-in):
        gate.on_interrupt()
        # -> increments turn_id, releases any waiter

    Usage by the thinker tool::

        snapshot = callbacks.turn_id          # capture before work
        result = await Runner.run(...)        # slow work
        if callbacks.turn_id != snapshot:
            return STALE                      # user interrupted, discard
        ok = await callbacks.request_response(snapshot)
        if not ok:
            return STALE                      # interrupted while waiting
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._response_done = asyncio.Event()
        self._response_done.set()  # No response active initially
        self._turn_id = 0

    @property
    def turn_id(self) -> int:
        """Monotonic counter incremented on every interrupt."""
        return self._turn_id

    async def acquire(self, snapshot_turn_id: int) -> bool:
        """Wait for any active response to finish, then check staleness.

        Args:
            snapshot_turn_id: The ``turn_id`` the caller captured before
                starting its work.

        Returns:
            True if the caller should proceed with ``response.create``.
            False if the turn_id changed (user interrupted) — the caller
            should discard its result.
        """
        async with self._lock:
            # Wait for the previous response to finish
            await self._response_done.wait()

            # Re-check after waiting — the user may have interrupted
            if snapshot_turn_id != self._turn_id:
                logger.debug(f"Response gate: stale request (snapshot={snapshot_turn_id}, current={self._turn_id})")
                return False

            # Mark a response as active
            self._response_done.clear()
            return True

    def on_response_completed(self) -> None:
        """Signal that the current response has finished (``response.done`` event)."""
        self._response_done.set()

    def on_interrupt(self) -> None:
        """Handle a user interrupt — increment turn_id and unblock waiters.

        Any thinker that captured an earlier turn_id will see staleness
        when it tries to ``acquire``.
        """
        self._turn_id += 1
        # Unblock anyone waiting for response_done — the response was cancelled
        self._response_done.set()
        logger.debug(f"Response gate: interrupt (turn_id={self._turn_id})")
