"""Lightweight recovery for the "active response in progress" SDK race.

When ``response.create`` is called while OpenAI is still processing the
previous response, the realtime SDK emits
``RealtimeError(code='conversation_already_has_active_response')``.
A full ``recover_session()`` rebuild is too heavy for this recoverable
race — instead we force-cancel the in-flight response, briefly poll for
confirmation, and retry ``response.create``.

Mirrors v1's handler at ``twilio_handler.py:1125-1190`` (KNCK-38039 / PR
#521).  See https://github.com/openai/openai-agents-python/issues/1907.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from agent_leasing.voice.coordination.interrupt import InterruptHandler
    from agent_leasing.voice.session.manager import SessionManager

logger = structlog.get_logger(__name__)

CANCELLATION_POLL_INTERVAL_SECONDS = 0.05
CANCELLATION_POLL_MAX_ATTEMPTS = 10


async def recover_from_active_response_race(
    session_manager: SessionManager,
    interrupt_handler: InterruptHandler,
) -> None:
    """Force-cancel the in-flight response and retry ``response.create``.

    Skips the retry — leaving the session as-is — if cancellation isn't
    confirmed within ~500ms; retrying without confirmation would hit the
    same error and cause churn.

    The ``InterruptHandler.expecting_cancel_interrupt`` flag is raised
    around the cancel so the resulting ``audio_interrupted`` event isn't
    treated as a user barge-in (which would reset the filler dead-line
    counter and mark the user as speaking).
    """
    interrupt_handler.expecting_cancel_interrupt = True
    try:
        await session_manager.force_cancel_response()

        confirmed = False
        for i in range(CANCELLATION_POLL_MAX_ATTEMPTS):
            await asyncio.sleep(CANCELLATION_POLL_INTERVAL_SECONDS)
            if not session_manager.is_response_active():
                elapsed_ms = int((i + 1) * CANCELLATION_POLL_INTERVAL_SECONDS * 1000)
                logger.info(f"Confirmed response cancellation after {elapsed_ms}ms")
                confirmed = True
                break

        if confirmed:
            await session_manager.create_response(output_modalities=["audio"])
            logger.info("Retried response.create after cancellation")
        else:
            logger.warning("Cancellation not confirmed after 500ms; skipping response.create retry")
    except Exception as e:
        logger.warning(f"Failed to recover from active response conflict: {e}")
    finally:
        interrupt_handler.expecting_cancel_interrupt = False
