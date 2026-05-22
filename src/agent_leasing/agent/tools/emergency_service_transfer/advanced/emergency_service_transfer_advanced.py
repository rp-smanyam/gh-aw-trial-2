"""Emergency service transfer tool - Advanced implementation."""

from __future__ import annotations

import copy
import os
from typing import Annotated, Any

import phonenumbers
import structlog
from agents import FunctionTool, RunContextWrapper, function_tool
from langsmith import traceable

from agent_leasing.agent.tools.emergency_service_transfer.description_helper import render_tool_description
from agent_leasing.agent.tools.emergency_service_transfer.http_util import _make_api_call
from agent_leasing.agent.util import SessionScope, get_channel_from_context, is_enabled
from agent_leasing.api.model import HandoffReasonCode
from agent_leasing.kafka.task_activity.emit import publish_task_activity
from agent_leasing.kafka.task_activity.extractors import extract_handoff_events
from agent_leasing.models.context import HandoffResult
from agent_leasing.settings import settings

logger = structlog.get_logger(__name__)

NEVER_CALL_MESSAGE = (
    "NEVER call this function without an existing emergency service request. "
    "Please create the emergency service request first and then retry."
)
PLAY_VOICE_MESSAGE_FIRST = (
    "Tell the user that the emergency technician is being dispatched and will call them shortly. "
    "Include this as non-empty assistant text before calling this tool again."
)
DISPATCH_SUCCESS_MESSAGE = (
    "Emergency technician dispatch initiated successfully. The technician will call the resident shortly."
)
DISPATCH_ERROR_MESSAGE = "Failed to initiate emergency technician dispatch. Please escalate to a human teammate."

_description_path = os.path.join(os.path.dirname(__file__), "EMERGENCY_SERVICE_TRANSFER_ADVANCED_DESCRIPTION.md")

with open(_description_path, encoding="utf-8") as f:
    _DESCRIPTION_TEMPLATE = f.read().strip()

# Exported for backwards compatibility - unrendered template
EMERGENCY_SERVICE_TRANSFER_ADVANCED_DESCRIPTION = _DESCRIPTION_TEMPLATE


def get_emergency_service_transfer_advanced_fxn(context: SessionScope | None = None) -> FunctionTool:
    """Get the advanced emergency service transfer tool with rendered description."""
    tool = copy.copy(emergency_service_transfer_advanced)
    tool.description = render_tool_description(_DESCRIPTION_TEMPLATE, context)
    return tool


