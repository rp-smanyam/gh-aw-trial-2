import os
from typing import Any, Final, Literal

from agents import (
    Agent,
    GuardrailFunctionOutput,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunContextWrapper,
    Runner,
    output_guardrail,
)
from pydantic import BaseModel

from agent_leasing.agent.guardrails.text_utils import (
    extract_text_from_input,
    extract_text_from_output,
)
from agent_leasing.clients.openai import get_openai_client
from agent_leasing.models.context import SessionScope
from agent_leasing.settings import settings
from agent_leasing.util.language_utils import localize_guardrail_response

SAFE_RESPONSE: Final[str] = (
    "I'm not able to provide legal advice or interpret legal matters. "
    "Please consult a qualified attorney for guidance. "
    "I can help with general property information or point you to your lease terms."
)


class Prompts:
    """Agent prompts."""

    def __init__(self):
        """Load prompts from markdown files."""

        with open(os.path.join(os.path.dirname(__file__), "LEGAL_ADVICE_GUARDRAIL_AGENT.md")) as f:
            self.AGENT = f.read()


prompts = Prompts()


class LegalAdviceGuardrailAgentOutput(BaseModel):
    reasoning: str
    is_legal_advice: bool
    safe_response: Final[str] = SAFE_RESPONSE

    @property
    def labels(self) -> list[str]:
        return []


def _build_legal_advice_agent() -> Agent:
    """Build the agent at call time so it picks up the current OpenAI client."""
    return Agent(
        name="Legal Advice Guardrail Agent",
        model=OpenAIChatCompletionsModel(model=settings.guardrail_model, openai_client=get_openai_client()),
        model_settings=ModelSettings(
            temperature=settings.model_temperature,
            extra_args={"service_tier": settings.model_service_tier},
        ),
        instructions=prompts.AGENT,
        output_type=LegalAdviceGuardrailAgentOutput,
    )


class LegalAdviceGuardrailOutput(BaseModel):
    """Standard payload for legal advice output guardrail when blocking."""

    reasoning: str
    safe_response: str
    is_legal_advice: bool

    @property
    def labels(self) -> list[str]:
        return []


_BLOCK_REASON = "Response contained content disallowed by Legal Advice policy."


async def _evaluate_legal_advice(
    ctx: RunContextWrapper[SessionScope],
    original_content: Any,
    content_type: Literal["input", "output"],
) -> GuardrailFunctionOutput:
    if content_type == "input":
        text = extract_text_from_input(original_content)
    else:
        text = extract_text_from_output(original_content)

    result = await Runner.run(
        _build_legal_advice_agent(),
        text,
        context=ctx.context,
    )

    guardrail_decision = result.final_output

    if guardrail_decision.is_legal_advice:
        safe_response = await localize_guardrail_response(
            base_response=guardrail_decision.safe_response,
            guardrail_name="legal_advice_guardrail",
            original_content=original_content,
            content_type=content_type,
            language_code=ctx.context.language_code,
        )
        guardrail_output = LegalAdviceGuardrailOutput(
            reasoning=guardrail_decision.reasoning or _BLOCK_REASON,
            safe_response=safe_response,
            is_legal_advice=guardrail_decision.is_legal_advice,
        )
        return GuardrailFunctionOutput(
            output_info=guardrail_output,
            tripwire_triggered=True,
        )

    return GuardrailFunctionOutput(
        output_info=original_content,
        tripwire_triggered=False,
    )


@output_guardrail
async def legal_advice_output_guardrail(
    ctx: RunContextWrapper[SessionScope],
    agent: Agent,
    output: Any,
) -> GuardrailFunctionOutput:
    """Output guardrail that prevents legal advice or legal interpretations."""

    return await _evaluate_legal_advice(ctx, output, "output")
