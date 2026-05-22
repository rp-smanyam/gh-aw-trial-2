"""
Tool for setting the conversation language code.
"""

from typing import Annotated

import structlog
from agents import RunContextWrapper, function_tool
from langsmith import traceable

from agent_leasing.models.context import SessionScope

logger = structlog.get_logger(__name__)

DESCRIPTION = """Set the conversation language. Silent internal operation — do NOT announce or add spoken output about setting the language. Never say "I'm setting your language" or similar.

Call this when the caller explicitly asks to switch languages.

Parameters:
- language_code: ISO 639-1 language code (e.g., 'en', 'es', 'fr', 'zh')"""


@traceable(run_type="tool", name="set_conversation_language")
async def apply_language_code(
    context: SessionScope,
    language_code: str,
) -> str:
    """Set language_code on a SessionScope. Traced to LangSmith."""
    normalized = language_code.lower().strip()

    previous_language = context.language_code
    context.language_code = normalized

    logger.info(f"Language set from {previous_language} to {context.language_code}")

    return "ok"


@function_tool(description_override=DESCRIPTION)
async def set_conversation_language(
    ctx: RunContextWrapper[SessionScope],
    language_code: Annotated[
        str, "ISO 639-1 language code (e.g., 'en', 'es', 'fr', 'zh', 'pt', 'de', 'it', 'ja', 'ko')"
    ],
) -> str:
    """Set the conversation language code."""
    return await apply_language_code(ctx.context, language_code)
