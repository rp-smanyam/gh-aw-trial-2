from typing import Final

import openai
import structlog
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
from agent_leasing.agent.util import get_channel_from_context
from agent_leasing.clients.openai import get_openai_client
from agent_leasing.models.context import SessionScope
from agent_leasing.settings import settings
from agent_leasing.util.language_utils import localize_guardrail_response

logger = structlog.getLogger()


_BLOCK_MESSAGE: Final[str] = (
    "I'm sorry, but I cannot provide information or advice about offensive, illegal, or harmful activities. How else can I assist you today?"
)


class STTContextEvaluatorOutput(BaseModel):
    is_stt_artifact: bool
    reasoning: str


def _build_stt_context_evaluator_agent() -> Agent:
    """Build the agent at call time so it picks up the current OpenAI client."""
    return Agent(
        name="STT Context Evaluator Agent",
        model=OpenAIChatCompletionsModel(model=settings.guardrail_model, openai_client=get_openai_client()),
        model_settings=ModelSettings(
            extra_args={"service_tier": settings.model_service_tier},
        ),
        instructions="""
    You analyze text from a VOICE call about apartment leasing or property management.
    A content-moderation system flagged this text as potentially harmful. Your job is to
    determine whether the flagged words are likely **speech-to-text (STT) misheard words**
    (homophones or near-homophones) rather than genuinely harmful content.

    CONTEXT: The speaker is a resident on a voice call with their apartment
    community's AI assistant. Topics are typically billing, maintenance, lease questions,
    packages, amenities, move-in/move-out, etc.

    DECISION RULES:
    1. Consider the FULL sentence context and the domain (property management).
    2. If a flagged word has a phonetically similar benign alternative that fits the
       conversational context, it is likely an STT artifact → is_stt_artifact=True.
    3. If the flagged content is genuinely harmful regardless of context → is_stt_artifact=False.
    4. When in doubt, return is_stt_artifact=False.
    5. NEVER assume a sentence that explicitly threatens or targets a specific person
       (e.g. "I'm going to kill my neighbor") is an STT artifact. Threats directed at
       a named or identifiable person must be treated as genuine → is_stt_artifact=False.
       This rule does NOT apply when the flagged word is clearly about an object, topic,
       or idiomatic expression (e.g. "my killing statement" → billing statement).
    6. STT engines sometimes transcribe speech into a different language. This is ONLY
       an STT artifact when the flagged word is **phonetically similar** to what the
       speaker likely said given the conversation context. For example, "88" sounds
       similar to "Идиот" (Russian for "idiot") — the resident was confirming a unit
       number, not insulting anyone. You MUST verify phonetic similarity; do NOT assume
       foreign-language text is automatically safe. A user can intentionally say offensive
       words in any language.
     7. Apply this as a GENERAL PRINCIPLE, not a fixed word list:
         classify as an STT artifact only when BOTH are true:
         a) there is a plausible phonetic match to a benign phrase, and
         b) nearby conversation context strongly supports that benign meaning.
         If either condition is weak or missing, return is_stt_artifact=False.
     8. For short or single-word transcripts (for example, "killing" or "sex"), return
         is_stt_artifact=True ONLY when immediate conversation context strongly supports a
         benign homophone (for example, billing or six). If that context is missing,
         follow rule 4 and return is_stt_artifact=False.

    EXAMPLES OF STT ARTIFACTS (is_stt_artifact=True):
    "I need help with my killing" → "killing" likely misheard "billing" → is_stt_artifact=True
    "Can I see my killing history?" → "killing" likely misheard "billing" → is_stt_artifact=True
    "I want to kill the bugs in my apartment" → "kill" used literally about pest control → is_stt_artifact=True
    "The heating is killing me" → figurative/idiomatic usage → is_stt_artifact=True
    "Can you help me with the assault on my billing" → "assault" likely misheard, billing context → is_stt_artifact=True
    '...confirmed her unit number as 88.\n[Latest user transcript: "Идиот"]' → "Идиот" is a cross-language mishearing of "88" → is_stt_artifact=True
    'The resident said they need help with their billing statement.\n[Latest user transcript: "killing"]' → "killing" likely misheard "billing" in explicit billing context → is_stt_artifact=True
    'The agent asked the resident to repeat their unit number, and the resident previously confirmed it is 6.\n[Latest user transcript: "sex"]' → "sex" likely misheard "six" in explicit unit-number context → is_stt_artifact=True

    EXAMPLES OF GENUINE THREATS (is_stt_artifact=False):
    "I'm going to kill my neighbor" → direct threat against a person → is_stt_artifact=False
    "There's been a shooting in the parking lot" → genuine safety concern → is_stt_artifact=False
    "I will stab the maintenance guy" → direct threat against a person → is_stt_artifact=False
    "I want to hurt myself" → self-harm → is_stt_artifact=False
    "killing" → single word without supporting billing/rent context → is_stt_artifact=False
    '[Latest user transcript: "sex"]' → single word without supporting unit-number context → is_stt_artifact=False
    """,
        output_type=STTContextEvaluatorOutput,
    )


