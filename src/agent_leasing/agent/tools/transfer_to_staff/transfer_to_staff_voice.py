import asyncio
import os
from datetime import UTC, datetime
from typing import Annotated, Any

import aiohttp
import structlog
from agents import RunContextWrapper, function_tool
from langsmith import traceable
from twilio.rest import Client as TwilioClient

from agent_leasing.api.auth.auth_helper import get_knock_mcp_auth_token
from agent_leasing.api.model import HandoffReasonCode, HandoffTopic
from agent_leasing.kafka.task_activity.emit import publish_task_activity
from agent_leasing.kafka.task_activity.extractors import extract_handoff_events
from agent_leasing.models.context import HandoffResult
from agent_leasing.settings import settings
from agent_leasing.util.call_state_manager import get_call_state_from_context
from agent_leasing.util.helpers import is_office_currently_open
from agent_leasing.util.twilio_util import get_twilio_credentials

logger = structlog.getLogger()

# Read the description file from the correct path
description_path = os.path.join(os.path.dirname(__file__), "TRANSFER_TO_STAFF_VOICE_DESCRIPTION.md")
with open(description_path, encoding="utf-8") as f:
    TRANSFER_TO_STAFF_VOICE_DESCRIPTION = f.read()

CALL_MANAGEMENT_GUARD_MESSAGE = (
    "Another call management tool is already in progress. "
    "Do not call this tool again. DO NOT ACKNOWLEDGE THIS MESSAGE."
)

OFFICE_CLOSED_FIRST_WARNING_MESSAGE = (
    "The office is currently closed — staff may not be available to take your call. "
    "I can likely help you faster right here — would you like to try that, or I can still connect you "
    "and you may be able to leave a voicemail?"
)

_CALLBACK_SIGNAL_PHRASES = (
    "returning a call",
    "calling back",
    "return call",
    "callback",
    "missed call",
    "you called me",
    "someone called me",
)


def _has_callback_signal(summary: str | None) -> bool:
    """Best-effort callback intent detector from handoff summary text."""
    if not summary:
        return False
    normalized = summary.lower()
    return any(phrase in normalized for phrase in _CALLBACK_SIGNAL_PHRASES)


