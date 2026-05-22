"""SR-created activity extractor.

Two surfaces produce DIFFERENT payload shapes:
- MCP `create_service_request` tool output (parsed from `CallToolResult.content[0].text`):
  flat fields `service_request_id` + `service_request_created: true` + `priority_number` + `priority_name`.
- `call_facilities_thinker_via_api` direct response (already a dict):
  `service_request_numbers: [{sr_id, priority_number, priority_name}, ...]` + `action_taken: "service_request_created"`.

`parse_sr_created_facts` detects which shape it received and normalizes
both to a list of `SRCreatedFacts`. `build_sr_created_event` then turns
each fact into a `TaskActivityEvent` dict (one per SR, since the
facilities-thinker shape can carry multiple SRs in a single response).

Activity summary: `Create SR - Emergency` (priority_number == "1") /
`Create SR - Non-Emergency`.

TODO(follow-up): `services/analytics_service.py::_extract_sr_metadata`
+ `_extract_sr_metadata_legacy` parse these same shapes for the
data-curation event path — dedupe them through `parse_sr_created_facts`
in a separate refactor PR.
"""

from dataclasses import dataclass
from urllib.parse import urljoin

from agent_leasing.kafka.common_context import build_common_context
from agent_leasing.kafka.references import build_activity_references
from agent_leasing.kafka.task_activity.event import build_task_activity_event
from agent_leasing.kafka.task_activity.event_context import build_common_event_context
from agent_leasing.kafka.task_activity.extractors._common import optional_str
from agent_leasing.models.context import SessionScope

MCP_TOOL_NAME = "create_service_request"
# When invoked via the MCP post-processor, the LLM's restatement of the
# resident's issue lives under this key in the call's `arguments`.
_MCP_USER_REQUEST_ARG = "chat_summary"
ACTION_SR_CREATED = "service_request_created"
EMERGENCY_PRIORITY_NUMBER = "1"
ACTIVITY_SUMMARY_EMERGENCY = "Create SR - Emergency"
ACTIVITY_SUMMARY_NON_EMERGENCY = "Create SR - Non-Emergency"


@dataclass(frozen=True)
class SRCreatedFacts:
    """One service request created in a single tool call."""

    sr_number: str
    priority_number: str | None
    priority_name: str | None


def parse_sr_created_facts(tool_output: dict | None) -> list[SRCreatedFacts]:
    """Return one entry per SR created. Empty list when the output isn't
    an SR-creation success (wrong shape, wrong action, malformed input).

    Handles both the facilities-thinker-API shape (`service_request_numbers`
    + `action_taken`) and the legacy MCP-tool shape (flat
    `service_request_id` + `service_request_created`).
    """
    if not isinstance(tool_output, dict):
        return []

    if tool_output.get("action_taken") == ACTION_SR_CREATED:
        return _parse_thinker_shape(tool_output)
    if tool_output.get("service_request_created") is True:
        return _parse_mcp_shape(tool_output)
    return []


def _parse_thinker_shape(tool_output: dict) -> list[SRCreatedFacts]:
    sr_numbers = tool_output.get("service_request_numbers")
    if not isinstance(sr_numbers, list):
        return []
    facts: list[SRCreatedFacts] = []
    for sr in sr_numbers:
        if not isinstance(sr, dict):
            continue
        sr_id = sr.get("sr_id")
        if not sr_id:
            continue
        facts.append(
            SRCreatedFacts(
                sr_number=str(sr_id),
                priority_number=optional_str(sr.get("priority_number")),
                priority_name=optional_str(sr.get("priority_name")),
            )
        )
    return facts


def _parse_mcp_shape(tool_output: dict) -> list[SRCreatedFacts]:
    sr_id = tool_output.get("service_request_id")
    if not sr_id:
        return []
    return [
        SRCreatedFacts(
            sr_number=str(sr_id),
            priority_number=optional_str(tool_output.get("priority_number")),
            priority_name=optional_str(tool_output.get("priority_name")),
        )
    ]


