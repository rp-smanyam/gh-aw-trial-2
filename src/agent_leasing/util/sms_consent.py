import re
from enum import Enum

import structlog
from agents import Agent, Runner
from pydantic import BaseModel

from agent_leasing.agent.util import extract_tool_result
from agent_leasing.api.model import AskRequest
from agent_leasing.clients.mcp import CachingMCPServer
from agent_leasing.models.context import SessionScope
from agent_leasing.settings import build_model_settings, settings
from agent_leasing.util.tracing_utils import set_span_data

logger = structlog.getLogger()


_SMS_CONSENT_START_PATTERN = re.compile(r"^\s*start\b.*$", re.IGNORECASE)
_SMS_CONSENT_STOP_EXACT_INPUTS = {"stop", "stop text", "stop texting"}


class ConsentStatus(str, Enum):
    """SMS consent status values."""

    GRANTED = "granted"
    REVOKED = "revoked"
    DECLINED = "declined"


class GateResult(BaseModel):
    """Result from SMS consent gate."""

    action: str  # "proceed" or "return_message"
    message: str | None = None


_OPT_OUT_CLASSIFIER_INSTRUCTIONS = """You classify whether a user message indicates they do NOT want to receive SMS text messages from this property management assistant.

# Core Principle
Only classify as opt-out (is_opt_out=true) if the message is CLEARLY and SPECIFICALLY about refusing, stopping, or declining SMS/text messages.

When ambiguous, err on the side of NOT being opt-out (is_opt_out=false). It's better to miss an opt-out (user can use "STOP" keyword) than to incorrectly opt out someone mid-conversation.

# Clear Opt-Out Messages (is_opt_out=true)
These messages are SPECIFICALLY about stopping SMS/text messages:
- "stop texting me"
- "I don't want texts anymore"
- "please stop messaging me"
- "unsubscribe from texts"
- "no more text messages"
- "I want to opt out of SMS"
- "stop sending me messages"
- "don't text me anymore"
- "remove me from texts"
- "I don't want to receive SMS"
- "please don't message me" (when clearly about SMS)

# NOT Opt-Out Messages (is_opt_out=false)
These are conversational responses or declining OTHER things, NOT opting out of SMS:
- "no" - could be answering any question
- "no thanks" - polite decline, not about SMS
- "no thank you" - polite decline, not about SMS
- "I'm good" - declining an offer, not SMS
- "not interested" - could be about tours, amenities, etc.
- "maybe later" - postponing something, not opting out
- "I don't want that" - declining something specific, not SMS
- "nevermind" - changing topic, not opting out
- "No thanks, I need a guest parking pass" - declining one thing, asking another
- "No, I meant something else" - clarification, not opt-out
- "I do not consent" (without SMS context) - too vague

# Detection Logic
1. Is the message SPECIFICALLY mentioning SMS, texts, messages, or texting?
2. Is it expressing refusal, stopping, or declining those messages?
3. If BOTH are true → is_opt_out=true
4. If unclear or ambiguous → is_opt_out=false (err on safe side)

# Examples

**User:** "stop texting me" → is_opt_out=true (clear + specific about texts)
**User:** "I don't want SMS anymore" → is_opt_out=true (clear + specific about SMS)
**User:** "unsubscribe" → is_opt_out=true (clear opt-out intent)
**User:** "please stop messaging me" → is_opt_out=true (clear + about messaging)

**User:** "no thanks" → is_opt_out=false (conversational, not about SMS)
**User:** "no thank you" → is_opt_out=false (conversational, not about SMS)
**User:** "no" → is_opt_out=false (too vague, could be answering anything)
**User:** "I'm good" → is_opt_out=false (declining something, not SMS)
**User:** "not interested" → is_opt_out=false (not specific to SMS)
**User:** "No thanks. Do I have packages?" → is_opt_out=false (declining something else)
**User:** "maybe later" → is_opt_out=false (postponing, not opting out)
"""


async def fetch_sms_consent_status(
    property_mcp_server: CachingMCPServer,
    knock_resident_id: int | str,
) -> str | None:
    """
    Fetch SMS consent status from MCP server.

    Returns:
        Consent status string ("granted", "revoked", "new", etc.) or None if unavailable.
    """
    result = None
    tool_result = None
    try:
        result = await property_mcp_server.call_tool(
            "check_resident_sms_opt_in_status",
            {"resident_id": knock_resident_id},
        )
        tool_result = extract_tool_result(result)

        # Handle case where tool_result is not a dict
        if not isinstance(tool_result, dict):
            logger.warning(
                "Unexpected tool_result type for SMS consent check",
                tool_result_type=type(tool_result).__name__,
                tool_result_value=str(tool_result)[:200],  # Truncate to avoid huge logs
                resident_id=knock_resident_id,
            )
            return None

        status = tool_result.get("sms_consent", {}).get("status", None)
        set_span_data(
            sms_consent_status=status,
            resident_id=knock_resident_id,
        )
        logger.info(f"SMS consent status fetched: {status}")

        return status
    except Exception:
        logger.exception(
            "Failed to fetch SMS consent status",
            resident_id=knock_resident_id,
            result_type=type(result).__name__ if result is not None else "None",
            result_preview=str(result)[:200] if result is not None else "None",
            tool_result_type=type(tool_result).__name__ if tool_result is not None else "None",
            tool_result_preview=str(tool_result)[:200] if tool_result is not None else "None",
        )
        return None