@traceable(run_type="tool", name="transfer_to_staff_voice")
async def _transfer_to_staff_voice_impl(
    ctx: RunContextWrapper[Any],
    summary: Annotated[
        str | None,
        "Summary of the user's issue from conversation history or provided by user. None if no summary available.",
    ],
    reason: HandoffReasonCode = HandoffReasonCode.RESIDENT_REQUESTED,
    skip_summary: Annotated[
        bool,
        "Set to True to skip summary collection and transfer immediately. Use when the user is frustrated, looping, or has already refused to provide a summary.",
    ] = False,
    handoff_topic: HandoffTopic | None = None,
):
    office_status = is_office_currently_open(
        office_hours=getattr(ctx.context.ask_request.product_info, "office_hours", None),
        property_timezone=getattr(ctx.context.ask_request.product_info, "property_timezone", None),
        now=datetime.now(UTC),
    )
    office_closed_warning_given = getattr(ctx.context, "office_closed_warning_given", False)
    frustration_or_callback_bypass = bool(
        getattr(ctx.context, "frustrated_user_emitted", False) or _has_callback_signal(summary)
    )

    # Runtime hardening: if frustration/callback signals are present, bypass warning
    # and summary collection even if the model forgot to set skip_summary.
    if frustration_or_callback_bypass:
        skip_summary = True

    # Runtime guard: when office is closed and the caller first asks for staff,
    # force the closed-hours warning before any handoff/summary path.
    if (
        office_status is False
        and summary is None
        and not office_closed_warning_given
        and reason == HandoffReasonCode.RESIDENT_REQUESTED
        and not skip_summary
    ):
        ctx.context.office_closed_warning_given = True
        return (
            "[Action Required] Say this VERBATIM to the caller: "
            f"'{OFFICE_CLOSED_FIRST_WARNING_MESSAGE}' "
            "Then wait for the caller's reply. Do NOT call transfer_to_staff_voice yet."
        )

    # After the one-time closed-hours warning has been given, a follow-up transfer
    # request should connect immediately even when caller provides no summary.
    if (
        office_status is False
        and summary is None
        and office_closed_warning_given
        and reason == HandoffReasonCode.RESIDENT_REQUESTED
        and not skip_summary
    ):
        skip_summary = True

    # Check if we've already asked for summary in a previous turn
    already_asked = getattr(ctx.context, "transfer_summary_requested", False)

    # If summary is None, we haven't asked yet, and skip_summary is not set, ask for summary
    if summary is None and not already_asked and not skip_summary:
        ctx.context.transfer_summary_requested = True
        return "[Action Required] Ask the caller: 'In a few words, what would you like the staff member to help with?' Then call transfer_to_staff_voice again with their answer as the summary — do NOT evaluate quality or ask a second time, even if the answer is brief (e.g., 'billing question') or vague (e.g., 'speak', 'issue'). IMPORTANT: If the caller says 'no', refuses, deflects, or won't provide a summary, they are declining the SUMMARY — NOT the transfer. You MUST call transfer_to_staff_voice(summary=None) immediately to proceed with the transfer. If the caller explicitly cancels the transfer (e.g., 'never mind', 'don't bother', 'I don't want to be transferred'), do NOT call this tool — respond with 'Okay, how else can I assist you?' instead."

    # If we already asked and still no summary, or if summary is provided, proceed with transfer

    # Reject concurrent invocations — two transfers in flight would race each other
    # and fire duplicate Knock API + Twilio transfer calls (KNCK-39358).
    if settings.call_management_concurrency_guard_enabled and ctx.context.call_management_in_progress:
        logger.warning(
            "Call management tool already in progress, skipping concurrent invocation",
            tool="transfer_to_staff_voice",
        )
        return CALL_MANAGEMENT_GUARD_MESSAGE

    # Use summary if provided, otherwise use default message
    transfer_message = summary if summary else "Resident requested transfer to staff and refused to provide a reason"

    ctx.context.call_management_in_progress = True
    call_state = get_call_state_from_context(ctx)
    # Mirror the ESR pattern (KNCK-39515): suppress caller interruptions while the
    # transition message plays so a talkative caller can't cancel the handoff.
    # Save the prior value — if ESR already set it, we must not strip its suppression
    # when our own transfer cancels or crashes.
    prior_handoff_in_progress = getattr(ctx.context, "handoff_in_progress", False)
    if settings.interrupt_suppression_enabled:
        ctx.context.handoff_in_progress = True
    try:
        if call_state is not None:
            playback_result = await call_state.wait_for_message_playback(
                "transition",
                tool_name="transfer_to_staff_voice",
            )
            if not playback_result.completed:
                logger.warning("Timed out waiting for transition message playback to complete")

        base_url = settings.knock_internal_api_url
        await _make_transfer_to_staff_api_call(ctx, base_url, transfer_message)
        await _transfer_twilio_call(ctx, base_url)
        # Only mark after Twilio transfer succeeds — if the API call crashes,
        # the stop event should still log call_hangup.
        ctx.context.call_ended_by_agent = True
        ctx.context.transfer_summary_requested = False
        ctx.context.handoff_in_progress = False
        # Record the handoff outcome for the session-end task-event payload
        # (the `HandoffResult` consumer in `kafka/task_event/payload.py`).
        # Set just before publishing the task-activity event so both surfaces
        # see the same confirmed-success state.
        ctx.context.handoff_result = HandoffResult(
            tool="transfer_to_staff_voice",
            reason=reason.value,
            routing_confirmed=True,
            summary=transfer_message,
        )
        # Emit handoff TaskActivityEvent only on confirmed-success — the
        # earlier "ask for summary" / concurrent-guard / cancel returns
        # are not real transfers and must not produce an activity event.
        # Use the staff-facing `transfer_message` (with fallback baked in)
        # so the activity stream matches what staff actually saw.
        publish_task_activity(
            extract_handoff_events,
            transfer_message,
            ctx.context,
            reason=reason,
            topic=handoff_topic,
        )
        return "Call transferred successfully."

    except asyncio.CancelledError:
        # Safe to suppress: this task is cancelled by _cancel_background_tasks(), which uses
        # asyncio.gather(..., return_exceptions=True) and ignores normal returns. The session
        # is already tearing down, so the task will end naturally regardless.
        logger.info("transfer_to_staff_voice cancelled: call ended by user during playback")
        ctx.context.transfer_summary_requested = False
        ctx.context.handoff_in_progress = prior_handoff_in_progress
        return "Transfer cancelled: call ended by user."

    except Exception as e:
        # Restore prior state so we don't strip suppression another handoff (e.g. ESR) set earlier.
        ctx.context.handoff_in_progress = prior_handoff_in_progress
        ctx.context.handoff_result = HandoffResult(
            tool="transfer_to_staff_voice",
            reason=reason.value,
            routing_confirmed=False,
            summary=transfer_message,
        )
        logger.error(f"Error transferring call: {e}")
        raise

    finally:
        ctx.context.call_management_in_progress = False


