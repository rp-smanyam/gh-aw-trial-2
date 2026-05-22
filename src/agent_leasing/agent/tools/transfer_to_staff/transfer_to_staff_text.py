import os
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from agents import RunContextWrapper, function_tool
from langsmith import traceable

from agent_leasing.agent.tools.create_link.create_link import (
    _create_human_hand_off_link,
)
from agent_leasing.agent.tools.transfer_to_staff.handoff import maybe_get_handoff_key
from agent_leasing.agent.util import get_channel_from_context
from agent_leasing.api.model import AskResponse, Channel, HandoffReasonCode, HandoffTopic
from agent_leasing.kafka.task_activity.emit import publish_task_activity
from agent_leasing.kafka.task_activity.extractors import extract_handoff_events
from agent_leasing.models.context import HandoffResult
from agent_leasing.settings import settings
from agent_leasing.util.memory import put

# Channels that support AI pause/resume after handoff
HANDOFF_PAUSE_CHANNELS = {"SMS", "EMAIL"}

logger = structlog.getLogger()


# Read the description file from the correct path
description_path = os.path.join(os.path.dirname(__file__), "TRANSFER_TO_STAFF_TEXT_DESCRIPTION.md")
with open(description_path, encoding="utf-8") as f:
    TRANSFER_TO_STAFF_TEXT_DESCRIPTION = f.read()


@traceable(run_type="tool", name="transfer_to_staff_text")
async def _transfer_to_staff_text_impl(
    ctx: RunContextWrapper[Any],
    repeated_handoff_attempt: bool,
    sufficient_summary_information: bool,
    user_refused_to_provide_summary: bool,
    transfer_message: str,
    user_confirmation: bool,
    reason: HandoffReasonCode = HandoffReasonCode.RESIDENT_REQUESTED,
    handoff_topic: HandoffTopic | None = None,
) -> str:
    ctx.context.call_ended_by_agent = True
    # If this is a repeated handoff attempt, bypass all validation checks
    if repeated_handoff_attempt:
        logger.info("Repeated handoff attempt detected, bypassing validation checks")
    else:
        # Normal validation flow
        if not sufficient_summary_information and not user_refused_to_provide_summary:
            error = "User did not provide sufficient summary information. Please ask for implicit or explicit confirmation based on your workflow instructions and try again."
            return error

        if not user_confirmation:
            error = "User did not confirm the action. Please ask for implicit or explicit confirmation based on your workflow instructions and try again."
            return error

    ctx.context.handoff = True
    ctx.context.handoff_message = "(AI Summary) " + transfer_message

    # Write handoff state to Redis for SMS/EMAIL channels only
    # This pauses AI responses until inactivity timer expires (configured in settings)
    channel = get_channel_from_context(ctx.context)
    if channel in HANDOFF_PAUSE_CHANNELS:
        property_id = ctx.context.ask_request.product_info.knock_property_id
        knock_resident_id = ctx.context.ask_request.product_info.knock_resident_id
        ab_resident_id = getattr(ctx.context.ask_request.product_info.ab_resident_id, "id", None)
        product = ctx.context.ask_request.product
        handoff_key = maybe_get_handoff_key(product, property_id, knock_resident_id, ab_resident_id)
        if handoff_key is not None:
            handoff_data = {
                "transferred": True,
                "handoff_time": datetime.now(UTC).isoformat(),
            }
            # TTL matches the handoff duration - key auto-expires when AI should resume
            await put(handoff_key, handoff_data, expire=settings.handoff_inactivity_ttl)
            logger.info(f"Handoff state written to Redis for {channel} channel with key: {handoff_key}")

    product_info = ctx.context.ask_request.product_info
    handoff_portal_link: str | None = None
    if not product_info.uc_portal_base_url or not product_info.static_paths:
        return_message = "Successfully set the context variable to trigger handoff. Portal base URL not configured, so no handoff portal link will be sent to the user."
    else:
        handoff_portal_link = _create_human_hand_off_link(
            product_info.uc_portal_base_url,
            product_info.static_paths,
        )
        return_message = (
            f"Successfully set context variable to trigger handoff. Handoff portal link: {handoff_portal_link}"
        )

    # Structured event for dashboards: one log per handoff trigger, with the
    # portal domain as a queryable field instead of buried in the tool's
    # return-message text.
    handoff_destination = handoff_portal_link.split("/")[2] if handoff_portal_link else None
    logger.info(
        "Handoff triggered",
        event_type="handoff_triggered",
        channel=channel,
        handoff_destination=handoff_destination,
        reason=reason.value,
    )

    # Record the handoff outcome for the session-end task-event payload
    # (the `HandoffResult` consumer in `kafka/task_event/payload.py`).
    # Text channels have no Twilio routing — `routing_confirmed=True`
    # means the handoff context flag is successfully set.
    ctx.context.handoff_result = HandoffResult(
        tool="transfer_to_staff_text",
        reason=reason.value,
        routing_confirmed=True,
        summary=transfer_message,
    )
    # Emit one TaskActivityEvent. handoff=True is set above; every path
    # from here is a confirmed handoff.
    publish_task_activity(
        extract_handoff_events,
        transfer_message,
        ctx.context,
        reason=reason,
        handoff_portal_link=handoff_portal_link,
        topic=handoff_topic,
    )
    return return_message


