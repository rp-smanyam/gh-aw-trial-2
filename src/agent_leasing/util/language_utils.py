from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Literal, Sequence

import structlog
import yaml
from agents import TResponseInputItem

from agent_leasing.agent.guardrails.text_utils import (
    extract_text_from_input,
    extract_text_from_output,
)
from agent_leasing.clients.openai import get_openai_client
from agent_leasing.settings import settings
from agent_leasing.util.language_classifier import classify_language

logger = structlog.getLogger()

GUARDRAILS_RESPONSES_PATH = Path(__file__).parent / "guardrails_reponses.yaml"


@cache
def get_guardrail_message(
    guardrail_name: str,
    language_code: str,
    default_language: str = "en",
) -> str | None:
    """
    Returns localized guardrail message from YAML.
    Cached to avoid repeated disk reads.
    """

    with open(GUARDRAILS_RESPONSES_PATH, encoding="utf-8") as f:
        messages: dict[str, dict[str, str]] = yaml.safe_load(f) or {}

    guardrail = messages.get(guardrail_name)
    if not guardrail:
        return None

    return guardrail.get(language_code) or guardrail.get(default_language)


async def translate_text(text: str, target_language_code: str) -> str:
    """
    Translate text into the target language using the guardrail model.

    Falls back to the original text if translation fails.
    """
    if target_language_code.lower() == "en":
        return text

    try:
        completion = await get_openai_client().chat.completions.create(
            model=settings.guardrail_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Translate the assistant-facing safety message into the target language. "
                        "Return only the translated text without quotes."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Target language code: {target_language_code}\nText: {text}",
                },
            ],
            temperature=0,
        )
        translated = completion.choices[0].message.content.strip()
        return translated or text
    except Exception:
        logger.warning(
            "Failed to translate guardrail safe response",
            exc_info=True,
        )
        return text


async def localize_guardrail_response(
    base_response: str,
    original_content: str | Sequence[TResponseInputItem] | object,
    guardrail_name: str | None = None,
    *,
    content_type: Literal["input", "output"] = "input",
    language_code: str | None = None,
) -> str:
    """
    Return a guardrail safe response in the same language as the triggering content.

    If ``language_code`` is provided (non-empty) AND ``content_type == "output"``,
    the classify_language LLM call is skipped — the agent generated the output in
    the established conversation language, so the context's language_code matches.

    Input guardrails always classify from the user's text: a resident can type in a
    language different from their stored conversation language (first message before
    set_conversation_language fires, or a mid-conversation switch), so the context
    value is not safe to trust there.
    """
    if language_code and content_type == "output":
        resolved_language_code = language_code.lower()
    else:
        try:
            text = (
                extract_text_from_input(original_content)
                if content_type == "input"
                else extract_text_from_output(original_content)
            )
        except Exception:
            logger.warning("Failed to extract text for localization; using base response", exc_info=True)
            return base_response

        try:
            language_result = await classify_language(text)
            resolved_language_code = (language_result.language_code or "en").lower()
        except Exception:
            logger.warning("Failed to classify language for guardrail response", exc_info=True)
            resolved_language_code = "en"

    if resolved_language_code == "en":
        return base_response

    if guardrail_name is not None:
        guardrail_response = get_guardrail_message(guardrail_name, resolved_language_code)
        if guardrail_response is not None:
            return guardrail_response

    return await translate_text(base_response, resolved_language_code)
