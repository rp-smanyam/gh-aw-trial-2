"""Emergency service transfer tool orchestration."""

from __future__ import annotations

import asyncio
import copy
import os
from typing import Annotated, Any

import phonenumbers
import structlog
from agents import FunctionTool, RunContextWrapper, function_tool
from langsmith import traceable
from twilio.rest import Client as TwilioClient

from agent_leasing.agent.tools.emergency_service_transfer.description_helper import render_tool_description
from agent_leasing.agent.tools.emergency_service_transfer.http_util import _make_api_call
from agent_leasing.agent.util import SessionScope, get_channel_from_context, is_enabled
from agent_leasing.api.auth.auth_helper import get_books_auth_token
from agent_leasing.api.model import HandoffReasonCode
from agent_leasing.kafka.task_activity.emit import publish_task_activity
from agent_leasing.kafka.task_activity.extractors import extract_handoff_events
from agent_leasing.models.context import HandoffResult
from agent_leasing.settings import settings
from agent_leasing.util.call_state_manager import get_call_state_from_context
from agent_leasing.util.twilio_util import get_twilio_credentials

logger = structlog.get_logger(__name__)

# Constants
EMERGENCY_SERVICE_TRANSFER_PLAYBACK_END_TIMEOUT_SECONDS = 45.0
EMERGENCY_NUMBER_KEY_NAME = "emergphone"

NEVER_CALL_MESSAGE = (
    "NEVER call this function without an existing emergency service request. "
    "Please create the emergency service request first and then retry."
)
PLAY_VOICE_MESSAGE_FIRST = (
    "Tell the user to 1) stay safe, 2) call emergency services, and 3) if the service request was created, "
    "include the service request ID. If it failed, mention the attempt failed. "
    "Include this as non-empty assistant text before calling this tool again."
)
VOICE_REDIRECT_MESSAGE_TEMPLATE = "The resident will be redirected to {emergency_number} shortly."
CALL_RESIDENT_MESSAGE_TEMPLATE = "Please ask the resident to call the emergency technician at {emergency_number}."

EMERGENCY_NOT_FOUND_KB_ERROR = "Emergency number not found in knowledge base response"
INVALID_EMERGENCY_NUMBER_ERROR_PREFIX = "Invalid emergency number"
FAILED_TO_PARSE_ERROR_PREFIX = "Failed to parse emergency number"
EMERGENCY_NOT_FOUND_ERROR = "Emergency number not found. Please escalate to a human teammate."

CALL_MANAGEMENT_GUARD_MESSAGE = (
    "Another call management tool is already in progress. "
    "Do not call this tool again. DO NOT ACKNOWLEDGE THIS MESSAGE."
)

_description_path = os.path.join(os.path.dirname(__file__), "EMERGENCY_SERVICE_TRANSFER_BASIC_DESCRIPTION.md")
with open(_description_path, encoding="utf-8") as f:
    _DESCRIPTION_TEMPLATE = f.read().strip()

# Exported for backwards compatibility - unrendered template
EMERGENCY_SERVICE_TRANSFER_BASIC_DESCRIPTION = _DESCRIPTION_TEMPLATE


def get_emergency_service_transfer_basic_fxn(context: SessionScope | None = None) -> FunctionTool:
    """Get the basic emergency service transfer tool with rendered description."""
    tool = copy.copy(emergency_service_transfer_basic)
    tool.description = render_tool_description(_DESCRIPTION_TEMPLATE, context)
    return tool


