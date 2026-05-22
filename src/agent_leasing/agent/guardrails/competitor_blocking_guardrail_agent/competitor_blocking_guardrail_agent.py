import json
from pathlib import Path

from agents import (
    GuardrailFunctionOutput,
    RunContextWrapper,
    TResponseInputItem,
    output_guardrail,
)
from pydantic import BaseModel

from agent_leasing.agent.guardrails.text_utils import extract_text_from_output
from agent_leasing.models.context import SessionScope
from agent_leasing.util.language_utils import localize_guardrail_response


class CompetitorBlockingGuardrailOutput(BaseModel):
    """Output type for the competitor blocking guardrail."""

    reasoning: str
    detected_competitors: list[str]
    is_blocked: bool = True
    safe_response: str

    @property
    def labels(self) -> list[str]:
        return self.detected_competitors


# Load competitor configuration from JSON file
_config_path = Path(__file__).parent / "competitors.json"
with open(_config_path, encoding="utf-8") as f:
    _config = json.load(f)

_competitors: list[str] = _config.get("competitors", [])
_case_sensitive: bool = _config.get("case_sensitive", False)
_block_message: str = _config.get(
    "block_message",
    "I'm sorry, I couldn't complete the previous request. I'm here to help you with your property-related needs. How else can I assist you today?",
)


def _check_for_competitors(text: str) -> list[str]:
    """Check if text contains any competitor mentions."""
    if _case_sensitive:
        return _check_for_competitors_case_sensitive(text)
    else:
        return _check_for_competitors_case_insensitive(text)


def _check_for_competitors_case_sensitive(text: str) -> list[str]:
    """Check if text contains any competitor mentions (case sensitive)."""
    detected = []
    search_text = text

    for competitor in _competitors:
        if competitor in search_text:
            detected.append(competitor)

    return detected


def _check_for_competitors_case_insensitive(text: str) -> list[str]:
    """Check if text contains any competitor mentions (case insensitive)."""
    detected = []
    search_text = text.lower()

    for competitor in _competitors:
        if competitor.lower() in search_text:
            detected.append(competitor)

    return detected


@output_guardrail
async def competitor_blocking_guardrail(
    ctx: RunContextWrapper[SessionScope],
    agent,
    output: str | list[TResponseInputItem] | object,
) -> GuardrailFunctionOutput:
    """Guardrail that blocks mentions of competitor names in AI responses."""

    # Extract text from output
    text_to_check = extract_text_from_output(output)

    # Check for competitor mentions
    detected_competitors = _check_for_competitors(text_to_check)

    # Create output
    if detected_competitors:
        safe_response = await localize_guardrail_response(
            base_response=_block_message,
            guardrail_name="competitor_blocking_guardrail",
            original_content=output,
            content_type="output",
            language_code=ctx.context.language_code,
        )
        guardrail_output = CompetitorBlockingGuardrailOutput(
            reasoning=f"Detected competitor mentions: {', '.join(detected_competitors)}",
            safe_response=safe_response,
            detected_competitors=detected_competitors,
        )

        return GuardrailFunctionOutput(
            output_info=guardrail_output,
            tripwire_triggered=True,
        )

    return GuardrailFunctionOutput(
        output_info=output,
        tripwire_triggered=False,
    )
