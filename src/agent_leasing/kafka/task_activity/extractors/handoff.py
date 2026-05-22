"""Handoff-to-staff activity extractor.

Three caller surfaces emit a handoff TaskActivityEvent:

1. `transfer_to_staff_text` (CHAT/SMS/EMAIL) — local function tool.
2. `transfer_to_staff_voice` (VOICE) — local function tool.
3. `emergency_service_transfer_{basic,advanced,rpcc}` (VOICE) — local
   function tools that route the live call to maintenance AFTER the ESR
   has already been created. They always emit `reason=EMERGENCY`.
4. SMS/EMAIL active-handoff short-circuit at
   `server.py::_handle_active_handoff` — no tool call. Always emits
   `reason=ALREADY_IN_HANDOFF`.

Activity summary: `Handoff to Staff - <Sub-label>` where sub-label is
the human-readable form of the `HandoffReasonCode`. The channel rides
in `extra.channel`.

Whichever transfer message the call site provides lands in both
`activity.detail` and `extra.handoff_message`. The handoff portal link
applies to text channels only (voice has no portal link).
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_leasing.api.model import HandoffReasonCode, HandoffTopic
from agent_leasing.kafka.common_context import build_common_context
from agent_leasing.kafka.references import build_activity_references
from agent_leasing.kafka.task_activity.event import build_task_activity_event
from agent_leasing.kafka.task_activity.event_context import build_common_event_context
from agent_leasing.kafka.task_activity.extractors._common import optional_str
from agent_leasing.models.context import SessionScope

ACTIVITY_SUMMARY_HANDOFF = "Handoff to Staff"

# Mirrors the agent-facing description of each reason code in
# `Handoff to Staff - <Sub-label>` form (ticket notes line 232).
_REASON_SUB_LABELS: dict[HandoffReasonCode, str] = {
    HandoffReasonCode.RESIDENT_REQUESTED: "Resident Requested",
    HandoffReasonCode.SYSTEM_ERROR: "System Error",
    HandoffReasonCode.EMERGENCY: "Emergency",
    HandoffReasonCode.OUT_OF_SCOPE: "Out of Scope",
    HandoffReasonCode.MISSING_DATA: "Missing Data",
    HandoffReasonCode.ALREADY_IN_HANDOFF: "Already in Handoff",
    HandoffReasonCode.COMPLAINT: "Complaint",
}


@dataclass(frozen=True)
class HandoffFacts:
    """One staff handoff. `handoff_message` is the AI-authored summary
    sent to staff; `handoff_portal_link` is the resident-facing portal
    URL (text channels only); `reason` is the agent-set
    `HandoffReasonCode` (drives the activity sub-label); `topic` is the
    optional `HandoffTopic` tag for what the conversation is about.
    """

    handoff_message: str | None
    reason: HandoffReasonCode | None = None
    handoff_portal_link: str | None = None
    topic: HandoffTopic | None = None


def parse_handoff_facts(
    transfer_message: str | None,
    reason: HandoffReasonCode | None = None,
    handoff_portal_link: str | None = None,
    topic: HandoffTopic | None = None,
) -> list[HandoffFacts]:
    """Return a one-element list — every call site only invokes this on
    a confirmed-success path, so we always have one handoff to record.

    Empty `transfer_message` is allowed: the voice tool can transfer
    without a summary (resident refused). Downstream consumers can treat
    a missing message as "no summary" rather than "no event".
    """
    return [
        HandoffFacts(
            handoff_message=optional_str(transfer_message),
            reason=reason,
            handoff_portal_link=optional_str(handoff_portal_link),
            topic=topic,
        )
    ]


def build_handoff_event(
    facts: HandoffFacts,
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
    property_name: str | None = None,
    property_timezone: str | None = None,
    resident_stream_id: str | None = None,
    os_company_id: str | None = None,
    os_property_id: str | None = None,
    resident_household_id: str | None = None,
    resident_member_id: str | None = None,
) -> dict:
    """Build a single Handoff `TaskActivityEvent` dict."""
    summary = _build_summary(facts.reason)
    detail = _build_detail(facts)

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
    if facts.reason is not None:
        extras["handoff_reason"] = facts.reason.value
    if facts.topic is not None:
        extras["handoff_topic"] = facts.topic.value
    if facts.handoff_message:
        extras["handoff_message"] = facts.handoff_message
    if facts.handoff_portal_link:
        # Wire key is `loft_living_link` (unified with sr_created /
        # guest_parking) — both point to the resident's Loft Living
        # portal page for this activity. Internal field name stays
        # `handoff_portal_link` to preserve call-site semantics.
        extras["loft_living_link"] = facts.handoff_portal_link

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


def _build_summary(reason: HandoffReasonCode | None) -> str:
    sub_label = _REASON_SUB_LABELS.get(reason) if reason is not None else None
    return f"{ACTIVITY_SUMMARY_HANDOFF} - {sub_label}" if sub_label else ACTIVITY_SUMMARY_HANDOFF


def _build_detail(facts: HandoffFacts) -> str:
    if facts.handoff_message:
        return f"Handed off to staff: {facts.handoff_message}"
    return "Handed off to staff"


def extract_handoff_events(
    transfer_message: str | None,
    *,
    context: SessionScope,
    reason: HandoffReasonCode | None = None,
    handoff_portal_link: str | None = None,
    topic: HandoffTopic | None = None,
) -> list[dict]:
    """Parse + derive handoff kwargs from `context`, then build one
    event per recorded handoff (always exactly one today).
    """
    facts_list = parse_handoff_facts(transfer_message, reason, handoff_portal_link, topic)
    if not facts_list:
        return []
    common_kwargs = build_common_event_context(context)
    return [build_handoff_event(facts, **common_kwargs) for facts in facts_list]
