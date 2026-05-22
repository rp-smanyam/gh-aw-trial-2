"""Conversation-level frustration classifier.

Sibling of `language_classifier`. Used on the voice path where the
thinker doesn't see raw caller transcripts and so its
`user_frustrated` structured-output field isn't a reliable signal.
The classifier reads the full conversation transcript and returns a
single verdict per call.

Wired into `log_data_curation_event_for_realtime_history` so it runs
in parallel with the per-message language classification at
end-of-call.
"""

import os

import structlog
from agents import Agent, Runner
from pydantic import BaseModel

from agent_leasing.settings import build_model_settings, settings

logger = structlog.get_logger(__name__)


class Prompts:
    """Agent prompts."""

    def __init__(self):
        with open(
            os.path.join(os.path.dirname(__file__), "FRUSTRATION_CLASSIFIER_AGENT.md"),
            encoding="utf-8",
        ) as f:
            self.AGENT = f.read()


prompts = Prompts()


class FrustrationClassificationOutput(BaseModel):
    """Structured verdict from the frustration classifier."""

    reason: str
    confidence: float
    is_frustrated: bool
    trigger_message: str = ""


frustration_classifier_agent = Agent(
    name="Frustration Classifier Agent",
    model=settings.guardrail_model,
    model_settings=build_model_settings(
        model=settings.guardrail_model,
        effort=None,
        override_model=settings.guardrail_model,
    ),
    instructions=prompts.AGENT,
    output_type=FrustrationClassificationOutput,
)

DEFAULT_FRUSTRATION_CLASSIFICATION_OUTPUT = FrustrationClassificationOutput(
    reason="Default to not-frustrated on empty / failure",
    confidence=0.0,
    is_frustrated=False,
    trigger_message="",
)


async def classify_frustration(transcript: str) -> FrustrationClassificationOutput:
    """Classify the conversation transcript for frustration.

    Soft-fails to `is_frustrated=False` on empty input or any error so
    the broader data-curation flow keeps running. The frustration
    activity is observability, not safety — a missed signal is better
    than a broken Kafka pipeline.
    """
    if not transcript or not transcript.strip():
        return DEFAULT_FRUSTRATION_CLASSIFICATION_OUTPUT

    try:
        result = await Runner.run(frustration_classifier_agent, transcript)
        return result.final_output
    except Exception as e:
        logger.error("Frustration classification failed", error=str(e), exc_info=True)
        return DEFAULT_FRUSTRATION_CLASSIFICATION_OUTPUT
