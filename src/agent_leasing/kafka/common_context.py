"""Builder for the `extra.map` common context shared across every
TaskActivityEvent. Activity-specific keys are layered on top of this at
extractor time.
"""

ORIGINATING_SOURCE_RESIDENT_AI = "RESIDENT_AI"


def build_common_context(
    *,
    channel: str,
    first_name: str | None = None,
    last_name: str | None = None,
    ab_unit_number: str | None = None,
    ab_building_number: str | None = None,
    chat_session_id: str | None = None,
    thread_id: str | None = None,
    call_sid: str | None = None,
    property_name: str | None = None,
    property_timezone: str | None = None,
    resident_stream_id: str | None = None,
    os_company_id: str | None = None,
    os_property_id: str | None = None,
    resident_household_id: str | None = None,
    resident_member_id: str | None = None,
) -> dict[str, str]:
    """Return the common-context dict for `TaskActivityEvent.extra.map`.

    Keys only appear when their value is truthy — Avro map values are
    required strings, so a missing upstream field is omitted entirely
    rather than emitted as an empty string. Resident identity rides in
    `references` (type RESIDENT), not in `extra.map`.

    `resident_stream_id` mirrors the prospect-side `prospect_stream_id`
    convention so reporting consumers can correlate across user types;
    it is sourced from `product_info.thread_id` (the upstream
    Knock-style stream identifier — distinct from the agent-internal
    LangSmith `thread_id` already emitted above).
    """
    if not channel:
        raise ValueError("channel must be a non-empty string")

    ctx: dict[str, str] = {
        "originating_source": ORIGINATING_SOURCE_RESIDENT_AI,
        "channel": channel,
    }
    optional = [
        ("first_name", first_name),
        ("last_name", last_name),
        ("ab_unit_number", ab_unit_number),
        ("ab_building_number", ab_building_number),
        ("chat_session_id", chat_session_id),
        ("thread_id", thread_id),
        ("call_sid", call_sid),
        ("property_name", property_name),
        ("property_timezone", property_timezone),
        ("resident_stream_id", resident_stream_id),
        ("os_company_id", os_company_id),
        ("os_property_id", os_property_id),
        ("resident_household_id", resident_household_id),
        ("resident_member_id", resident_member_id),
    ]
    ctx.update({key: str(value) for key, value in optional if value})
    return ctx
