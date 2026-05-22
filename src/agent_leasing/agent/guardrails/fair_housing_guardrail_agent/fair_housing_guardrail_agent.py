import os
from typing import Any, Final, Literal

from agents import (
    Agent,
    GuardrailFunctionOutput,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunContextWrapper,
    Runner,
    TResponseInputItem,
    input_guardrail,
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


class Prompts:
    """Agent prompts."""

    def __init__(self):
        """Load prompts from markdown files."""

        with open(
            os.path.join(os.path.dirname(__file__), "FAIR_HOUSING_GUARDRAIL_AGENT.md"),
            encoding="utf-8",
        ) as f:
            self.AGENT = f.read()


prompts = Prompts()


class FairHousingAgentOutput(BaseModel):
    reasoning: str
    fair_housing_category: Literal[
        "none",
        "race",
        "color",
        "religion",
        "national origin",
        "sex",
        "familial status",
        "disability",
        "age",
        "marital status",
        "sexual orientation",
        "gender identity",
        "ancestry",
        "citizenship status",
        "military status",
        "other",
    ]
    is_discriminative: bool
    safe_response: Final[str] = "I cannot make judgments about groups of people."

    @property
    def labels(self) -> list[str]:
        if self.fair_housing_category and self.fair_housing_category != "none":
            return [self.fair_housing_category]
        return []


def _build_fair_housing_agent() -> Agent:
    """Build the agent at call time so it picks up the current OpenAI client."""
    return Agent(
        name="Fair Housing Guardrail Agent",
        model=OpenAIChatCompletionsModel(model=settings.guardrail_model, openai_client=get_openai_client()),
        model_settings=ModelSettings(
            temperature=settings.model_temperature,
            extra_args={"service_tier": settings.model_service_tier},
        ),
        instructions=prompts.AGENT,
        output_type=FairHousingAgentOutput,
    )


class FairHousingGuardrailOutput(BaseModel):
    """Standard payload for fair housing output guardrail when blocking."""

    reasoning: str
    safe_response: str
    category: str
    is_discriminative: bool

    @property
    def labels(self) -> list[str]:
        if self.category and self.category != "none":
            return [self.category]
        return []


_BLOCK_REASON = "Response contained content disallowed by Fair Housing policy."


async def _evaluate_fair_housing(
    ctx: RunContextWrapper[SessionScope],
    original_content: Any,
    content_type: Literal["input", "output"],
) -> GuardrailFunctionOutput:
    if content_type == "input":
        text = extract_text_from_input(original_content)
    else:
        text = extract_text_from_output(original_content)

    result = await Runner.run(
        _build_fair_housing_agent(),
        text,
        context=ctx.context,
    )

    guardrail_decision = result.final_output

    if guardrail_decision.is_discriminative:
        safe_response = await localize_guardrail_response(
            base_response=guardrail_decision.safe_response,
            guardrail_name="fair_housing_guardrail",
            original_content=original_content,
            content_type=content_type,
            language_code=ctx.context.language_code,
        )
        guardrail_output = FairHousingGuardrailOutput(
            reasoning=guardrail_decision.reasoning,
            safe_response=safe_response,
            category=guardrail_decision.fair_housing_category,
            is_discriminative=guardrail_decision.is_discriminative,
        )
        return GuardrailFunctionOutput(
            output_info=guardrail_output,
            tripwire_triggered=True,
        )

    return GuardrailFunctionOutput(
        output_info=original_content,
        tripwire_triggered=False,
    )


@input_guardrail
async def fair_housing_input_guardrail(
    ctx: RunContextWrapper[SessionScope],
    agent: Agent,
    input: str | list[TResponseInputItem],
) -> GuardrailFunctionOutput:
    """Guardrail that checks if the user is requesting discriminative information."""

    return await _evaluate_fair_housing(ctx, input, "input")


@output_guardrail
async def fair_housing_output_guardrail(
    ctx: RunContextWrapper[SessionScope],
    agent: Agent,
    output: Any,
) -> GuardrailFunctionOutput:
    """Output guardrail that enforces Fair Housing compliance in agent replies."""

    return await _evaluate_fair_housing(ctx, output, "output")