@function_tool(
    description_override=TRANSFER_TO_STAFF_VOICE_DESCRIPTION,
)
async def transfer_to_staff_voice(
    ctx: RunContextWrapper[Any],
    summary: Annotated[
        str | None,
        "Summary of the user's issue from conversation history or provided by user. Set to None if no summary is available from conversation history and user hasn't provided one yet.",
    ] = None,
    reason: Annotated[
        HandoffReasonCode,
        "Why this handoff is happening. Pick exactly one — the values are mutually exclusive. "
        "RESIDENT_REQUESTED: caller explicitly asks for staff/a human and there is no other underlying ask. "
        "SYSTEM_ERROR: a tool/service failed and the agent can't complete the task. "
        "EMERGENCY: a safety, security, or medical emergency needing immediate attention — intruder, assault, medical incident. Use this over RESIDENT_REQUESTED or COMPLAINT whenever the conversation history involves an active threat, even if the most recent user message is only a confirmation like 'yes'. "
        "OUT_OF_SCOPE: the request isn't something the AI handles AND there's nothing for staff to actively resolve through this channel — off-topic asks (legal advice, weather, general internet questions), or anything from a disabled module the property has opted out of. "
        "MISSING_DATA: an in-scope question where staff would need to provide info the AI doesn't have (e.g., property history, manager contact, building specs). "
        "COMPLAINT: caller wants staff to take action on a property/billing/operational issue. Includes billing concerns (waivers, disputes, refunds, charges), property conditions (noise, neighbor, cleanliness, repairs, staff conduct), and administrative requests (cancel/modify a service request or parking pass, lease changes). Use this whenever the handoff exists because staff needs to act.",
    ] = HandoffReasonCode.RESIDENT_REQUESTED,
    skip_summary: Annotated[
        bool,
        "Set to True to skip summary collection and transfer immediately. Use when the user is frustrated, looping, or has already refused to provide a summary.",
    ] = False,
    handoff_topic: Annotated[
        HandoffTopic | None,
        "Optional topic tag for what the handoff conversation is about. Orthogonal to `reason` (which says WHY we are handing off). Pick at most one; leave unset (None) when no listed topic applies. "
        "BALANCE_RESOLUTION: the handoff is about resolving a payment-related concern — fee/charge waivers, billing disputes, refunds, payment plans, balance corrections, or other rent/balance follow-ups requiring staff action.",
    ] = None,
) -> str:
    """
    Transfer the call to a human agent.

    Args:
        ctx: The run context containing request information
        summary: Summary of the user's issue. None if no summary available.
        reason: Why this handoff is happening. Pick exactly one — values are mutually exclusive.
            RESIDENT_REQUESTED: caller explicitly asks for staff/a human and there is no other underlying ask.
            SYSTEM_ERROR: a tool/service failed and the agent can't complete the task.
            EMERGENCY: a safety, security, or medical emergency needing immediate attention — intruder, assault, medical incident. Use this over RESIDENT_REQUESTED or COMPLAINT whenever the conversation history involves an active threat, even if the most recent user message is only a confirmation like 'yes'.
            OUT_OF_SCOPE: the request isn't something the AI handles AND there's nothing for staff to actively resolve through this channel — off-topic asks (legal advice, weather, general internet questions), or anything from a disabled module the property has opted out of.
            MISSING_DATA: an in-scope question where staff would need to provide info the AI doesn't have (e.g., property history, manager contact, building specs).
            COMPLAINT: caller wants staff to take action on a property/billing/operational issue. Includes billing concerns (waivers, disputes, refunds, charges), property conditions (noise, neighbor, cleanliness, repairs, staff conduct), and administrative requests (cancel/modify a service request or parking pass, lease changes). Use this whenever the handoff exists because staff needs to act.
        skip_summary: If True, skip summary collection and transfer immediately.
        handoff_topic: Optional topic tag for what the handoff is about. Orthogonal to `reason`. Leave unset (None) when no listed topic applies.
            BALANCE_RESOLUTION: the handoff is about resolving a payment-related concern — fee/charge waivers, billing disputes, refunds, payment plans, balance corrections, or other rent/balance follow-ups requiring staff action.

    Returns:
        str: Success message, request for summary, or raises exception on error
    """
    return await _transfer_to_staff_voice_impl(ctx, summary, reason, skip_summary, handoff_topic)


