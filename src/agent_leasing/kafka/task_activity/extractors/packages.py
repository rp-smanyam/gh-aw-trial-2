"""Packages activity extractor.

Wraps `get_residents_packages` (Loft MCP server). One activity per
successful retrieval; flattens the `packages_list` count + per-package
type/station summaries into `extra.map`. The packages list itself is
not flattened individually — we capture aggregate descriptors (count,
unique types, unique stations) so a downstream consumer can answer
"how many packages did the resident ask about, of what type?" without
us bloating the Avro `extra.map<string, string>` with a JSON blob per
package.
"""

from __future__ import annotations

from agent_leasing.kafka.common_context import build_common_context
from agent_leasing.kafka.references import build_activity_references
from agent_leasing.kafka.task_activity.event import build_task_activity_event
from agent_leasing.kafka.task_activity.event_context import build_common_event_context
from agent_leasing.models.context import SessionScope

MCP_TOOL_NAME = "get_residents_packages"
ACTIVITY_SUMMARY = "Package Questions Asked"


def extract_packages_events(
    tool_output: dict | None,
    *,
    context: SessionScope,
    mcp_arguments: dict | None = None,
    user_request: str | None = None,
) -> list[dict]:
    if not isinstance(tool_output, dict):
        return []

    packages_list = tool_output.get("packages_list")
    if not isinstance(packages_list, list):
        packages_list = []
    count = tool_output.get("packages_count")
    if not isinstance(count, int):
        count = len(packages_list)

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
    extras["packages_count"] = str(count)

    package_types = _unique_strs(p.get("packageType") for p in packages_list if isinstance(p, dict))
    if package_types:
        extras["package_types"] = ",".join(package_types)
    package_stations = _unique_strs(p.get("packageStation") for p in packages_list if isinstance(p, dict))
    if package_stations:
        extras["package_stations"] = ",".join(package_stations)

    detail = f"Resident asked about {count} package(s)" if count else "Resident asked about packages (none on file)"

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


def _unique_strs(values) -> list[str]:
    """Preserve first-seen order, drop falsy / non-string values."""
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if not v or not isinstance(v, str):
            continue
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out
