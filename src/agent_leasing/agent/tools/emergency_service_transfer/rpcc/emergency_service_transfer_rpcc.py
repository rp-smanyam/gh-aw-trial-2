"""Emergency service transfer tool - RPCC implementation."""

from __future__ import annotations

import asyncio
import copy
import os
import unicodedata
from typing import Annotated, Any
from urllib.parse import quote

import phonenumbers
import structlog
from agents import FunctionTool, RunContextWrapper, function_tool
from langsmith import traceable
from twilio.rest import Client as TwilioClient

from agent_leasing.agent.tools.emergency_service_transfer.description_helper import render_tool_description
from agent_leasing.agent.util import SessionScope, get_channel_from_context
from agent_leasing.api.model import HandoffReasonCode
from agent_leasing.kafka.task_activity.emit import publish_task_activity
from agent_leasing.kafka.task_activity.extractors import extract_handoff_events
from agent_leasing.models.context import HandoffResult
from agent_leasing.settings import settings
from agent_leasing.util.call_state_manager import get_call_state_from_context
from agent_leasing.util.twilio_util import get_twilio_credentials

logger = structlog.get_logger(__name__)

# Constants
PLAYBACK_END_TIMEOUT_SECONDS = 45.0
MAX_SIP_SUMMARY_LENGTH = 256

VOICE_TRANSFER_SUCCESS = "Call transferred to RPCC maintenance team."
NONVOICE_TRANSFER_SUCCESS = "Outbound call initiated to the resident — connecting them to RPCC maintenance team."
DISPATCH_ERROR_MESSAGE = "Failed to transfer to RPCC maintenance team. Please escalate to a human teammate."

_description_path = os.path.join(os.path.dirname(__file__), "EMERGENCY_SERVICE_TRANSFER_RPCC_DESCRIPTION.md")

with open(_description_path, encoding="utf-8") as f:
    _DESCRIPTION_TEMPLATE = f.read().strip()

EMERGENCY_SERVICE_TRANSFER_RPCC_DESCRIPTION = _DESCRIPTION_TEMPLATE


def get_emergency_service_transfer_rpcc_fxn(context: SessionScope | None = None) -> FunctionTool:
    """Get the RPCC emergency service transfer tool with rendered description."""
    tool = copy.copy(emergency_service_transfer_rpcc)
    tool.description = render_tool_description(_DESCRIPTION_TEMPLATE, context)
    return tool


def _sanitize_summary_for_sip_header(summary: str) -> str:
    """Normalize summary text so it is safe to embed in the SIP metadata header."""
    ascii_summary = (
        unicodedata.normalize("NFKD", summary)
        .encode("ascii", "ignore")
        .decode("ascii")
        .replace("|", "/")
        .replace(";", ",")
    )
    collapsed_summary = " ".join(ascii_summary.split())
    return collapsed_summary[:MAX_SIP_SUMMARY_LENGTH].strip()


def _build_sip_uri(sip_endpoint: str, property_id: str, caller: str, callee: str, summary: str) -> str:
    """Build the full SIP URI with pipe-delimited X-User-to-User header.

    Format: sip_endpoint?X-User-to-User={PropertyID}|{ResidentNumber}|{ResidentAINumber}|{CallSummary};encoding=ascii
    The entire query value (including ;encoding=ascii) is URL-encoded.
    """
    # ;encoding=ascii is part of the header value, inside the URL encoding
    safe_summary = _sanitize_summary_for_sip_header(summary)
    header_value = f"{property_id}|{caller}|{callee}|{safe_summary};encoding=ascii"
    return f"{sip_endpoint}?X-User-to-User={quote(header_value)}"


def _build_sip_transfer_twiml(sip_uri: str) -> str:
    """Build TwiML to transfer a call to the RPCC SIP endpoint."""
    return f'<Response><Pause length="1"/><Dial><Sip>{sip_uri}</Sip></Dial></Response>'