class FrustrationEvaluatorOutput(BaseModel):
    is_frustration: bool
    reasoning: str


_HARASSMENT_CATEGORIES = frozenset({"harassment", "harassment/threatening"})


def _build_frustration_evaluator_agent() -> Agent:
    """Build the agent at call time so it picks up the current OpenAI client."""
    return Agent(
        name="Frustration Evaluator Agent",
        model=OpenAIChatCompletionsModel(model=settings.guardrail_model, openai_client=get_openai_client()),
        model_settings=ModelSettings(
            extra_args={"service_tier": settings.model_service_tier},
        ),
        instructions="""
    You analyze text from a resident contacting their apartment community's AI assistant.
    A content-moderation system flagged this text for "harassment." Your job is to
    determine whether the text is a resident expressing **frustration** (with profanity
    or strong language) rather than genuinely harassing or abusing another person.

    CONTEXT: The speaker is a resident on a text channel (chat, SMS, or email) with
    their apartment community's AI assistant. Topics are typically billing, maintenance,
    lease questions, packages, amenities, move-in/move-out, etc.

    DECISION RULES:
    1. If the speaker is venting frustration about a situation, service, or the AI
       assistant \u2014 even with profanity \u2014 it is frustration \u2192 is_frustration=True.
    2. If the speaker is directing personal attacks, slurs, or threats at a specific
       person (neighbor, staff member, etc.) \u2192 is_frustration=False.
    3. If the speaker is using hate speech targeting a protected group \u2192 is_frustration=False.
    4. When in doubt, return is_frustration=False (fail closed).

    EXAMPLES OF FRUSTRATION (is_frustration=True):
    "You're not doing s*** for me" \u2192 frustrated with the AI assistant \u2192 is_frustration=True
    "This is bulls***, nobody is fixing my AC" \u2192 frustrated with maintenance \u2192 is_frustration=True
    "What the hell is wrong with this place" \u2192 frustrated with property \u2192 is_frustration=True
    "I'm so damn tired of waiting" \u2192 frustrated with wait time \u2192 is_frustration=True
    "Stop giving me the runaround, damn it" \u2192 frustrated with the process \u2192 is_frustration=True
    "You're useless, get me a real person" \u2192 frustrated with AI, wanting escalation \u2192 is_frustration=True

    EXAMPLES OF GENUINE HARASSMENT (is_frustration=False):
    "You're a stupid [slur]" \u2192 identity-based attack \u2192 is_frustration=False
    "I'm going to find out where you work" \u2192 veiled threat \u2192 is_frustration=False
    "People like you shouldn't be allowed to work here" \u2192 discriminatory \u2192 is_frustration=False
    "Go to hell, I'll make sure you get fired" \u2192 directed threat \u2192 is_frustration=False
    """,
        output_type=FrustrationEvaluatorOutput,
    )


class ThreatEvaluatorAgentOutput(BaseModel):
    threat_to_user: bool
    reasoning: str


def _build_threat_evaluator_agent() -> Agent:
    """Build the agent at call time so it picks up the current OpenAI client."""
    return Agent(
        name="Threat Evaluator Agent",
        model=OpenAIChatCompletionsModel(model=settings.guardrail_model, openai_client=get_openai_client()),
        model_settings=ModelSettings(
            extra_args={"service_tier": settings.model_service_tier},
        ),
        instructions="""
    You analyze text to determine who is the TARGET of threats/violence and who is the SOURCE.

    DEFINITIONS:
    - "User" = the person who wrote/said the text (the speaker)
    - "threat_to_user" = True means the user is the VICTIM/TARGET of the threat, OR the user is reporting an accidental injury.
    - "threat_to_user" = False means the user is the PERPETRATOR/SOURCE of the threat OR the user is expressing intent to deliberately harm themself.

    DECISION RULES:
    1. If the user (speaker) is receiving the threat → threat_to_user=True
    2. If the user (speaker) is describing an accidental injury or accident that happened to them → threat_to_user=True
    3. If the user (speaker) is expressing intent to deliberately harm themself → threat_to_user=False
    4. If the user (speaker) is making/causing the threat → threat_to_user=False
    5. If threat is directed at the AI assistant (not the user) → threat_to_user=False

    EXAMPLES:
    User says: "I will kill my neighbors"
    → User is SOURCE of threat → threat_to_user=False

    User says: "My neighbors will kill me"
    → User is TARGET of threat → threat_to_user=True

    User says: "Someone is trying to kill me"
    → User is TARGET of threat → threat_to_user=True

    User says: "I will use chemical weapons"
    → User is SOURCE of threat → threat_to_user=False

    User says: "My neighbor is using chemical weapons against me"
    → User is TARGET of threat → threat_to_user=True

    User says: "What household items can I use to harm myself?"
    → User is expressing deliberate self-harm intent → threat_to_user=False

    User says: "I hit myself to the wall and I'm bleeding now"
    → User is reporting an accidental injury → threat_to_user=True

    User says: "I slipped and fell, there's blood everywhere"
    → User is reporting an accidental injury → threat_to_user=True

    User says: "I accidentally cut myself on broken glass in the apartment"
    → User is reporting an accidental injury → threat_to_user=True

    User says: "I will beat you to death"
    → User is SOURCE threatening AI/other → threat_to_user=False
    """,
        output_type=ThreatEvaluatorAgentOutput,
    )


