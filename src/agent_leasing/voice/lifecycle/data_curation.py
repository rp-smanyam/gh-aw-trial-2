"""Data curation logging — snapshots session history and fires a Kafka event.

Data curation from ``twilio_handler.py:_schedule_data_curation_logging``.

Wraps the existing ``log_data_curation_event_for_realtime_history`` utility
so the voice package doesn't import the Kafka stack at module level.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _log_task_exception(task: asyncio.Task[None]) -> None:
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc:
        logger.warning(f"Data curation task failed: {exc}")


async def schedule_data_curation(
    session: Any,
    transcript_cache: dict[str, str],
) -> asyncio.Task[None] | None:
    """Snapshot session history and fire data curation logging as a background task.

    Must be called before the session is closed (needs session._history
    and session._context_wrapper).

    Returns the background task for cleanup tracking, or None on error.
    """
    if not session:
        return None

    try:
        history_snapshot = list(getattr(session, "_history", []))
        context = session._context_wrapper.context
    except Exception as e:
        logger.warning(f"Error capturing data curation snapshot: {e}")
        return None

    try:
        from agent_leasing.util.realtime_util import log_data_curation_event_for_realtime_history

        task = asyncio.create_task(
            log_data_curation_event_for_realtime_history(
                history_snapshot,
                context,
                transcript_cache=dict(transcript_cache),
            )
        )
        task.add_done_callback(_log_task_exception)
        return task
    except Exception as e:
        logger.warning(f"Error scheduling data curation: {e}")
        return None


async def await_data_curation(task: asyncio.Task[None] | None, timeout: float = 10.0) -> None:
    """Wait for the data curation task to finish, cancel if too slow."""
    if not task or task.done():
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except TimeoutError:
        logger.warning("Data curation timed out — cancelling")
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    except asyncio.CancelledError:
        logger.debug("Data curation await cancelled")
    except Exception as e:
        logger.warning(f"Error awaiting data curation: {e}")
