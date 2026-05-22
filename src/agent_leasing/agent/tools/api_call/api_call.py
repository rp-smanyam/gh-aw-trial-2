import asyncio
import contextvars
from typing import Annotated, Any, Literal

import structlog
from agents import RunContextWrapper, custom_span, function_tool
from langsmith import traceable
from pydantic import BaseModel

from agent_leasing.agent.tools.mcp_post_processors import EMERGENCY_PRIORITY_NUMBER
from agent_leasing.agent.tools.verification_check import check_verification_status
from agent_leasing.agent.util import get_channel_from_context
from agent_leasing.api.util import perform_api_call
from agent_leasing.kafka.task_activity.emit import publish_task_activity
from agent_leasing.kafka.task_activity.extractors import extract_facilities_thinker_events
from agent_leasing.services.agent_service import CONVERSATION_ID_HEADER, PREVIOUS_RESPONSE_ID_HEADER
from agent_leasing.settings import settings
from agent_leasing.util.tracing_utils import set_span_data
from agent_leasing.util.voice_text_normalizer import normalize_json_values

logger = structlog.get_logger(__name__)
BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


class ResidentIdentifiers(BaseModel):
    pmc_id: str | int
    site_id: str | int
    resident_household_id: str | int
    resident_member_id: str | int
    ab_community_id: str | int
    ab_resident_id: str | int
    ab_unit_id: str | int | None = None  # Required if the channel is chat


class FacilitiesThinkerApiRequestModel(BaseModel):
    resident_identifiers: ResidentIdentifiers
    relevant_context_from_last_user_message: str
    channel: Literal["chat", "sms", "voice", "email", "other"]
    cidp_token: str | None = None  # Required if the channel is chat
    phone_number: str | None = None
    permission_to_enter: bool | None = None
    permission_entry_notes: str | None = None
    emergency: bool = False


def _build_facilities_payload(
    ctx: RunContextWrapper[Any],
    emergency: bool,
    channel: str,
    message: str | None,
    permission_to_enter: bool | None,
    permission_entry_notes: str | None,
) -> dict:
    product_info = ctx.context.ask_request.product_info
    channel = get_channel_from_context(ctx.context).lower()

    resident_identifiers = ResidentIdentifiers(
        pmc_id=product_info.uc_company_id.id,
        site_id=product_info.uc_property_id.id,
        resident_household_id=product_info.uc_resident_household_id.id,
        resident_member_id=product_info.uc_resident_member_id.id,
        ab_community_id=product_info.uc_community_id.id,
        ab_resident_id=product_info.ab_resident_id.id,
        ab_unit_id=getattr(getattr(product_info, "ab_unit_id", None), "id", None) if channel == "chat" else None,
    )

    cidp_token = (
        getattr(getattr(product_info, "uc_consumer_identity_token", None), "id", None) if channel == "chat" else None
    )

    phone_number = getattr(product_info, "resident_phone", None) if channel == "chat" else None

    if (
        message
        and message != "List my active service requests"
        and settings.facilities_thinker_self_service_enabled is False
    ):
        message += " Resident does not want self-service troubleshooting steps."

    request_payload = FacilitiesThinkerApiRequestModel(
        resident_identifiers=resident_identifiers,
        relevant_context_from_last_user_message=message or ctx.context.ask_request.prompt,
        channel=channel,
        cidp_token=cidp_token,
        phone_number=phone_number,
        emergency=emergency,
        permission_to_enter=permission_to_enter,
        permission_entry_notes=permission_entry_notes,
    )

    return request_payload.model_dump(exclude_none=True)


