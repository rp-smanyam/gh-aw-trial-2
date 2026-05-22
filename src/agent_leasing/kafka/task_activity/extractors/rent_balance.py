"""Rent and Balance activity extractor.

Wraps `get_rent_information` (Policy & Ledger MCP server). One activity
per successful tool call. The conversational user_request is captured
from the call's MCP arguments (the LLM's restatement of what the
resident asked) so the activity stream can answer "what was the
specific question?" alongside the raw rent fields.
"""

from __future__ import annotations

from agent_leasing.kafka.common_context import build_common_context
from agent_leasing.kafka.references import build_activity_references
from agent_leasing.kafka.task_activity.event import build_task_activity_event
from agent_leasing.kafka.task_activity.event_context import build_common_event_context
from agent_leasing.models.context import SessionScope

MCP_TOOL_NAME = "get_rent_information"
ACTIVITY_SUMMARY = "Rent and Balance"


def extract_rent_balance_events(
    tool_output: dict | None,
    *,
    context: SessionScope,
    mcp_arguments: dict | None = None,
    user_request: str | None = None,
) -> list[dict]:
    """One event per successful `get_rent_information` call. Empty list
    when the output isn't a dict (defensive — failed calls don't reach
    the post-processor)."""
    if not isinstance(tool_output, dict):
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
    for key in ("current_balance", "past_due_balance", "rent", "rent_due_date"):
        value = tool_output.get(key)
        # Use explicit None/"" check instead of `if value:` so a numeric 0
        # (or "$0.00" should the upstream type ever change) is preserved.
        if value not in (None, ""):
            extras[key] = str(value)

    resolved_user_request = user_request or _coerce_str(_user_request_from_args(mcp_arguments))
    if resolved_user_request:
        extras["user_request"] = resolved_user_request

    detail = (
        f"Fetched rent and balance for: {resolved_user_request}"
        if resolved_user_request
        else "Fetched rent and balance"
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
    """The Policy & Ledger MCP wrapper passes `chat_summary` for the
    LLM's restatement of the user's question. Falls back to None when
    arguments are missing."""
    if not mcp_arguments:
        return None
    return mcp_arguments.get("chat_summary") or mcp_arguments.get("user_request")


def _coerce_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
