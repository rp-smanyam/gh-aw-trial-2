import os
import re
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
    "I'm not authorized to make that commitment. Let me connect you with someone who can help with that request."
)


class Prompts:
    """Agent prompts."""

    def __init__(self):
        """Load prompts from markdown files."""

        with open(os.path.join(os.path.dirname(__file__), "UNAUTHORIZED_PROMISES_GUARDRAIL_AGENT.md")) as f:
            self.AGENT = f.read()


prompts = Prompts()


class UnauthorizedPromisesGuardrailAgentOutput(BaseModel):
    reasoning: str
    is_promise: bool
    safe_response: Final[str] = SAFE_RESPONSE

    @property
    def labels(self) -> list[str]:
        return []


def _build_unauthorized_promises_agent() -> Agent:
    """Build the agent at call time so it picks up the current OpenAI client."""
    return Agent(
        name="Unauthorized Promises Guardrail Agent",
        model=OpenAIChatCompletionsModel(model=settings.guardrail_model, openai_client=get_openai_client()),
        model_settings=ModelSettings(
            temperature=settings.model_temperature,
            extra_args={"service_tier": settings.model_service_tier},
        ),
        instructions=prompts.AGENT,
        output_type=UnauthorizedPromisesGuardrailAgentOutput,
    )


class UnauthorizedPromisesGuardrailOutput(BaseModel):
    """Standard payload for unauthorized promises output guardrail when blocking."""

    reasoning: str
    safe_response: str
    is_promise: bool

    @property
    def labels(self) -> list[str]:
        return []


_BLOCK_REASON = "Response contained content disallowed by Unauthorized Promises policy."


_EVENT_ACCESS_KEYWORDS = (
    "event",
    "party",
    "mixer",
    "rsvp",
    "pool",
    "gym",
    "clubhouse",
    "amenity",
)


def detect_unauthorized_event_promise(text: str) -> bool:
    """Fast, high-confidence detection for unauthorized event/amenity access promises.

    This intentionally only covers a narrow slice of promises (event/amenity access + reservations)
    to avoid broad false positives. The full LLM-based guardrail still runs for everything else.
    """

    normalized = " ".join(text.lower().split())
    if not normalized:
        return False

    if not any(keyword in normalized for keyword in _EVENT_ACCESS_KEYWORDS):
        return False

    # Common compliant phrasing: the agent is describing a process the resident can do themselves.
    # Avoid flagging these as promises.
    if re.search(
        r"\b(you\s+can\s+reserve|to\s+reserve|reserve\s+online|make\s+(?:a\s+)?reservation\s+online)\b",
        normalized,
    ):
        return False

    # Examples this should catch:
    # - "I'll make sure we have a spot reserved for you at the pool party this weekend."
    # - "I can get you into the resident mixer even though RSVPs are closed."
    explicit_access_or_reservation = re.search(
        r"\b("
        r"i\s*(?:'ll|will)\s*(?:make\s+sure|ensure|guarantee|promise)\b"
        r"|i\s+can\s+get\s+you\s+into\b"
        r"|we\s+can\s+get\s+you\s+into\b"
        r"|\bget\s+you\s+into\b"
        r"|i\s*(?:'ll|will|can)\s*(?:reserve|book|hold|save)\b"
        r"|we\s*(?:'ll|will|can)\s*(?:reserve|book|hold|save)\b"
        r"|i\s*(?:have|already|just)\s*reserved\b"
        r"|\breserved\s+for\s+you\b"
        r"|\bsave\s+(?:you\s+)?a\s+spot\b"
        r"|\bhold\s+(?:you\s+)?a\s+spot\b"
        r")",
        normalized,
    )

    return explicit_access_or_reservation is not None


async def _evaluate_unauthorized_promises(
    ctx: RunContextWrapper[SessionScope],
    original_content: Any,
    content_type: Literal["input", "output"],
) -> GuardrailFunctionOutput:
    if content_type == "input":
        text = extract_text_from_input(original_content)
    else:
        text = extract_text_from_output(original_content)

    # DISABLING FOR NOW UNTIL THE PROMPT IS MORE OPTIMIZED
    # REVISIT LATER FOR SOME 100% CONFIDENCE CASES

    # Reduce LLM false negatives for a small, high-confidence set of cases.
    # if detect_unauthorized_event_promise(text):
    #     guardrail_output = UnauthorizedPromisesGuardrailOutput(
    #         reasoning=_BLOCK_REASON,
    #         safe_response=SAFE_RESPONSE,
    #         is_promise=True,
    #     )
    #     return GuardrailFunctionOutput(
    #         output_info=guardrail_output,
    #         tripwire_triggered=True,
    #     )

    result = await Runner.run(
        _build_unauthorized_promises_agent(),
        text,
        context=ctx.context,
    )

    guardrail_decision = result.final_output

    if guardrail_decision.is_promise:
        safe_response = await localize_guardrail_response(
            base_response=guardrail_decision.safe_response,
            guardrail_name="unauthorized_promises_guardrail",
            original_content=original_content,
            content_type=content_type,
            language_code=ctx.context.language_code,
        )
        guardrail_output = UnauthorizedPromisesGuardrailOutput(
            reasoning=guardrail_decision.reasoning,
            safe_response=safe_response,
            is_promise=guardrail_decision.is_promise,
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
async def unauthorized_promises_output_guardrail(
    ctx: RunContextWrapper[SessionScope],
    agent: Agent,
    output: Any,
) -> GuardrailFunctionOutput:
    """Output guardrail that prevents unauthorized promises in agent replies."""

    return await _evaluate_unauthorized_promises(ctx, output, "output")
