"""Community Event Signup activity extractor.

Wraps `sign_up_community_events` (Loft MCP server). One activity per
successful signup. The tool returns `registerEvent: null` on failure
(event not found / already full) — the post-processor only fires for
successful (non-error) tool calls, but we still defensive-skip when
`registerEvent` is null since the activity should only record actual
signups.
"""

from __future__ import annotations

from agent_leasing.kafka.common_context import build_common_context
from agent_leasing.kafka.references import build_activity_references
from agent_leasing.kafka.task_activity.event import build_task_activity_event
from agent_leasing.kafka.task_activity.event_context import build_common_event_context
from agent_leasing.models.context import SessionScope

MCP_TOOL_NAME = "sign_up_community_events"
ACTIVITY_SUMMARY = "Signed Up for Community Event"


def extract_community_event_signup_events(
    tool_output: dict | None,
    *,
    context: SessionScope,
    mcp_arguments: dict | None = None,
    user_request: str | None = None,
) -> list[dict]:
    if not isinstance(tool_output, dict):
        return []

    signup = tool_output.get("registerEvent")
    if not isinstance(signup, dict) or not signup.get("eventId"):
        return []

    common_kwargs = build_common_event_context(context)

    extras = build_common_context(
        channel=common_kwargs["channel"],
        first_name=common_kwargs.get("first_name"),
        last_name=common_kwargs.get("last_name"),
        ab_unit_number=common_kwargs.get("ab_unit_number"),
        ab_building_number=common_kwargs.get("ab_building_number"),
        chat_session_id=common_kwargs.get("chat_session_id"),
        thread_id=common_kwargs.get("thread_id"),
        call_sid=common_kwargs.get("call_sid"),
        property_name=common_kwargs.get("property_name"),
        property_timezone=common_kwargs.get("property_timezone"),
        resident_stream_id=common_kwargs.get("resident_stream_id"),
        os_company_id=common_kwargs.get("os_company_id"),
        os_property_id=common_kwargs.get("os_property_id"),
        resident_household_id=common_kwargs.get("resident_household_id"),
        resident_member_id=common_kwargs.get("resident_member_id"),
    )
    for source_key, dest_key in (
        ("eventId", "event_id"),
        ("eventSignupId", "event_signup_id"),
        ("guests", "guests"),
        ("attendeesCount", "attendees_count"),
        ("totalCost", "total_cost"),
        ("successText", "event_summary"),
    ):
        value = signup.get(source_key)
        if value not in (None, ""):
            extras[dest_key] = str(value)

    detail = _build_detail(signup)

    event = build_task_activity_event(
        task_id=common_kwargs["task_id"],
        activity_summary=ACTIVITY_SUMMARY,
        activity_detail=detail,
        references=build_activity_references(
            knock_company_id=common_kwargs.get("knock_company_id"),
            knock_property_id=common_kwargs.get("knock_property_id"),
            knock_resident_id=common_kwargs.get("knock_resident_id"),
        ),
        extra=extras,
    )
    return [event]


def _build_detail(signup: dict) -> str:
    success_text = signup.get("successText")
    event_id = signup.get("eventId")
    if success_text:
        # `successText` typically contains the event title and time slot —
        # most informative one-liner the tool gives us.
        return f"Signed up for community event: {success_text}"
    return f"Signed up for community event {event_id}"