@traceable(run_type="tool", name="call_facilities_thinker_via_api")
async def _call_facilities_thinker_via_api_impl(
    ctx: RunContextWrapper[Any],
    emergency: Annotated[bool, "Set to true when the resident reports an emergency."] = False,
    message: Annotated[
        str | None,
        "The resident's request. Always provide a succinct yet comprehensive full request summary. If not provided, the last user message in the context will be used.",
    ] = None,
    permission_to_enter: Annotated[
        bool | None,
        "Whether the technician has permission to enter without the resident present. If omitted, defaults based on property settings.",
    ] = None,
    permission_entry_notes: Annotated[
        str | None,
        "Resident-provided access instructions (e.g., call first). Optional when permission_to_enter is True.",
    ] = None,
    issue_resolved_with_self_service: Annotated[
        bool | None,
        "Set to true when the resident confirms the issue was resolved using self-service steps; set to false when the issue remains unresolved after attempting self-service.",
    ] = None,
    self_service_steps_requested: Annotated[
        bool | None,
        "Set to true when a resident explicitly agrees to receive self-service troubleshooting steps; set to false when a resident declines self-service steps.",
    ] = None,
) -> dict | str:
    """Call the facilities thinker via its API endpoint.

    Args:
        ctx: The run context wrapper containing request information

    Returns:
        The response from the facilities thinker
    """
    # Set handoff_in_progress up front for emergency calls — the ESR transfer tool
    # runs regardless of thinker outcome, so suppression must be active even
    # if the API returns None or throws.
    if emergency and settings.interrupt_suppression_enabled:
        ctx.context.handoff_in_progress = True
        logger.info("handoff_in_progress=True set via facilities API (emergency)")

    try:
        channel = get_channel_from_context(ctx.context).lower()
        payload = _build_facilities_payload(
            ctx,
            emergency,
            channel,
            message,
            permission_to_enter,
            permission_entry_notes,
        )
        extra_headers: dict[str, str] = {}
        prev_response_id = str(getattr(ctx.context, "previous_response_id", ""))
        if prev_response_id:
            extra_headers[PREVIOUS_RESPONSE_ID_HEADER] = prev_response_id
        conversation_id = getattr(ctx.context, "openai_conversation_id", None)
        if settings.use_conversations_api and conversation_id:
            extra_headers[CONVERSATION_ID_HEADER] = conversation_id
        extra_headers = extra_headers or None

        with custom_span(
            "Facilities Thinker API Call",
            data={"payload": payload, "channel": channel, "extra_headers": extra_headers},
        ):
            response = await perform_api_call(
                host=settings.facilities_thinker_api_host,
                endpoint="/facilities-resident-thinker/v2/thinker",
                method="POST",
                auth_server="facilities",
                payload=payload,
                extra_headers=extra_headers,
            )
            set_span_data(response=response)  # open ai trace

        if response is None:
            return "Error: No response from facilities thinker API."
        if isinstance(response, dict):
            # Promote to handoff_in_progress if response confirms P1 (covers
            # non-emergency calls that the thinker escalated to P1).
            if not emergency and settings.interrupt_suppression_enabled:
                sr_numbers = response.get("service_request_numbers") or []
                response_is_p1 = any(
                    isinstance(sr, dict) and sr.get("priority_number") == EMERGENCY_PRIORITY_NUMBER
                    for sr in sr_numbers
                )
                if response_is_p1:
                    ctx.context.handoff_in_progress = True
                    logger.info("handoff_in_progress=True set via facilities API (response P1)")
            # Emit whatever TaskActivityEvent this response implies (SR
            # creation today, SR-status / self-service later). The thinker
            # response muxes multiple activities; dispatch lives inside
            # `extract_facilities_thinker_events`, not here.
            publish_task_activity(extract_facilities_thinker_events, response, ctx.context, user_request=message)
            if channel == "voice":
                return normalize_json_values(response)
            return response
    except Exception as e:
        logger.exception("Error calling facilities thinker via API", error=str(e))
        return f"Error calling facilities thinker via API: {repr(e)}"


@function_tool(
    description_override="Call the Facilities thinker REST API to help with maintenance issues. Use this when a resident needs to "
    "(1) create a new service request, (2) check the status of existing requests/work orders, or "
    "(3) retrieve self-service troubleshooting steps before escalating."
)
async def call_facilities_thinker_via_api(
    ctx: RunContextWrapper[Any],
    emergency: Annotated[bool, "Set to true when the resident reports an emergency."] = False,
    message: Annotated[
        str | None,
        "The resident's request. Always provide a succinct yet comprehensive full request summary. If not provided, the last user message in the context will be used.",
    ] = None,
    permission_to_enter: Annotated[
        bool | None,
        "Whether the technician has permission to enter without the resident present. If omitted, defaults based on property settings.",
    ] = None,
    permission_entry_notes: Annotated[
        str | None,
        "Resident-provided access instructions (e.g., call first). Optional when permission_to_enter is True.",
    ] = None,
    issue_resolved_with_self_service: Annotated[
        bool | None,
        "Set to true when the resident confirms the issue was resolved using self-service steps; set to false when the issue remains unresolved after attempting self-service.",
    ] = None,
    self_service_steps_requested: Annotated[
        bool | None,
        "Set to true when a resident explicitly agrees to receive self-service troubleshooting steps; set to false when a resident declines self-service steps.",
    ] = None,
) -> dict | str:
    """Function tool wrapper that delegates to the implementation for easier testing."""

    # Pre-flight verification check (defense in depth)
    is_verified, error_msg = check_verification_status(ctx.context, "call_facilities_thinker_via_api")
    if not is_verified:
        logger.info("Verification check failed for call_facilities_thinker_via_api", error=error_msg)
        return {"error": error_msg, "instructions": error_msg}

    return await _call_facilities_thinker_via_api_impl(
        ctx,
        emergency=emergency,
        message=message,
        permission_to_enter=permission_to_enter,
        permission_entry_notes=permission_entry_notes,
        issue_resolved_with_self_service=issue_resolved_with_self_service,
        self_service_steps_requested=self_service_steps_requested,
    )