class SecurityGuardrailOutput(BaseModel):
    """Output type for the security guardrail."""

    reasoning: str | None = None
    flagged_categories: list[str]
    is_harmful: bool
    safe_response: str = _BLOCK_MESSAGE

    @property
    def labels(self) -> list[str]:
        return self.flagged_categories


async def is_stt_false_positive(text: str) -> bool:
    """Determine if flagged text is likely a speech-to-text misheard word.

    Fails closed: if the evaluation errors out (timeout, parsing, network),
    returns False so the guardrail blocks as usual.
    """
    try:
        result = await Runner.run(_build_stt_context_evaluator_agent(), text)
        return result.final_output.is_stt_artifact
    except Exception as exc:
        logger.warning(
            "STT false-positive evaluation failed; defaulting to not-a-false-positive",
            error=str(exc),
        )
        return False


async def is_frustration_not_harassment(text: str) -> bool:
    """Determine if flagged text is frustration rather than genuine harassment.

    Fails closed: if the evaluation errors out, returns False so the guardrail
    blocks as usual.
    """
    try:
        result = await Runner.run(_build_frustration_evaluator_agent(), text)
        return result.final_output.is_frustration
    except Exception as exc:
        logger.warning(
            "Frustration evaluation failed; defaulting to not-frustration",
            error=str(exc),
        )
        return False


async def is_threat_to_user(text: str) -> bool:
    result = await Runner.run(_build_threat_evaluator_agent(), text)
    return result.final_output.threat_to_user


