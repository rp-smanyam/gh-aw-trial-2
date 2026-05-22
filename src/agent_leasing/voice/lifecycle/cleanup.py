"""Call cleanup — cancel tasks, close session, finalize tracing.

Cleanup patterns from ``twilio_handler.py:_cleanup_call`` and ``_cancel_background_tasks``.

Orchestrates the teardown sequence at the end of a call.  The handler
calls ``cleanup_call`` which runs through the steps in the correct order.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


async def cancel_tasks(
    tasks: list[tuple[str, asyncio.Task[None] | None]],
) -> None:
    """Cancel and await a list of named background tasks.

    Args:
        tasks: List of (name, task) tuples.  None or already-done tasks
               are skipped.
    """
    current = asyncio.current_task()
    to_await: list[tuple[str, asyncio.Task[None]]] = []

    for name, task in tasks:
        if not task or task.done() or task is current:
            continue
        logger.debug(f"Cancelling {name} task")
        task.cancel()
        to_await.append((name, task))

    if not to_await:
        return

    try:
        results = await asyncio.gather(*(t for _, t in to_await), return_exceptions=True)
    except asyncio.CancelledError:
        logger.debug("Task cleanup gather cancelled")
        results = []

    for (name, _), result in zip(to_await, results):
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
            logger.warning(f"Error awaiting {name} task: {result}")


async def close_with_timeout(
    coro: Any,
    *,
    timeout: float,
    label: str,
) -> None:
    """Await a coroutine with a timeout, using ``asyncio.shield`` for resilience.

    Ensures the coroutine completes even if the caller's task is externally
    cancelled (e.g. by MCP pool health check cancel scope leak).
    """
    try:
        await asyncio.wait_for(asyncio.shield(coro), timeout=timeout)
    except TimeoutError:
        logger.warning(f"Timed out closing {label}")
    except asyncio.CancelledError:
        logger.warning(f"Interrupted by external cancellation closing {label}")
    except Exception as e:
        logger.warning(f"Error closing {label}: {e}")
