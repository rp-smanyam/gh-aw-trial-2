"""Lease Info activity extractor.

Wraps `get_lease_term_information` (Policy & Ledger MCP server).
Pulls lease_start, lease_end, unit, occupants, building from the
`result` block. The conversational `user_request` (the resident's
specific question — e.g., "when does my lease end?") is captured
from the LLM's `chat_summary` MCP argument when present.
"""

from __future__ import annotations

from agent_leasing.kafka.common_context import build_common_context
from agent_leasing.kafka.references import build_activity_references
from agent_leasing.kafka.task_activity.event import build_task_activity_event
from agent_leasing.kafka.task_activity.event_context import build_common_event_context
from agent_leasing.models.context import SessionScope

MCP_TOOL_NAME = "get_lease_term_information"
ACTIVITY_SUMMARY = "Lease Info"


def extract_lease_info_events(
    tool_output: dict | None,
    *,
    context: SessionScope,
    mcp_arguments: dict | None = None,
    user_request: str | None = None,
) -> list[dict]:
    if not isinstance(tool_output, dict):
        return []

    result = tool_output.get("result")
    if not isinstance(result, dict):
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
        ("lease_start", "lease_start"),
        ("lease_end", "lease_end"),
        ("unit", "lease_unit"),
        ("buildingNumber", "lease_building_number"),
    ):
        value = result.get(source_key)
        # Explicit None/"" check to match community_event_signup; preserves
        # numeric zero values (e.g., a building number of 0) instead of
        # silently dropping them under `if value:`.
        if value not in (None, ""):
            extras[dest_key] = str(value)
    occupants = result.get("occupants")
    if isinstance(occupants, list) and occupants:
        extras["occupants_count"] = str(len(occupants))

    resolved_user_request = user_request or _coerce_str(_user_request_from_args(mcp_arguments))
    if resolved_user_request:
        extras["user_request"] = resolved_user_request

    detail = (
        f"Fetched lease information for: {resolved_user_request}"
        if resolved_user_request
        else "Fetched lease information"
    )

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


def _user_request_from_args(mcp_arguments: dict | None) -> str | None:
    if not mcp_arguments:
        return None
    return mcp_arguments.get("chat_summary") or mcp_arguments.get("user_request")


def _coerce_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
