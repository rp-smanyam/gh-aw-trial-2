"""EventDispatcher — splits OpenAI realtime events into inline and deferred paths.

Audio events (latency-critical) are processed inline in the event loop.
Non-audio events are dispatched to a background task via an asyncio.Queue,
preventing slow handlers (LangSmith HTTP, guardrail evaluation) from
blocking audio delivery.

Adopted from VJ's branch deferred-event-queue pattern.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Event types that must be processed inline (latency-critical)
INLINE_EVENT_TYPES = frozenset({"audio", "audio_interrupted", "audio_end"})

# Threshold for logging slow deferred handlers
_SLOW_HANDLER_MS = 100

EventHandler = Callable[[Any], Coroutine[Any, Any, None]]


class EventDispatcher:
    """Routes realtime session events to inline or deferred handlers.

    Usage::

        dispatcher = EventDispatcher()
        dispatcher.register("audio", handle_audio)              # inline
        dispatcher.register("audio_interrupted", handle_barge)  # inline
        dispatcher.register("history_updated", handle_history)  # deferred
        dispatcher.register("guardrail_tripped", handle_guard)  # deferred

        # In the session event loop:
        async for event in session_manager.events():
            await dispatcher.dispatch(event)

        # On shutdown:
        await dispatcher.shutdown()
    """

    def __init__(self) -> None:
        self._handlers: dict[str, EventHandler] = {}
        self._deferred_queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self._deferred_task: asyncio.Task[None] | None = None
        self._running = False

    def register(self, event_type: str, handler: EventHandler) -> None:
        """Register a handler for an event type.

        Inline vs deferred is determined by ``INLINE_EVENT_TYPES`` — the
        caller doesn't need to specify which path.
        """
        self._handlers[event_type] = handler

    def start(self) -> None:
        """Start the deferred event processor background task."""
        if self._running:
            return
        self._running = True
        self._deferred_task = asyncio.create_task(self._deferred_loop(), name="voice_deferred_events")

    async def dispatch(self, event: Any) -> None:
        """Route an event to its handler (inline) or the deferred queue.

        Args:
            event: A ``RealtimeSessionEvent`` from the session.
        """
        event_type: str = getattr(event, "type", "")
        handler = self._handlers.get(event_type)
        if handler is None:
            return

        if event_type in INLINE_EVENT_TYPES:
            await handler(event)
        else:
            self._deferred_queue.put_nowait((event_type, event))

    async def shutdown(self) -> None:
        """Drain the deferred queue and stop the background task."""
        self._running = False
        if self._deferred_task and not self._deferred_task.done():
            # Sentinel to unblock the queue.get()
            self._deferred_queue.put_nowait(("_shutdown", None))
            try:
                await asyncio.wait_for(self._deferred_task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                self._deferred_task.cancel()
                try:
                    await self._deferred_task
                except asyncio.CancelledError:
                    pass
            self._deferred_task = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _deferred_loop(self) -> None:
        """Process deferred events from the queue."""
        try:
            while self._running:
                event_type, event = await self._deferred_queue.get()
                if event_type == "_shutdown":
                    break

                handler = self._handlers.get(event_type)
                if handler is None:
                    continue

                start = time.monotonic()
                try:
                    await handler(event)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning(
                        f"Deferred event handler error for {event_type}",
                        exc_info=True,
                    )
                elapsed_ms = (time.monotonic() - start) * 1000
                if elapsed_ms > _SLOW_HANDLER_MS:
                    logger.warning(f"Slow deferred handler for {event_type} ({round(elapsed_ms, 1)}ms)")
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Deferred event loop error")
