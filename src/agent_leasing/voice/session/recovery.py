"""Session recovery — rebuild the session after a crash.

Tears down the broken session, creates a fresh one, enters it, and
sends a recovery message so the conversation can continue.
"""

from __future__ import annotations

from typing import Any

import structlog

from agent_leasing.voice.session.manager import SessionManager

logger = structlog.get_logger(__name__)

RECOVERY_MESSAGE = (
    "The agent crashed.  Here is the conversation state:\n{history}\n"
    "Please continue the conversation with the user where it left off as naturally as possible. "
    "Only recognize the error when absolutely necessary (ideally, in a charismatic or disarming way). "
    "**IMPORTANT**: Do not call any tools as part of this response, as that may be the reason for the crash. "
    "**IMPORTANT**: Respond in {language_code}."
)


async def recover_session(
    session_manager: SessionManager,
    agent: Any,
    context: Any,
    language_code: str,
) -> None:
    """Tear down the current session and create a fresh one.

    Args:
        session_manager: The session manager to recover.
        agent: The OpenAI RealtimeAgent to recreate with.
        context: The SessionScope context.
        language_code: Language for the recovery message.
    """
    history = list(session_manager.history)

    logger.info(f"Recovering session (history_len={len(history)})")

    # Tear down the old session
    await session_manager.close()

    # Rebuild
    await session_manager.create(
        agent=agent,
        context=context,
        metadata=session_manager._metadata,
    )
    await session_manager.enter()

    # Send recovery message
    recovery_msg = RECOVERY_MESSAGE.format(
        history=history,
        language_code=language_code,
    )
    logger.info("Sending recovery message")
    await session_manager.send_message(recovery_msg)