def build_sr_created_event(
    facts: SRCreatedFacts,
    *,
    task_id: str,
    channel: str,
    knock_company_id: str | None = None,
    knock_property_id: str | None = None,
    knock_resident_id: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    ab_unit_number: str | None = None,
    ab_building_number: str | None = None,
    chat_session_id: str | None = None,
    thread_id: str | None = None,
    call_sid: str | None = None,
    user_request: str | None = None,
    loft_living_link: str | None = None,
    property_name: str | None = None,
    property_timezone: str | None = None,
    resident_stream_id: str | None = None,
    os_company_id: str | None = None,
    os_property_id: str | None = None,
    resident_household_id: str | None = None,
    resident_member_id: str | None = None,
) -> dict:
    """Build a single SR-created `TaskActivityEvent` dict."""
    is_emergency = facts.priority_number == EMERGENCY_PRIORITY_NUMBER
    summary = ACTIVITY_SUMMARY_EMERGENCY if is_emergency else ACTIVITY_SUMMARY_NON_EMERGENCY
    detail = _build_detail(facts, user_request)

    extras = build_common_context(
        channel=channel,
        first_name=first_name,
        last_name=last_name,
        ab_unit_number=ab_unit_number,
        ab_building_number=ab_building_number,
        chat_session_id=chat_session_id,
        thread_id=thread_id,
        call_sid=call_sid,
        property_name=property_name,
        property_timezone=property_timezone,
        resident_stream_id=resident_stream_id,
        os_company_id=os_company_id,
        os_property_id=os_property_id,
        resident_household_id=resident_household_id,
        resident_member_id=resident_member_id,
    )
    extras["sr_number"] = facts.sr_number
    if facts.priority_number is not None:
        extras["sr_priority_number"] = facts.priority_number
    if facts.priority_name is not None:
        extras["sr_priority_name"] = facts.priority_name
    if loft_living_link:
        extras["loft_living_link"] = loft_living_link
    if user_request:
        extras["user_request"] = user_request

    return build_task_activity_event(
        task_id=task_id,
        activity_summary=summary,
        activity_detail=detail,
        references=build_activity_references(
            knock_company_id=knock_company_id,
            knock_property_id=knock_property_id,
            knock_resident_id=knock_resident_id,
        ),
        extra=extras,
    )


def _build_detail(facts: SRCreatedFacts, user_request: str | None) -> str:
    sr_clause = (
        f"Created {facts.priority_name} SR {facts.sr_number}"
        if facts.priority_name
        else f"Created SR {facts.sr_number}"
    )
    if user_request:
        return f"{sr_clause} for: {user_request}"
    return sr_clause


def extract_sr_created_events(
    tool_output: dict | None,
    *,
    context: SessionScope,
    mcp_arguments: dict | None = None,
    user_request: str | None = None,
) -> list[dict]:
    """Parse + derive SR-specific kwargs from `context`, then build one
    event per SR. Empty list = nothing to emit.

    Two caller surfaces today:
    - MCP post-processor passes `mcp_arguments` (raw call args dict);
      the resident's wording lives at `arguments[_MCP_USER_REQUEST_ARG]`.
    - Facilities-thinker caller passes `user_request` directly.
    """
    facts_list = parse_sr_created_facts(tool_output)
    if not facts_list:
        return []

    ask_request = context.ask_request
    product_info = ask_request.product_info if ask_request else None
    common_kwargs = build_common_event_context(context)
    portal_link = _loft_service_request_link(product_info)
    resolved_user_request = user_request or (mcp_arguments or {}).get(_MCP_USER_REQUEST_ARG)

    return [
        build_sr_created_event(
            facts,
            **common_kwargs,
            loft_living_link=portal_link,
            user_request=resolved_user_request,
        )
        for facts in facts_list
    ]


def _loft_service_request_link(product_info) -> str | None:
    """SR portal URL — `uc_portal_base_url` joined with
    `static_paths.service_request`. Mirrors `create_link("service_request")`
    so the activity record carries the same URL the resident saw.
    """
    if product_info is None:
        return None
    base_url = getattr(product_info, "uc_portal_base_url", None)
    static_paths = getattr(product_info, "static_paths", None)
    sr_path = getattr(static_paths, "service_request", None) if static_paths else None
    if not base_url or not sr_path:
        return None
    return urljoin(base_url, sr_path)