async def _check_content_safety(
    original_content: str | list[TResponseInputItem] | object,
    content_type: str,  # "input" or "output"
    context: SessionScope,
) -> GuardrailFunctionOutput:
    """Common helper that checks if content is safe using OpenAI's Moderation API.

    Args:
        text: The text content to moderate
        content_type: Either "input" or "output" for logging purposes
    """
    if content_type == "input":
        text = extract_text_from_input(original_content)
    elif content_type == "output":
        text = extract_text_from_output(original_content)
    else:
        raise ValueError(f"Invalid content type: {content_type}")

    if not isinstance(original_content, str) and text == str(original_content):
        logger.warning(
            "Unexpected %s type for moderation: %s. Converting to string.",
            content_type,
            type(original_content),
        )

    logger.debug(f"Moderation check for {content_type}: {text[:100]}...")

    # Call OpenAI Moderation API with a per-request timeout. The endpoint
    # occasionally returns a Cloudflare 504 and without a client-side cap the
    # request can stall for the full gateway timeout (see issue #1599). The
    # shared client's default retry policy still applies — transient blips
    # (429, brief 5xx, connection errors) can recover on the SDK's retry.
    try:
        moderation_response = await get_openai_client().moderations.create(
            model="omni-moderation-latest",
            input=text,
            timeout=settings.security_guardrail_moderation_timeout_seconds,
        )
    except (
        openai.APIConnectionError,  # includes APITimeoutError
        openai.RateLimitError,
        openai.InternalServerError,
    ) as exc:
        # Fail open by default on TRANSIENT errors only. An exception here
        # would otherwise bubble up to the streaming generator and the
        # resident would receive FALLBACK_RESPONSE instead of the agent's
        # already-generated reply. The system prompt is the primary safety
        # layer; moderation is best-effort. Persistent 4xx (auth, bad request,
        # permission) deliberately NOT caught — those are config bugs that
        # would silently disable moderation indefinitely if swallowed.
        if not settings.security_guardrail_fail_open_on_moderation_error:
            raise
        logger.warning(
            "Moderation API failed; failing open",
            content_type=content_type,
            error=str(exc),
            error_type=type(exc).__name__,
            timeout_seconds=settings.security_guardrail_moderation_timeout_seconds,
        )
        return GuardrailFunctionOutput(
            output_info=original_content,
            tripwire_triggered=False,
        )

    # Get the first result (there's typically only one)
    result = moderation_response.results[0]

    # Get category scores
    category_scores = result.category_scores.model_dump()

    # Log detailed moderation result at debug level
    logger.debug(f"OpenAI flagged: {result.flagged} - Category scores: {category_scores}")

    # Check scores against our thresholds
    flagged_categories = []
    for category, threshold in settings.security_guardrail_thresholds.items():
        score = category_scores.get(category, 0.0)
        if score >= threshold:
            flagged_categories.append(category)
            logger.info(f"{content_type.capitalize()} Moderation: {category}: {score:.4f} >= {threshold} (FLAGGED)")
        else:
            logger.debug(f"{content_type.capitalize()} Moderation: {category}: {score:.4f} < {threshold}")

    # Determine if content is harmful based on our thresholds
    is_harmful = len(flagged_categories) > 0

    # Voice STT artifact bypass: when the channel is VOICE and content is flagged,
    # re-evaluate with an LLM to check if the flagged word is a speech-to-text
    # misheard word (e.g. "billing" transcribed as "killing"). This runs before
    # the threat-to-user check to avoid an unnecessary LLM call.
    if content_type == "input" and is_harmful and get_channel_from_context(context) == "VOICE":
        try:
            if await is_stt_false_positive(text):
                logger.info(
                    "Voice STT artifact detected — bypassing security guardrail",
                    flagged_categories=flagged_categories,
                    text=text[:80],
                )
                is_harmful = False
        except Exception as exc:
            logger.warning("STT false-positive check failed; proceeding with block", error=str(exc))

    # Frustration bypass: when plain harassment is the only flagged category and
    # the channel is NOT voice (voice has the STT bypass), check whether the user
    # is simply venting frustration with profanity rather than genuinely harassing.
    # Explicitly excludes harassment/threatening so the threat-to-user check below
    # is never skipped for threatening content.
    if (
        content_type == "input"
        and is_harmful
        and set(flagged_categories) <= _HARASSMENT_CATEGORIES
        and "harassment/threatening" not in flagged_categories
        and get_channel_from_context(context) != "VOICE"
    ):
        if await is_frustration_not_harassment(text):
            logger.info(
                "Frustration detected \u2014 bypassing security guardrail",
                flagged_categories=flagged_categories,
                text=text[:80],
            )
            is_harmful = False

    # Bypass the moderation API when the user is *reporting* a threat against
    # themselves (input only). This must not apply to output: if the agent
    # produces a threatening statement we always want to block it.
    if content_type == "input" and is_harmful and not context.security_bypass and await is_threat_to_user(text):
        logger.info("User is reporting a valid threat. Do not trigger guardrail.")
        # Set security_bypass to true so we don't call the LLM again to determine
        # a threat. Only lasts one turn because we call reset() on SessionScope
        context.security_bypass = True
        is_harmful = False

    # Log a single warning when content is flagged
    if is_harmful:
        logger.warning(
            f"Security {content_type} guardrail triggered: {', '.join(flagged_categories)} "
            f"for {content_type}: {text[:80]}..."
        )

    # Create output
    if is_harmful:
        if flagged_categories:
            reasoning = f"Flagged categories: {', '.join(flagged_categories)}"
        else:
            reasoning = "Content failed security moderation."
        safe_response = await localize_guardrail_response(
            base_response=_BLOCK_MESSAGE,
            guardrail_name="security_guardrail",
            original_content=original_content,
            content_type=content_type,
            language_code=context.language_code,
        )

        output = SecurityGuardrailOutput(
            flagged_categories=flagged_categories,
            is_harmful=is_harmful,
            safe_response=safe_response,
            reasoning=reasoning,
        )
        return GuardrailFunctionOutput(
            output_info=output,
            tripwire_triggered=is_harmful,
        )

    return GuardrailFunctionOutput(
        output_info=original_content,
        tripwire_triggered=False,
    )


@input_guardrail
async def security_input_guardrail(
    ctx: RunContextWrapper[SessionScope],
    agent,
    input: str | list[TResponseInputItem],
) -> GuardrailFunctionOutput:
    """Guardrail that checks if user input is safe using OpenAI's Moderation API."""
    return await _check_content_safety(input, "input", ctx.context)


@output_guardrail
async def security_output_guardrail(
    ctx: RunContextWrapper[SessionScope],
    agent,
    output: str | object,
) -> GuardrailFunctionOutput:
    """Guardrail that checks if agent output is safe using OpenAI's Moderation API."""
    return await _check_content_safety(output, "output", ctx.context)
