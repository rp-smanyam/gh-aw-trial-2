"""Frustrated-user activity extractor.

Driven by the responder's structured output
(`ResidentResponderOutput.user_frustrated`). Emits the
`FRUSTRATED_USER` activity at most once per conversation: the first
turn the responder flips `user_frustrated=True` flips
`SessionScope.frustrated_user_emitted` to True, and every subsequent
turn — even if `user_frustrated` is True again — produces no event.

`task.id` is conversation-scoped, so once-per-`task.id` and
once-per-`SessionScope` are the same gate; we use the session flag
because it's already cached across messages.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_leasing.kafka.common_context import build_common_context
from agent_leasing.kafka.references import build_activity_references
from agent_leasing.kafka.task_activity.event import build_task_activity_event
from agent_leasing.kafka.task_activity.event_context import build_common_event_context
from agent_leasing.kafka.task_activity.extractors._common import optional_str
from agent_leasing.models.context import SessionScope

ACTIVITY_SUMMARY_FRUSTRATED_USER = "Frustrated User"


@dataclass(frozen=True)
class FrustratedUserFacts:
    user_message: str | None


def parse_frustrated_user_facts(
    user_frustrated: bool,
    user_message: str | None,
    *,
    already_emitted: bool,
) -> list[FrustratedUserFacts]:
    """Return a one-element list only on the first True. Empty list when
    `user_frustrated` is False or the dedup gate has already fired.
    """
    if not user_frustrated:
        return []
    if already_emitted:
        return []
    return [FrustratedUserFacts(user_message=optional_str(user_message))]


def build_frustrated_user_event(
    facts: FrustratedUserFacts,
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
    """Build a single `FRUSTRATED_USER` `TaskActivityEvent` dict."""
    detail = (
        f"Resident frustration detected: {facts.user_message}"
        if facts.user_message
        else "Resident frustration detected"
    )

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
    if facts.user_message:
        extras["user_message"] = facts.user_message

    return build_task_activity_event(
        task_id=task_id,
        activity_summary=ACTIVITY_SUMMARY_FRUSTRATED_USER,
        activity_detail=detail,
        references=build_activity_references(
            knock_company_id=knock_company_id,
            knock_property_id=knock_property_id,
            knock_resident_id=knock_resident_id,
        ),
        extra=extras,
    )


def extract_frustrated_user_events(
    user_frustrated: bool,
    *,
    context: SessionScope,
    user_message: str | None = None,
) -> list[dict]:
    """Parse + derive common kwargs from `context`, then build the
    frustration event when `user_frustrated` flipped True for the first
    time on this session. Sets `context.frustrated_user_emitted=True`
    on emit so subsequent calls in the same session are no-ops.
    """
    facts_list = parse_frustrated_user_facts(
        user_frustrated,
        user_message,
        already_emitted=context.frustrated_user_emitted,
    )
    if not facts_list:
        return []
    common_kwargs = build_common_event_context(context)
    events = [build_frustrated_user_event(facts, **common_kwargs) for facts in facts_list]
    # Dedup is delivery-time, not build-time: the caller passes
    # `on_success=lambda: setattr(context, "frustrated_user_emitted", True)`
    # to publish_task_activity, which fires the callback only after the
    # event lands on the topic. A failed publish leaves the flag clear
    # and the next turn can retry. There is a small duplicate-emit window
    # (turn N+1 fires before turn N's publish completes), accepted as the
    # cost of at-least-once delivery for the FRUSTRATED_USER signal.
    return events
