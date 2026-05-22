"""
Post-processing functions for MCP tool outputs.

These functions are applied to tool results before they are returned to the agent.
They can modify, filter, or transform tool outputs as needed.
"""

import json
from decimal import Decimal
from typing import TYPE_CHECKING, Callable

import structlog
from agents import custom_span
from mcp.types import CallToolResult, TextContent

from agent_leasing.agent.guardrails.pii_guardrail.pii_guardrail import detect_pii
from agent_leasing.settings import settings
from agent_leasing.util.tracing_utils import set_span_data
from agent_leasing.util.voice_text_normalizer import normalize_json_values

if TYPE_CHECKING:
    from agent_leasing.models.context import SessionScope

EMERGENCY_PRIORITY_NUMBER = "1"

logger = structlog.getLogger(__name__)


def parse_tool_result_json(result: CallToolResult, *, warn_label: str = "post_processor") -> dict | None:
    """Shared helper: decode `result.content[0].text` as JSON, or return
    None on error/empty/parse-fail with a warning log.
    """
    if result.isError or not result.content:
        return None
    try:
        return json.loads(result.content[0].text)
    except (json.JSONDecodeError, AttributeError, IndexError) as e:
        logger.warning(f"{warn_label}_parse_failed", error=str(e))
        return None


# ALL Post Processors (Guardrails)


def mcp_output_guardrails(result: CallToolResult, **kwargs) -> CallToolResult:
    """Guardrails for MCP tool outputs."""
    if not result.content or len(result.content) == 0:
        return result

    with custom_span(
        "MCP Output Guardrails",
        data={},
    ) as c_span:
        scan_text = result.content[0].text

        # Detect and redact PII in the tool output

        scan_text, pii_detected = _mcp_pii_guardrail(scan_text)

        # limit output length based on settings values

        scan_text, too_long = _mcp_length_guardrail(scan_text)

        set_span_data(
            c_span,
            redacted_text=scan_text,
            pii_detected=pii_detected,
            too_long=too_long,
        )

        result.content[0].text = scan_text

        return result


def _mcp_pii_guardrail(scan_text: str) -> tuple[str, bool]:
    """Guardrails for MCP tool outputs."""

    pii_result = detect_pii(scan_text, redact_pii=True)

    if pii_result.contains_pii:
        logger.info(f"PII detected in MCP tool output: {pii_result.reasoning}")

    # Update the tool result with the redacted text
    scan_text = pii_result.redacted_text

    return scan_text, pii_result.contains_pii


def _mcp_length_guardrail(scan_text: str) -> tuple[str, bool]:
    max_length = settings.mcp_max_output_length

    if len(scan_text) > max_length:
        logger.info(f"Output length exceeded max length of {max_length} characters. Truncating.")
        scan_text = scan_text[:max_length]

        return scan_text, True

    return scan_text, False


# Community Events Post Processors


def modify_events_output(result: CallToolResult, **kwargs) -> CallToolResult:
    """Remove imageUrl and hasUserSignedUp field from fetch_community_events output."""
    if not result.content or len(result.content) == 0:
        return result

    try:
        # Parse the JSON text
        text = result.content[0].text
        data = json.loads(text)

        # Remove imageUrl and hasUserSignedUp from each event
        if "events" in data:
            if data["events"] is None:
                return result
            for event in data["events"]:
                event.pop("imageUrl", None)
                event.pop("hasUserSignedUp", None)

        # Create new result with modified data
        modified_text = json.dumps(data)
        return CallToolResult(
            content=[TextContent(text=modified_text, type="text")],
            structuredContent=data if result.structuredContent else None,
        )
    except Exception as e:
        # If parsing fails, return original
        logger.warning(f"Failed to remove imageUrl and hasUserSignedUp: {e}")
        return result


def add_currency(a: str | None, b: str | None) -> str | None:
    if not a or not b:
        return None
    try:
        val_a = Decimal(a.replace("$", "").replace(",", ""))
        val_b = Decimal(b.replace("$", "").replace(",", ""))
        return f"${val_a + val_b:.2f}"
    except Exception:
        return None


def modify_get_rent_information(result: CallToolResult, **kwargs) -> CallToolResult:
    """Add a total_balance_due attribute."""
    if not result.content or len(result.content) == 0:
        return result

    try:
        # Parse the JSON text
        text = result.content[0].text
        data = json.loads(text)

        # Add total_balance_due
        if "result" in data:
            if data.get("result") is None:
                return result
            balance = data["result"].get("balance")
            pending_balance = data["result"].get("pending_balance")
            data["result"]["total_balance_due"] = add_currency(balance, pending_balance)

        # Create new result with modified data
        modified_text = json.dumps(data)
        return CallToolResult(
            content=[TextContent(text=modified_text, type="text")],
            structuredContent=data if result.structuredContent else None,
        )
    except Exception as e:
        # If parsing fails, return original
        logger.warning(f"Failed to calculate total_balance_due: {e}")
        return result