@function_tool(
    description_override=TRANSFER_TO_STAFF_TEXT_DESCRIPTION,
)
async def transfer_to_staff_text(
    ctx: RunContextWrapper[Any],
    repeated_handoff_attempt: Annotated[
        bool,
        "Set to True if the user has already requested a handoff multiple times in this conversation (e.g., saying 'Agent' or 'transfer me' repeatedly). This will bypass all other validation checks and immediately process the transfer.",
    ],
    sufficient_summary_information: Annotated[
        bool,
        "Set to True if the conversation history contains sufficient information to create a concise, specific, and actionable one-sentence summary of the user's property-related issue. Set to False if the conversation history lacks sufficient context for a meaningful summary.",
    ],
    user_refused_to_provide_summary: Annotated[
        bool,
        "Set to True if the user explicitly refused to provide a summary of the issue. Set to False if the user provided a summary.",
    ],
    transfer_message: Annotated[
        str,
        "Highly detailed message on behalf of the user (should keep it in first-person voice), that summarizes the issue for the customer service agent to help with.",
    ],
    user_confirmation: Annotated[
        bool,
        "Whether user confirmed the action, either explicitly "
        "(`I hear you'd like to talk to staff about a payment issue. If that's correct, I can transfer you right away.` -> `Yes`) "
        "or implicitly "
        "(`I understand you'd like to talk to our staff.  Can you provide me a summary of the issue so I can connect you to the right person?` -> `Payment issue.`) ",
    ],
    reason: Annotated[
        HandoffReasonCode,
        "Why this handoff is happening. Pick exactly one — the values are mutually exclusive. "
        "RESIDENT_REQUESTED: resident explicitly asks for staff/a human and there is no other underlying ask. "
        "SYSTEM_ERROR: a tool/service failed and the agent can't complete the task. "
        "EMERGENCY: a safety, security, or medical emergency needing immediate attention — intruder, assault, medical incident. Use this over RESIDENT_REQUESTED or COMPLAINT whenever the conversation history involves an active threat, even if the most recent user message is only a confirmation like 'yes'. "
        "OUT_OF_SCOPE: the request isn't something the AI handles AND there's nothing for staff to actively resolve through this channel — off-topic asks (legal advice, weather, general internet questions), or anything from a disabled module the property has opted out of. "
        "MISSING_DATA: an in-scope question where staff would need to provide info the AI doesn't have (e.g., property history, manager contact, building specs). "
        "COMPLAINT: resident wants staff to take action on a property/billing/operational issue. Includes billing concerns (waivers, disputes, refunds, charges), property conditions (noise, neighbor, cleanliness, repairs, staff conduct), and administrative requests (cancel/modify a service request or parking pass, lease changes). Use this whenever the handoff exists because staff needs to act.",
    ] = HandoffReasonCode.RESIDENT_REQUESTED,
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
        repeated_handoff_attempt: Whether this is a repeated handoff attempt that should bypass validation
        sufficient_summary_information: Whether sufficient information is available for a summary
        user_refused_to_provide_summary: Whether the user explicitly refused to provide a summary
        transfer_message: Highly detailed message on behalf of the user (first-person voice), that summarizes the issue for the customer service agent to help with.
        user_confirmation: Whether user confirmed the action
        reason: Why this handoff is happening. Pick exactly one — values are mutually exclusive.
            RESIDENT_REQUESTED: resident explicitly asks for staff/a human and there is no other underlying ask.
            SYSTEM_ERROR: a tool/service failed and the agent can't complete the task.
            EMERGENCY: a safety, security, or medical emergency needing immediate attention — intruder, assault, medical incident. Use this over RESIDENT_REQUESTED or COMPLAINT whenever the conversation history involves an active threat, even if the most recent user message is only a confirmation like 'yes'.
            OUT_OF_SCOPE: the request isn't something the AI handles AND there's nothing for staff to actively resolve through this channel — off-topic asks (legal advice, weather, general internet questions), or anything from a disabled module the property has opted out of.
            MISSING_DATA: an in-scope question where staff would need to provide info the AI doesn't have (e.g., property history, manager contact, building specs).
            COMPLAINT: resident wants staff to take action on a property/billing/operational issue. Includes billing concerns (waivers, disputes, refunds, charges), property conditions (noise, neighbor, cleanliness, repairs, staff conduct), and administrative requests (cancel/modify a service request or parking pass, lease changes). Use this whenever the handoff exists because staff needs to act.
        handoff_topic: Optional topic tag for what the handoff is about. Orthogonal to `reason`. Leave unset (None) when no listed topic applies.
            BALANCE_RESOLUTION: the handoff is about resolving a payment-related concern — fee/charge waivers, billing disputes, refunds, payment plans, balance corrections, or other rent/balance follow-ups requiring staff action.

    Returns:
        str: Success message or raises exception on error
    """
    return await _transfer_to_staff_text_impl(
        ctx,
        repeated_handoff_attempt,
        sufficient_summary_information,
        user_refused_to_provide_summary,
        transfer_message,
        user_confirmation,
        reason,
        handoff_topic,
    )


def execute_handoff(channel: Channel, transfer_message: str, resp_model: AskResponse) -> AskResponse:
    if channel == Channel.EMAIL:
        return _execute_handoff_email(transfer_message, resp_model)
    elif channel == Channel.CHAT:
        return _execute_handoff_chat(transfer_message, resp_model)
    elif channel == Channel.SMS:
        return _execute_handoff_sms(transfer_message, resp_model)
    else:
        raise ValueError(f"Unsupported channel: {channel}")


def _execute_handoff_email(transfer_message: str, resp_model: AskResponse) -> AskResponse:
    resp_model.metadata["email_route_back"] = True
    resp_model.metadata["human_handoff"] = True
    resp_model.metadata["human_hand_off_message"] = transfer_message
    return resp_model


def _execute_handoff_chat(transfer_message: str, resp_model: AskResponse) -> AskResponse:
    resp_model.metadata["human_handoff"] = True
    resp_model.metadata["human_hand_off_message"] = transfer_message
    return resp_model


def _execute_handoff_sms(transfer_message: str, resp_model: AskResponse) -> AskResponse:
    resp_model.metadata["human_handoff"] = True
    resp_model.metadata["human_hand_off_message"] = transfer_message
    return resp_model