@traceable(run_type="tool", name="emergency_service_transfer_basic")
async def _emergency_service_transfer_basic_impl(
    ctx: RunContextWrapper[Any],
    already_created_emergency_service_request: Annotated[
        bool,
        "If the emergency service request has already been attempted. "
        "True, if the request has already been created. "
        "True, if the request was attempted but failed. "
        "False, if no request has been attempted",
    ],
    service_request_summary: Annotated[
        str,
        "A clear 1-2 sentence description of the emergency that was the basis for the prior `create_service_request` call. "
        "Reuse the same wording you passed as `chat_summary` to that tool. Include location and access details where known.",
    ],
):
    ctx.context.call_ended_by_agent = True
    try:
        channel = get_channel_from_context(ctx.context)
        disabled_modules = ctx.context.disabled_modules
        if is_enabled("MR", disabled_modules) and not already_created_emergency_service_request:
            return NEVER_CALL_MESSAGE

        # Reject concurrent invocations — a second emergency redirect racing with an
        # in-flight transfer would fire duplicate Twilio calls (KNCK-39358). Placed
        # after NEVER_CALL_MESSAGE so the pre-requisite check is still free to fire.
        if settings.call_management_concurrency_guard_enabled and ctx.context.call_management_in_progress:
            logger.warning(
                "Call management tool already in progress, skipping concurrent invocation",
                tool="emergency_service_transfer_basic",
            )
            return CALL_MANAGEMENT_GUARD_MESSAGE
        ctx.context.call_management_in_progress = True

        uc_company_id = ctx.context.ask_request.product_info.uc_company_id.id
        uc_property_id = ctx.context.ask_request.product_info.uc_property_id.id

        try:
            emergency_number = validate_and_format_emergency_number(ctx.context.ask_request.product_info.emerg_phone)
        except (ValueError, phonenumbers.NumberParseException):
            logger.info("Valid emergency number not found in payload, fetching from Unified Settings API")
            company_id, property_id = await get_books_ids(uc_company_id, uc_property_id)
            emergency_number = await get_emergency_number(company_id, property_id)

        if not emergency_number:
            ctx.context.handoff_in_progress = False
            return EMERGENCY_NOT_FOUND_ERROR

        if channel == "VOICE":
            call_state = get_call_state_from_context(ctx)
            if call_state is not None:
                playback_result = await call_state.wait_for_message_playback(
                    "transfer",
                    tool_name="emergency_service_transfer_basic",
                    end_timeout_seconds=EMERGENCY_SERVICE_TRANSFER_PLAYBACK_END_TIMEOUT_SECONDS,
                )
                if not playback_result.completed:
                    logger.warning("Timed out waiting for transfer message playback to complete")

            call_sid = ctx.context.ask_request.product_info.call_sid
            await redirect_to_number_via_twilio(call_sid, emergency_number)
            ctx.context.handoff_in_progress = False
            ctx.context.handoff_result = HandoffResult(
                tool="emergency_service_transfer_basic",
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
            return VOICE_REDIRECT_MESSAGE_TEMPLATE.format(emergency_number=emergency_number)
        else:  # if channel in ["SMS", "EMAIL", "CHAT"]
            ctx.context.handoff_in_progress = False
            ctx.context.handoff_result = HandoffResult(
                tool="emergency_service_transfer_basic",
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
            return CALL_RESIDENT_MESSAGE_TEMPLATE.format(emergency_number=emergency_number)

    except asyncio.CancelledError:
        # Safe to suppress: this task is cancelled by _cancel_background_tasks(), which uses
        # asyncio.gather(..., return_exceptions=True) and ignores normal returns. The session
        # is already tearing down, so the task will end naturally regardless.
        ctx.context.handoff_in_progress = False
        logger.info("emergency_service_transfer_basic cancelled: call ended by user during emergency transfer")
        return "Emergency transfer cancelled: call ended by user."

    except Exception:
        # ESR flow is over on failure — reset so interruptions aren't stuck suppressed
        ctx.context.handoff_in_progress = False
        ctx.context.handoff_result = HandoffResult(
            tool="emergency_service_transfer_basic",
            reason=HandoffReasonCode.EMERGENCY.value,
            routing_confirmed=False,
            summary=service_request_summary,
        )
        error_message = "Failed to route emergency transfer request. Please escalate to a human teammate."
        logger.exception(
            "Failed to route emergency transfer request",
            event_type="emergency_transfer_failed",
            error_category="emergency_failure",
            exc_info=True,
        )
        return error_message

    finally:
        # Clear the concurrency guard flag so sequential retries are not blocked.
        # Safe even when the early NEVER_CALL_MESSAGE / guard returns fire before the
        # flag was set — assigning False is a no-op in that case.
        ctx.context.call_management_in_progress = False


@function_tool(description_override=_DESCRIPTION_TEMPLATE)
async def emergency_service_transfer_basic(
    ctx: RunContextWrapper[Any],
    already_created_emergency_service_request: Annotated[
        bool,
        "If the emergency service request has already been attempted. "
        "True, if the request has already been created. "
        "True, if the request was attempted but failed. "
        "False, if no request has been attempted",
    ],
    service_request_summary: Annotated[
        str,
        "A clear 1-2 sentence description of the emergency that was the basis for the prior `create_service_request` call. "
        "Reuse the same wording you passed as `chat_summary` to that tool. Include location and access details where known.",
    ],
) -> str:
    """Route the emergency transfer request to the configured provider."""
    return await _emergency_service_transfer_basic_impl(
        ctx,
        already_created_emergency_service_request,
        service_request_summary,
    )


async def get_books_ids(uc_company_id: str, uc_property_id: str):
    company_id, property_id = await asyncio.gather(
        get_company_id(uc_company_id),
        get_property_id(uc_property_id),
    )

    return company_id, property_id


async def get_company_id(uc_company_id: str):
    method = "GET"
    books_host = settings.books_host.rstrip("/")
    url = f"{books_host}/books/translate/v2/companyinstance/{uc_company_id}/OS"

    headers = {
        "Authorization": f"Bearer {await get_books_auth_token()}",
    }

    response = await _make_api_call(url=url, payload={}, headers=headers, api_name="Company ID", method=method)
    return _extract_translated_id(response, "translatedCompanyInstances", "companyInstanceSourceId", "SET")


async def get_property_id(uc_property_id: str):
    method = "GET"
    books_host = settings.books_host.rstrip("/")
    url = f"{books_host}/books/translate/v2/propertyinstance/{uc_property_id}/OS"

    headers = {
        "Authorization": f"Bearer {await get_books_auth_token()}",
    }

    response = await _make_api_call(url=url, payload={}, headers=headers, api_name="Property ID", method=method)
    return _extract_translated_id(response, "translatedPropertyInstances", "propertyInstanceSourceId", "SET")


def _extract_translated_id(response: dict[str, Any], instances_key: str, id_key: str, target_source: str) -> str:
    """
    Extract a translated ID from the Books API response for a specific source.

    Args:
        response: The API response dictionary
        instances_key: Key name for the instances array (e.g., "translatedCompanyInstances")
        id_key: Key name for the ID field (e.g., "companyInstanceSourceId")
        target_source: The source system to find (e.g., "SET")

    Returns:
        The translated ID for the target source

    Raises:
        ValueError: If the target source is not found in the response
    """
    translated_instances = response.get("data", {}).get("attributes", {}).get(instances_key, [])
    for instance in translated_instances:
        if instance.get("source") == target_source:
            return instance.get(id_key)

    raise ValueError(f"{target_source} ID not found in translation response")


async def get_emergency_number(company_id: str, property_id: str):
    # Build the actual URL from template
    source_id = "SET"
    books_host = settings.books_host.rstrip("/")
    url = f"{books_host}/settings/v1/{source_id}/companies/{company_id}/properties/{property_id}"

    method = "POST"
    headers = {
        "Authorization": f"Bearer {await get_books_auth_token()}",
    }
    payload = {
        "keys": [
            {"sourceId": source_id, "mappingKey": "aipropertypronunciation"},
            {"sourceId": source_id, "mappingKey": "airesidentsreferredto"},
            {"sourceId": source_id, "mappingKey": "aienablepricingandavailability"},
            {"sourceId": source_id, "mappingKey": "aisendlinkforpricing"},
            {"sourceId": "SETC", "mappingKey": "emergphone"},
        ],
        "tables": [
            {"sourceId": source_id, "mappingKey": "aifaqlist"},
        ],
    }
    response = await _make_api_call(
        url=url,
        payload=payload,
        headers=headers,
        api_name="Knowledge Base",
        method=method,
    )

    # response is already a parsed dict from _make_api_call
    emergency_number = get_emergency_number_from_knowledge_base_response(response)

    return emergency_number


def get_emergency_number_from_knowledge_base_response(
    knowledge_base_response: dict[str, Any],
) -> str:
    """Parse the knowledge base response to get the emergency number."""

    # Extract emergency number from response
    emergency_number_str = None
    keys = knowledge_base_response.get("keys") or []

    for key in keys:
        if key.get("name") == EMERGENCY_NUMBER_KEY_NAME:
            emergency_number_str = key.get("value")
            break

    if not emergency_number_str:
        raise ValueError(EMERGENCY_NOT_FOUND_KB_ERROR)

    # Validate and format emergency number
    try:
        return validate_and_format_emergency_number(emergency_number_str)
    except (ValueError, phonenumbers.NumberParseException) as e:
        raise ValueError(f"{FAILED_TO_PARSE_ERROR_PREFIX} '{emergency_number_str}': {e}") from e


def validate_and_format_emergency_number(emergency_number_str: str) -> str:
    parsed_number = phonenumbers.parse(emergency_number_str, "US")
    if not phonenumbers.is_valid_number(parsed_number):
        raise ValueError(f"{INVALID_EMERGENCY_NUMBER_ERROR_PREFIX}: {emergency_number_str}")
    return phonenumbers.format_number(parsed_number, phonenumbers.PhoneNumberFormat.E164)


async def redirect_to_number_via_twilio(call_sid: str, emergency_number: str):
    logger.info(f"Redirecting call to emergency number: {emergency_number}")
    api_key, api_secret, account_sid = get_twilio_credentials()

    # Create Twilio client and transfer the call
    twilio_client = TwilioClient(api_key, api_secret, account_sid)

    twiml = _build_emergency_transfer_twiml(emergency_number)

    call = twilio_client.calls(call_sid).update(
        twiml=twiml,
    )

    logger.info(f"Successfully transferred call {call_sid} with this status: {call.status}")


def _build_emergency_transfer_twiml(phone_number: str):
    return f"""<Response>
    <Pause length="1"/>
    <Dial>{phone_number}</Dial>
</Response>"""