# SMS Consent Post Processor


def voice_sms_consent_confirmed_post_processor() -> Callable[[CallToolResult], CallToolResult]:
    """Post-processor factory that sets voice_sms_consent_confirmed flag after successful tool call.

    Returns:
        A post-processor function that sets context.voice_sms_consent_confirmed = True
        after user confirms SMS consent (via update_resident_sms_consent_information).
        The returned function receives ``context`` as a keyword argument at
        call time (injected by CachingMCPServer) so it always uses the
        current request's SessionScope, not a stale closure reference.
    """

    def set_flag(result: CallToolResult, context: "SessionScope" = None, **kwargs) -> CallToolResult:
        if not result.isError and context:
            context.voice_sms_consent_confirmed = True
            logger.info("Set voice_sms_consent_confirmed=True after user confirmed SMS consent")
        return result

    return set_flag


# Service Request Priority Post Processor


def sr_priority_post_processor(result: CallToolResult, **kwargs) -> CallToolResult:
    """Strip priority fields from non-emergency create_service_request output.

    Emergency (priority_number "1"): left unchanged so the LLM can see it.
    Non-emergency: removes priority_number and priority_name so the LLM can't leak them.
    """
    if result.isError or not result.content or len(result.content) == 0:
        return result

    try:
        text = result.content[0].text
        data = json.loads(text)

        if data.get("priority_number") == EMERGENCY_PRIORITY_NUMBER:
            # Signal handoff in progress for interrupt suppression in voice
            context: SessionScope | None = kwargs.get("context")
            if context is not None and settings.interrupt_suppression_enabled:
                context.handoff_in_progress = True
                logger.info("handoff_in_progress=True set via sr_priority_post_processor (P1 detected)")
        else:
            data.pop("priority_number", None)
            data.pop("priority_name", None)

        modified_text = json.dumps(data)
        return CallToolResult(
            content=[TextContent(text=modified_text, type="text")],
            structuredContent=data if result.structuredContent else None,
        )
    except (json.JSONDecodeError, AttributeError, IndexError) as e:
        logger.warning(f"Failed to modify SR priority output: {e}")
        return result


# Voice Text Normalization Post Processor


def voice_normalize_post_processor(context: "SessionScope") -> Callable[[CallToolResult], CallToolResult]:
    """Post-processor factory that normalizes tool output fields for VOICE TTS.

    Parses JSON tool output, recursively walks the structure, and applies
    voice-friendly normalization to string values (currency, dates, phones, IDs).

    Args:
        context: The session scope (used to determine channel)

    Returns:
        A post-processor function that normalizes tool output for voice TTS
    """
    from agent_leasing.agent.util import get_channel_from_context

    channel = get_channel_from_context(context)

    def normalize(result: CallToolResult, **kwargs) -> CallToolResult:
        if channel != "VOICE" or result.isError:
            return result

        if not result.content or len(result.content) == 0:
            return result

        try:
            text = result.content[0].text
            data = json.loads(text)

            # Recursively normalize all string values
            normalized_data = normalize_json_values(data)

            modified_text = json.dumps(normalized_data)
            return CallToolResult(
                content=[TextContent(text=modified_text, type="text")],
                structuredContent=normalized_data if result.structuredContent else None,
            )
        except (json.JSONDecodeError, AttributeError, IndexError) as e:
            logger.warning(f"Failed to normalize tool output for voice: {e}")
            return result

    return normalize


def create_voice_normalize_extras(context: "SessionScope", tool_names: list[str]) -> dict[str, list]:
    """Create voice normalization post-processor extras for all given tools.

    Returns an empty dict for non-VOICE channels so it's safe to always call.

    Args:
        context: The session scope (used to determine channel)
        tool_names: List of tool names to add voice normalization for

    Returns:
        Dict mapping tool names to [voice_normalize_processor] for VOICE channel,
        empty dict otherwise.
    """
    from agent_leasing.agent.util import get_channel_from_context

    channel = get_channel_from_context(context)
    if channel != "VOICE":
        return {}
    processor = voice_normalize_post_processor(context)
    return {tool: [processor] for tool in tool_names}


# Utility function for creating processor dictionaries


def create_mcp_post_processors(guardrail_tools: list[str], extras: dict[str, list] | None = None) -> dict[str, list]:
    """Create tool post-processors dict with mcp_output_guardrails for all tools.

    Args:
        guardrail_tools: List of tool names to create processors for
        extras: Optional dict mapping tool names to additional processors to append.
                Can include tools not in the base list.

    Returns:
        Dictionary mapping tool names to list of post-processor functions
    """
    processors = {tool: [mcp_output_guardrails] for tool in guardrail_tools}

    if extras:
        for tool, extra_processors in extras.items():
            if tool not in processors:
                processors[tool] = []
            processors[tool].extend(extra_processors)

    return processors