class OptOutClassification(BaseModel):
    """LLM classification result for opt-out intent."""

    is_opt_out: bool
    reasoning: str


opt_out_classifier_agent = Agent(
    name="SMS Opt-Out Classifier",
    model=settings.guardrail_model,
    model_settings=build_model_settings(
        model=settings.guardrail_model,
        effort=None,
        override_model=settings.guardrail_model,
    ),
    instructions=_OPT_OUT_CLASSIFIER_INSTRUCTIONS,
    output_type=OptOutClassification,
)


async def classify_opt_out_intent(user_input: str) -> OptOutClassification:
    """
    Use the Agents SDK to classify if user input indicates opt-out intent.
    Returns structured output with reasoning for logging.
    """
    result = await Runner.run(opt_out_classifier_agent, user_input)
    output = result.final_output
    logger.info(
        "SMS opt-out classification",
        user_input=user_input,
        is_opt_out=output.is_opt_out,
        reasoning=output.reasoning,
    )
    return output


async def _update_sms_consent(mcp_server: CachingMCPServer, resident_id: int | str, consent: bool) -> None:
    """Update SMS consent status via MCP."""

    result = await mcp_server.call_tool(
        "update_resident_sms_consent_information",
        {"request": {"resident_id": int(resident_id), "sms_consent": consent, "source": "renter-ai"}},
    )

    status = "granted" if consent else "revoked"

    if result and result.isError:
        error_text = result.content[0].text if result.content else "Unknown error"
        logger.error(
            "Failed to update SMS consent status",
            resident_id=int(resident_id),
            consent=consent,
            error=error_text,
        )
        set_span_data(
            sms_consent_update_failed=True,
            sms_consent_update_error=error_text,
            resident_id=int(resident_id),
            consent=consent,
        )
        return

    set_span_data(
        sms_consent_updated=status,
        resident_id=int(resident_id),
        consent=consent,
    )

    logger.info(f"SMS consent status updated: {status}")


async def _revoke_consent(mcp_server: CachingMCPServer, context: SessionScope, resident_id) -> None:
    """Helper to revoke consent."""
    await _update_sms_consent(mcp_server, resident_id, consent=False)
    context.sms_consent_status = ConsentStatus.REVOKED


async def _grant_consent(mcp_server: CachingMCPServer, context: SessionScope, resident_id) -> None:
    """Helper to grant consent."""
    await _update_sms_consent(mcp_server, resident_id, consent=True)
    context.sms_consent_status = ConsentStatus.GRANTED


def _extract_sms_start_consent_keyword(user_input: str) -> str | None:
    """Return START when input begins with the START keyword (optionally followed by anything)."""
    match = _SMS_CONSENT_START_PATTERN.search(user_input)
    return "START" if match else None


def _is_stop_opt_out_keyword(user_input: str) -> bool:
    """Return True only when user input exactly matches supported STOP opt-out commands (ignoring trailing punctuation)."""
    # Normalize whitespace and case first.
    normalized_input = " ".join(user_input.strip().lower().split())
    # Strip common terminal punctuation (e.g., "stop.", "stop!", "stop??") without affecting
    # inputs where STOP appears as part of a longer phrase (e.g., "light bulbs stop working").
    normalized_input = re.sub(r"[.!?]+$", "", normalized_input).strip()
    return normalized_input in _SMS_CONSENT_STOP_EXACT_INPUTS