async def prefetch_active_service_requests(ctx: RunContextWrapper[Any]):
    tool_output = await _call_facilities_thinker_via_api_impl(ctx, message="List my active service requests")

    instructions = (
        tool_output.get("instructions")
        if isinstance(tool_output, dict)
        else getattr(tool_output, "instructions", None)
    )

    if instructions:
        truncated_instructions = instructions if len(instructions) < 1000 else instructions[:1000] + "..."
        setattr(ctx.context, "active_service_requests", truncated_instructions)
        return "call_facilities_thinker_via_api"

    return None


async def _queue_resolution_ack_impl(
    ctx: RunContextWrapper[Any],
    message: Annotated[str, "Concise summary of the resolved issue in the resident's words"],
    self_service_steps_requested: Annotated[
        bool,
        "Set to true when a resident explicitly agrees to receive self-service troubleshooting steps; set to false when a resident declines self-service steps.",
    ] = True,
    issue_resolved_with_self_service: Annotated[
        bool,
        "Set to true when the resident confirms the issue was resolved using self-service steps; set to false when the issue remains unresolved after attempting self-service.",
    ] = True,
) -> str:
    # Make the ack text explicit and add the required no-action note
    summary = (message or "").strip()
    resolved_message = (
        f"{summary} — no further action is needed" if summary else "Issue resolved — no further action is needed"
    )

    # Use a shallow copy to avoid expensive deepcopy of histories while still
    # keeping background mutations isolated from the live session state.
    ctx_copy = RunContextWrapper(ctx.context.model_copy(deep=False))
    curr_ctx = contextvars.copy_context()

    async def _run():
        try:
            await _call_facilities_thinker_via_api_impl(
                ctx_copy,
                emergency=False,
                message=resolved_message,
                self_service_steps_requested=self_service_steps_requested,
                issue_resolved_with_self_service=issue_resolved_with_self_service,
            )
        except Exception:
            logger.exception("Failed to send resolution ack")

    task = asyncio.create_task(curr_ctx.run(_run), name="facilities_resolution_ack")

    def _track_task_result(done: asyncio.Task[Any]) -> None:
        BACKGROUND_TASKS.discard(done)
        try:
            done.result()
        except asyncio.CancelledError:
            logger.info("Resolution ack task was cancelled")
        except Exception:
            logger.exception("Resolution ack task failed")

    BACKGROUND_TASKS.add(task)
    task.add_done_callback(_track_task_result)
    return "Noted. I'll let the team know the issue is resolved."


@function_tool(
    description_override=(
        "Queue a background call to mark a maintenance issue as resolved (self-service). "
        "Call this whenever a resident confirms the issue is fixed so the ack appears in traces."
    )
)
async def queue_resolution_ack(
    ctx: RunContextWrapper[Any],
    message: Annotated[str, "Concise summary of the resolved issue in the resident's words"],
    self_service_steps_requested: Annotated[
        bool,
        "Set to true when a resident explicitly agrees to receive self-service troubleshooting steps; set to false when a resident declines self-service steps.",
    ] = True,
    issue_resolved_with_self_service: Annotated[
        bool,
        "Set to true when the resident confirms the issue was resolved using self-service steps; set to false when the issue remains unresolved after attempting self-service.",
    ] = True,
) -> str:
    """Function tool wrapper delegating to the implementation for easier testing."""

    return await _queue_resolution_ack_impl(
        ctx,
        message=message,
        self_service_steps_requested=self_service_steps_requested,
        issue_resolved_with_self_service=issue_resolved_with_self_service,
    )