def parse_and_validate_phone_number(phone: str) -> phonenumbers.PhoneNumber:
    """Parse and validate a phone number. Raises ValueError on invalid input."""
    try:
        parsed = phonenumbers.parse(phone, "US")
        if not phonenumbers.is_valid_number(parsed):
            raise ValueError(f"Invalid phone number: {phone}.")
        return parsed
    except phonenumbers.NumberParseException as e:
        raise ValueError(f"Failed to parse phone number '{phone}': {e}") from e


def _national(parsed: phonenumbers.PhoneNumber) -> str:
    return str(parsed.national_number)


def _e164(parsed: phonenumbers.PhoneNumber) -> str:
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _safe_national(phone: str | None) -> str:
    """Extract national number for SIP header metadata.

    Falls back to the raw string on parse failure, or "empty" if missing.
    """
    if not phone:
        return "empty"
    try:
        return _national(parse_and_validate_phone_number(phone))
    except ValueError:
        logger.warning("Could not parse phone for SIP header, using raw value")
        return phone


def _get_twilio_client() -> TwilioClient:
    """Create and return a Twilio client using configured credentials."""
    api_key, api_secret, account_sid = get_twilio_credentials()
    return TwilioClient(api_key, api_secret, account_sid)


@traceable(run_type="tool", name="emergency_service_transfer_rpcc")
async def _emergency_service_transfer_rpcc_impl(
    ctx: RunContextWrapper[Any],
    service_request_summary: Annotated[
        str,
        "A detailed description of the emergency including what happened, location, and access details.",
    ],
    resident_phone: Annotated[
        str | None,
        "The resident's callback phone number in E.164 format (e.g., +15555555555). Required for non-voice channels.",
    ] = None,
):
    ctx.context.call_ended_by_agent = True
    channel = get_channel_from_context(ctx.context)
    voice = channel == "VOICE"
    if voice:
        # Activate interrupt suppression so the safety message can't be cut off.
        # Reset in finally so every exit path (early return, exception) clears it.
        ctx.context.esr_initiated = True
    try:
        lo_property_id = ctx.context.ask_request.product_info.lo_property_id

        if voice:
            call_state = get_call_state_from_context(ctx)
            if call_state is not None:
                playback_result = await call_state.wait_for_message_playback(
                    "transfer",
                    tool_name="emergency_service_transfer_rpcc",
                    end_timeout_seconds=PLAYBACK_END_TIMEOUT_SECONDS,
                )
                if not playback_result.completed:
                    logger.warning("Timed out waiting for transfer message playback to complete")

            if not settings.rpcc_sip_endpoint:
                logger.error("RPCC SIP endpoint not configured — set RPCC_SIP_ENDPOINT to enable RPCC transfers")
                return DISPATCH_ERROR_MESSAGE

            call_sid = ctx.context.ask_request.product_info.call_sid
            if not call_sid:
                return DISPATCH_ERROR_MESSAGE

            caller = ctx.context.ask_request.product_info.caller
            if not caller:
                logger.error("Missing caller for RPCC voice transfer — RPCC needs the resident's number")
                return DISPATCH_ERROR_MESSAGE

            sip_uri = _build_sip_uri(
                settings.rpcc_sip_endpoint,
                lo_property_id,
                _safe_national(caller),
                _safe_national(ctx.context.ask_request.product_info.callee),
                service_request_summary,
            )
            twiml = _build_sip_transfer_twiml(sip_uri)

            twilio_client = _get_twilio_client()
            twilio_client.calls(call_sid).update(twiml=twiml)
            logger.info("Transferred live call to RPCC via SIP", call_sid=call_sid)
            ctx.context.handoff_result = HandoffResult(
                tool="emergency_service_transfer_rpcc",
                reason=HandoffReasonCode.EMERGENCY.value,
                routing_confirmed=True,
                summary=service_request_summary,
            )
            publish_task_activity(
                extract_handoff_events,
                service_request_summary,
                ctx.context,
                reason=HandoffReasonCode.EMERGENCY,
            )
            return VOICE_TRANSFER_SUCCESS

        else:
            # SMS / EMAIL / CHAT — call the resident, connect to RPCC SIP endpoint when they pick up.
            # NOTE: this branch is unreachable by default — `_resolve_emergency_product_from_code`
            # routes non-voice RPCC to ADVANCED. Change that mapping to reach this branch once the
            # outbound-call flow is validated in prod.
            callback = resident_phone or ctx.context.ask_request.callback_number
            if not callback:
                return (
                    "No callback phone number provided. Please ask the resident for their phone number and try again."
                )

            callee = ctx.context.ask_request.product_info.callee
            if not callee:
                return DISPATCH_ERROR_MESSAGE

            try:
                callback_parsed = parse_and_validate_phone_number(callback)
            except ValueError:
                if ctx.context.esr_phone_retry_attempted:
                    logger.warning("Second phone validation failure, escalating")
                    return "Phone validation failed twice. Please escalate to a human teammate."
                logger.warning("Invalid phone number provided for RPCC transfer")
                ctx.context.esr_phone_retry_attempted = True
                ctx.context.call_ended_by_agent = False  # Not ending the call, just retrying
                return (
                    "The phone number provided was not valid. "
                    "Ask the resident to repeat their callback phone number and call this tool again."
                )

            sip_uri = _build_sip_uri(
                settings.rpcc_sip_endpoint,
                lo_property_id,
                _national(callback_parsed),
                _safe_national(callee),
                service_request_summary,
            )
            sip_twiml = _build_sip_transfer_twiml(sip_uri)

            twilio_client = _get_twilio_client()
            twilio_client.calls.create(
                to=_e164(callback_parsed),
                from_=callee,
                twiml=sip_twiml,
            )
            logger.info("Initiated outbound call to resident for RPCC SIP transfer")
            ctx.context.handoff_result = HandoffResult(
                tool="emergency_service_transfer_rpcc",
                reason=HandoffReasonCode.EMERGENCY.value,
                routing_confirmed=True,
                summary=service_request_summary,
            )
            publish_task_activity(
                extract_handoff_events,
                service_request_summary,
                ctx.context,
                reason=HandoffReasonCode.EMERGENCY,
            )
            return NONVOICE_TRANSFER_SUCCESS

    except asyncio.CancelledError:
        # Safe to suppress: this task is cancelled by _cancel_background_tasks(), which uses
        # asyncio.gather(..., return_exceptions=True) and ignores normal returns. The session
        # is already tearing down, so the task will end naturally regardless.
        logger.info("emergency_service_transfer_rpcc cancelled: call ended by user during emergency transfer")
        return "Emergency transfer cancelled: call ended by user."

    except Exception as e:
        ctx.context.handoff_result = HandoffResult(
            tool="emergency_service_transfer_rpcc",
            reason=HandoffReasonCode.EMERGENCY.value,
            routing_confirmed=False,
            summary=service_request_summary,
        )
        error = f"Failed to transfer to RPCC maintenance team: {e!s}. Please escalate to a human teammate."
        logger.exception("Failed to transfer to RPCC maintenance team", exc_info=True)
        return error

    finally:
        if voice:
            ctx.context.esr_initiated = False


@function_tool(description_override=_DESCRIPTION_TEMPLATE)
async def emergency_service_transfer_rpcc(
    ctx: RunContextWrapper[Any],
    service_request_summary: Annotated[
        str,
        "A detailed description of the emergency including what happened, location, and access details.",
    ],
    resident_phone: Annotated[
        str | None,
        "The resident's callback phone number in E.164 format (e.g., +15555555555). Required for non-voice channels.",
    ] = None,
) -> str:
    """Transfer the emergency to RPCC maintenance team."""
    return await _emergency_service_transfer_rpcc_impl(ctx, service_request_summary, resident_phone)