async def handle_sms_consent_gate(
    req: AskRequest,
    context: SessionScope,
    property_mcp_server: CachingMCPServer,
) -> GateResult:
    """
    Pre-agent SMS consent gate - BLOCKS agent unless status is "granted".

    Returns:
        GateResult with action "proceed" (run agent) or "return_message" (skip agent)

    Blocking Behavior:
        - Agent ONLY runs when status is "granted" (or user says START to grant it)
        - All other statuses return a message directly without running agent
    """
    try:
        resident_id = context.ask_request.product_info.knock_resident_id
        user_input = req.prompt.strip()
        start_consent_keyword = _extract_sms_start_consent_keyword(user_input)

        is_first_message = not context.sms_consent_recorded

        logger.info(f"SMS consent gate entered: is_first_message={is_first_message}, user_input='{user_input}'")

        # Always fetch fresh status (don't cache - status may change externally)

        status = await fetch_sms_consent_status(property_mcp_server, resident_id)
        context.sms_consent_status = status
        context.sms_consent_recorded = True
        logger.info(f"Fetched SMS consent status: {status}")

        # Handle START keyword (works from ANY status to grant consent)
        if start_consent_keyword == "START":
            if status != ConsentStatus.GRANTED:
                logger.info("START keyword detected, granting consent")
                await _grant_consent(property_mcp_server, context, resident_id)

            pending_query = getattr(context, "pending_sms_query", None)
            # Always clear the consent prompt flags after START
            context.pending_sms_query = None
            context.sms_needs_consent_prompt = False

            if pending_query:
                logger.info(f"Processing pending query after START: '{pending_query}'")
                # Format prompt to include welcome workflow + original query
                req.prompt = (
                    f"Please greet the user with a welcome message, then answer their question: {pending_query}"
                )
            else:
                # No pending query, just return to agent for welcome only
                logger.info("START alone without pending query")

            return GateResult(action="proceed")

        # GRANTED: Check for opt-out, otherwise proceed normally
        if status == ConsentStatus.GRANTED:  # Ensure context is updated
            if _is_stop_opt_out_keyword(user_input) or await _is_opt_out_intent(user_input):
                logger.info("User opted out - revoking consent")
                await _revoke_consent(property_mcp_server, context, resident_id)
                return GateResult(
                    action="return_message",
                    message=_get_opt_out_message(user_input),
                )
            return GateResult(action="proceed")

        # Status is NOT GRANTED (new, revoked, declined, etc.)
        # Handle STOP keyword or opt-out intent
        if _is_stop_opt_out_keyword(user_input) or (is_first_message and await _is_opt_out_intent(user_input)):
            logger.info("User opted out - revoking consent")
            await _revoke_consent(property_mcp_server, context, resident_id)
            return GateResult(
                action="return_message",
                message=_get_opt_out_message(user_input),
            )

        # Status is NOT granted and user didn't say START — BLOCK agent
        logger.info(f"SMS consent not granted (status: {status}), storing query and requesting consent")
        # Store the LAST query so it can be processed after user types START
        # This overwrites any previous pending query
        context.pending_sms_query = user_input
        context.sms_needs_consent_prompt = True
        return GateResult(
            action="return_message",
            message=_get_consent_request_message(user_input, status),
        )

    except Exception as e:
        logger.error("SMS consent gate failed, setting revoked status", error=str(e), exc_info=True)
        context.sms_consent_status = ConsentStatus.REVOKED
        return GateResult(
            action="return_message",
            message=_get_opt_out_message(""),
        )


async def _is_opt_out_intent(user_input: str) -> bool:
    """
    Check for opt-out intent via LLM classification.

    Returns False on LLM failure (users can still use exact "STOP" keyword).
    If status is not granted, failure leads to consent mode which is safer than guessing.
    """
    try:
        classification = await classify_opt_out_intent(user_input)
        return classification.is_opt_out
    except Exception as e:
        logger.warning(
            "LLM opt-out classification failed, treating as no opt-out (user can use STOP keyword)",
            error=str(e),
        )
        return False


def _get_opt_out_message(user_input: str) -> str:
    """Get opt-out message in appropriate language."""
    language = _detect_language(user_input)
    messages = {
        "en": "You are opted out of SMS. To opt back in, reply START.",
        "es": "No estás inscrito en SMS. Para volver a inscribirte, responde START.",
    }
    return messages.get(language, messages["en"])


def _get_consent_request_message(user_input: str, status: str | None) -> str:
    """
    Get consent request message in appropriate language.

    Different messages for revoked/declined vs new/unknown status.
    """
    language = _detect_language(user_input)

    if status in (ConsentStatus.REVOKED, ConsentStatus.DECLINED):
        messages = {
            "en": "You are opted out of SMS. To opt back in, reply START.",
            "es": "No estás inscrito en SMS. Para volver a inscribirte, responde START.",
        }
    else:
        messages = {
            "en": "You are not opted in to SMS. To opt in, reply START or to opt out, reply STOP.",
            "es": "No estás inscrito en SMS. Para inscribirte, responde START o para rechazar, responde STOP.",
        }
    return messages.get(language, messages["en"])


def _detect_language(user_input: str) -> str:
    """Detect language from user input using simple heuristics. Returns 'en' or 'es'."""
    if len(user_input.strip()) < 10 or user_input.upper() in ("START", "STOP"):
        return "en"

    words = [w.strip(".,!?¿¡") for w in user_input.lower().split()]
    spanish_indicators = {"qué", "cuál", "cómo", "dónde", "quiero", "hola", "gracias"}
    if any(word in spanish_indicators for word in words):
        return "es"

    return "en"
