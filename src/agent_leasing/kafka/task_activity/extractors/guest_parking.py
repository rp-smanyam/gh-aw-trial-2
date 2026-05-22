"""Guest Parking activity extractor.

Wraps `issue_guest_parking_pass` (Loft MCP server). One activity per
successful pass issuance. Two URLs land in `extra.map`:

- `download_url` — the Twilio/Loft-hosted PDF of the parking pass that
  the tool returns directly.
- `loft_living_link` — the resident-facing Loft Living portal URL for
  parking passes (built from `uc_portal_base_url` + `static_paths.parking_passes`).

Lumina renders whichever it prefers (per ticket notes line 234).
"""

from __future__ import annotations

from urllib.parse import urljoin

from agent_leasing.kafka.common_context import build_common_context
from agent_leasing.kafka.references import build_activity_references
from agent_leasing.kafka.task_activity.event import build_task_activity_event
from agent_leasing.kafka.task_activity.event_context import build_common_event_context
from agent_leasing.models.context import SessionScope

MCP_TOOL_NAME = "issue_guest_parking_pass"
ACTIVITY_SUMMARY = "Created Guest Parking Pass"


def extract_guest_parking_events(
    tool_output: dict | None,
    *,
    context: SessionScope,
    mcp_arguments: dict | None = None,
    user_request: str | None = None,
) -> list[dict]:
    if not isinstance(tool_output, dict):
        return []

    pass_record = (tool_output.get("data") or {}).get("addParkingPass")
    if not isinstance(pass_record, dict) or not pass_record.get("id"):
        return []

    common_kwargs = build_common_event_context(context)
    ask_request = context.ask_request
    product_info = ask_request.product_info if ask_request else None

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
        ("id", "parking_pass_id"),
        ("downloadUrl", "download_url"),
        ("vehicleMake", "vehicle_make"),
        ("vehicleModel", "vehicle_model"),
        ("vehicleLicensePlate", "vehicle_license_plate"),
        ("validFrom", "valid_from"),
        ("validTo", "valid_to"),
    ):
        value = pass_record.get(source_key)
        # Explicit None/"" check matches community_event_signup style and
        # avoids silent-drop on legitimate falsy values (e.g., id == 0).
        if value not in (None, ""):
            extras[dest_key] = str(value)

    portal_link = _loft_parking_link(product_info)
    if portal_link:
        extras["loft_living_link"] = portal_link

    detail = _build_detail(pass_record)

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


def _build_detail(pass_record: dict) -> str:
    make = pass_record.get("vehicleMake")
    model = pass_record.get("vehicleModel")
    plate = pass_record.get("vehicleLicensePlate")
    descriptor = " ".join(part for part in (make, model) if part)
    if descriptor and plate:
        return f"Created guest parking pass for {descriptor} ({plate})"
    if descriptor:
        return f"Created guest parking pass for {descriptor}"
    if plate:
        return f"Created guest parking pass for plate {plate}"
    return "Created guest parking pass"


def _loft_parking_link(product_info) -> str | None:
    if product_info is None:
        return None
    base_url = getattr(product_info, "uc_portal_base_url", None)
    static_paths = getattr(product_info, "static_paths", None)
    parking_path = getattr(static_paths, "parking_passes", None) if static_paths else None
    if not base_url or not parking_path:
        return None
    return urljoin(base_url, parking_path)
