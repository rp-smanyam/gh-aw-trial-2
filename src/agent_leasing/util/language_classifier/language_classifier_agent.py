"""
A language classifier function using OpenAI API to classify into the ISO 639-1 language codes.

This is only (as of 2025 09 23) used for the RealTime models during the log_data_curation_event_for_realtime_events function, but this could be used for other uses in the future.
"""

import os

import structlog
from agents import (
    Agent,
    Runner,
)
from pydantic import BaseModel

from agent_leasing.settings import build_model_settings, settings

logger = structlog.get_logger(__name__)


class Prompts:
    """Agent prompts."""

    def __init__(self):
        """Load prompts from markdown files."""

        with open(
            os.path.join(os.path.dirname(__file__), "LANGUAGE_CLASSIFIER_AGENT.md"),
            encoding="utf-8",
        ) as f:
            self.AGENT = f.read()


prompts = Prompts()


class LanguageClassificationOutput(BaseModel):
    """Output type for the language classifier."""

    reason: str
    confidence: float
    language_code: str


language_classifier_agent = Agent(
    name="Language Classifier Agent",
    model=settings.guardrail_model,
    model_settings=build_model_settings(
        model=settings.guardrail_model,
        effort=None,
        override_model=settings.guardrail_model,
    ),
    instructions=prompts.AGENT,
    output_type=LanguageClassificationOutput,
)

DEFAULT_LANGUAGE_CLASSIFICATION_OUTPUT = LanguageClassificationOutput(
    reason="Default to English", confidence=1.0, language_code="en"
)


# TODO: refactor this to accept a conversation thread
# e.g., [{"role": "user", "content": "blah"}, {"role": "assistant", "content": "blah"}, {"role": "user", "content": "blah"}]
# and return the language codes for each in a list
async def classify_language(
    text: str,
) -> LanguageClassificationOutput:
    """Classify the language of the given text and return the language code."""

    if not text or not text.strip():
        return DEFAULT_LANGUAGE_CLASSIFICATION_OUTPUT

    try:
        result = await Runner.run(language_classifier_agent, text)
        return result.final_output
    except Exception as e:
        # Fallback to English if classification fails
        logger.error(f"Failed to classify language {text} with exception {e}")
        return DEFAULT_LANGUAGE_CLASSIFICATION_OUTPUT