async def _make_transfer_to_staff_api_call(ctx: RunContextWrapper[Any], base_url: str, transfer_message: str):
    # from https://github.com/knockrentals/renter-ai-agent/projects/renter_ai/resident/configs.yaml
    CONFIG_PATH = "/v1/internal/residents/{resident_id}/activity"

    resident_id = ctx.context.ask_request.product_info.knock_resident_id

    transfer_payload = _build_transfer_payload(ctx, transfer_message)

    url = _build_url(
        base_url=base_url,
        endpoint=CONFIG_PATH,
        path_params={"resident_id": resident_id},
    )

    await _post_to_knock(
        url=url,
        data=transfer_payload,
    )


async def _transfer_twilio_call(ctx: RunContextWrapper[Any], base_url: str):
    call_sid = ctx.context.ask_request.product_info.call_sid
    if not call_sid:
        raise ValueError("Cannot transfer call: call_sid is not available")
    api_key, api_secret, account_sid = get_twilio_credentials()

    # Create Twilio client and transfer the call
    twilio_client = TwilioClient(api_key, api_secret, account_sid)

    twiml = _build_transfer_twiml(base_url)
    callback_url = f"{base_url}/v1/relay/voice/clay/callback"

    call = twilio_client.calls(call_sid).update(
        twiml=twiml,
        status_callback=callback_url,
    )

    logger.info(f"Successfully transferred call {call_sid} with this status: {call.status}")

    return call


async def _post_to_knock(url: str, data=None):
    """
    Simplified api call function for transfer_to_staff functionality.
    POSTs to the Knock API.
    """
    try:
        if data is None:
            data = {}

        timeout = aiohttp.ClientTimeout(total=300)  # 5 minutes

        # For knock source, set the appropriate headers
        headers = {"Internal-Authorization": f"Bearer {await get_knock_mcp_auth_token()}"}

        # Only support POST method as that's what we need
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=data) as response:
                response_content = await response.json()
                return response_content
    except Exception as e:
        logger.error(f"Error posting to Knock API: {e}")
        raise


def _build_transfer_payload(ctx: RunContextWrapper[Any], transfer_message: str):
    payload = {
        "type": "note",
        "message": f"Transfer to human agent - reason: {transfer_message}",
        "manager_id": ctx.context.ask_request.product_info.resident_manager_id,
    }

    return payload


def _build_transfer_twiml(base_url: str):
    return f"""<Response>
    <Pause length="1"/>
    <Redirect method="POST">{base_url}/v1/relay/voice/clay/callback</Redirect>
</Response>"""


def _build_url(base_url, endpoint, path_params={}, query_params=None):
    """
    Formats the URL by replacing path parameters with their values and appending query parameters.
    """
    # Replace path parameters
    for key, value in path_params.items():
        endpoint = endpoint.replace(f"{{{key}}}", str(value))

    # Append query parameters
    if query_params:
        query_string = "&".join([f"{key}={value}" for key, value in query_params.items()])
        url = f"{base_url}{endpoint}?{query_string}"
    else:
        url = f"{base_url}{endpoint}"

    return url