@traceable(run_type="tool", name="emergency_service_transfer_advanced")
async def _emergency_service_transfer_advanced_impl(
    ctx: RunContextWrapper[Any],
    called_create_service_request: Annotated[
        bool,
        "Set to True if you have already attempted to create the service request (whether it succeeded or failed).",
    ],
    already_played_voice_channel_transfer_message: Annotated[
        bool,
        "Set to True if you have already told the user that the technician is being dispatched (or if not on voice channel).",
    ],
    resident_phone: Annotated[
        str,
        "The resident's callback phone number in E.164 format (e.g., +15555555555). This is where the technician will be connected after they pick up.",
    ],
    service_request_summary: Annotated[
        str,
        "A clear 1-2 sentence description of the emergency that will be read to the technician. Include location and access details.",
    ],
    service_request_id: Annotated[
        str | int | None,
        "The ID of the service request if one was successfully created.",
    ] = None,
):
    ctx.context.call_ended_by_agent = True
    try:
        channel = get_channel_from_context(ctx.context)
        disabled_modules = ctx.context.disabled_modules
        if is_enabled("MR", disabled_modules) and not called_create_service_request:
            return NEVER_CALL_MESSAGE
        if channel == "VOICE" and not already_played_voice_channel_transfer_message:
            return PLAY_VOICE_MESSAGE_FIRST

        # parse and format necessary IDs
        lo_property_id = ctx.context.ask_request.product_info.lo_property_id
        backup_number = (
            ctx.context.ask_request.product_info.caller or ctx.context.ask_request.product_info.resident_phone or ""
        )
        try:
            formatted_phone = parse_and_validate_phone_number(resident_phone, backup_number)
        except ValueError:
            if ctx.context.esr_phone_retry_attempted:
                logger.warning("Second phone validation failure, escalating", resident_phone=resident_phone)
                ctx.context.handoff_in_progress = False
                return "Phone validation failed twice. Please escalate to a human teammate."
            logger.warning("Invalid phone number provided for emergency dispatch", resident_phone=resident_phone)
            ctx.context.esr_phone_retry_attempted = True
            ctx.context.call_ended_by_agent = False  # Not ending the call, just retrying
            return (
                "The phone number provided was not valid. "
                "Ask the resident to repeat their callback phone number and call this tool again."
            )
        service_request_id_str = str(service_request_id) if service_request_id else "None provided"

        result = await dispatch_to_emergency_service_transfer(
            lo_property_id, formatted_phone, service_request_summary, service_request_id_str
        )
        # ESR flow is complete — reset so normal interruptions resume
        ctx.context.handoff_in_progress = False
        if result == DISPATCH_SUCCESS_MESSAGE:
            ctx.context.handoff_result = HandoffResult(
                tool="emergency_service_transfer_advanced",
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
        return result

    except Exception as e:
        # ESR flow is over even on failure — reset so interruptions aren't stuck suppressed
        ctx.context.handoff_in_progress = False
        ctx.context.handoff_result = HandoffResult(
            tool="emergency_service_transfer_advanced",
            reason=HandoffReasonCode.EMERGENCY.value,
            routing_confirmed=False,
            summary=service_request_summary,
        )
        error = f"Failed to dispatch emergency technician: {str(e)}. Please escalate to a human teammate."
        logger.exception("Failed to dispatch emergency technician", exc_info=True)
        return error


@function_tool(description_override=_DESCRIPTION_TEMPLATE)
async def emergency_service_transfer_advanced(
    ctx: RunContextWrapper[Any],
    called_create_service_request: Annotated[
        bool,
        "Set to True if you have already attempted to create the service request (whether it succeeded or failed).",
    ],
    already_played_voice_channel_transfer_message: Annotated[
        bool,
        "Set to True if you have already told the user that the technician is being dispatched (or if not on voice channel).",
    ],
    resident_phone: Annotated[
        str,
        "The resident's callback phone number in E.164 format (e.g., +15555555555). This is where the technician will be connected after they pick up.",
    ],
    service_request_summary: Annotated[
        str,
        "A clear 1-2 sentence description of the emergency that will be read to the technician. Include location and access details.",
    ],
    service_request_id: Annotated[
        str | int | None,
        "The ID of the service request if one was successfully created.",
    ] = None,
) -> str:
    """Dispatch the on-call emergency technician via the emergency dispatch system."""
    return await _emergency_service_transfer_advanced_impl(
        ctx,
        called_create_service_request,
        already_played_voice_channel_transfer_message,
        resident_phone,
        service_request_summary,
        service_request_id,
    )


def parse_and_validate_phone_number(resident_phone: str, backup_number: str) -> str:
    try:
        parsed_number = phonenumbers.parse(resident_phone, "US")
        if not phonenumbers.is_valid_number(parsed_number):
            raise ValueError(
                f"Invalid resident phone number: {resident_phone}. Please provide a valid callback number."
            )
        return str(parsed_number.national_number)
    except phonenumbers.NumberParseException as e:
        raise ValueError(
            f"Failed to parse resident phone number '{resident_phone}'. Please provide a valid callback number. : {e}"
        ) from e


async def dispatch_to_emergency_service_transfer(
    lo_property_id: str | None,
    formatted_phone: str,
    service_request_summary: str,
    service_request_id: str,
):
    if not (lo_property_id and settings.emergency_dispatch_url):
        raise ValueError(
            "lo_property_id and emergency_dispatch_url are required to dispatch emergency service. Args: "
            f"lo_property_id={lo_property_id!r}, emergency_dispatch_url={settings.emergency_dispatch_url!r}"
        )
    # Prepare payload
    payload = {
        "ServiceRequestID": service_request_id,
        "ResidentTelephone": formatted_phone,  # Already formatted without country code
        "Summary": service_request_summary,
    }

    # Make API call
    url = settings.emergency_dispatch_url + "/" + lo_property_id
    headers = {
        "Content-Type": "application/json",
    }

    response = await _make_api_call(
        url=url, payload=payload, headers=headers, api_name="Emergency Dispatch", method="POST"
    )

    # Check response
    if response.get("status") == 200 or response.get("success"):
        return DISPATCH_SUCCESS_MESSAGE
    else:
        logger.error("Emergency dispatch API returned non-success response", response=response)
        return DISPATCH_ERROR_MESSAGE
