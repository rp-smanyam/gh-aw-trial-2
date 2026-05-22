"""Q&A activity extractor.

Driven by the responder's structured output (`ResidentResponderOutput`),
not a tool call. One emit per turn where the responder set
`qna_flow` in `workflow_codes`.

`answered` is derived from the same workflow_codes list:
`answered = "handoff_to_human_flow" not in workflow_codes`. When the
agent fires the handoff flow alongside `qna_flow` in the same turn —
including the verification step that asks the resident to confirm a
transfer — the Q&A is not considered answered.

`qna_topics` is the responder's taxonomy list (`CATEGORY.SUBTOPIC`
strings). Empty list is allowed; downstream consumers can treat the
absence of topics as "topic not classified" rather than no event.

Activity summary: `Property Q&A - Answered` / `Property Q&A - Unanswered`.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from agent_leasing.kafka.common_context import build_common_context
from agent_leasing.kafka.references import build_activity_references
from agent_leasing.kafka.task_activity.event import build_task_activity_event
from agent_leasing.kafka.task_activity.event_context import build_common_event_context
from agent_leasing.kafka.task_activity.extractors._common import optional_str
from agent_leasing.models.context import SessionScope

logger = structlog.getLogger(__name__)

ACTIVITY_SUMMARY_QNA = "Property Q&A"
QNA_FLOW_CODE = "qna_flow"
HANDOFF_FLOW_CODE = "handoff_to_human_flow"


@dataclass(frozen=True)
class QnAFacts:
    """One Q&A turn. `answered=False` means the agent could not satisfy
    the question this turn (handoff was also fired).
    """

    answered: bool
    qna_topics: tuple[str, ...]
    user_message: str | None


def parse_qna_facts(
    workflow_codes: list[str] | None,
    qna_topics: list[str] | None,
    user_message: str | None,
) -> list[QnAFacts]:
    """Return one entry when `qna_flow` is set, empty list otherwise.

    The verification-step turn (agent has decided to hand off but is
    still asking the resident to confirm) carries both `qna_flow` and
    `handoff_to_human_flow` — that turn emits with `answered=False`.
    """
    if not workflow_codes or QNA_FLOW_CODE not in workflow_codes:
        return []
    answered = HANDOFF_FLOW_CODE not in workflow_codes
    raw = qna_topics or []
    topics = tuple(t for t in raw if isinstance(t, str) and t)
    if len(topics) != len(raw):
        # Schema-strict structured output should make this unreachable from
        # the responder path; if it fires, something is bypassing Pydantic.
        logger.warning("qna_topics_dropped_invalid_entries", original=raw, kept=list(topics))
    return [
        QnAFacts(
            answered=answered,
            qna_topics=topics,
            user_message=optional_str(user_message),
        )
    ]


def build_qna_event(
    facts: QnAFacts,
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
    """Build a single Q&A `TaskActivityEvent` dict."""
    summary = f"{ACTIVITY_SUMMARY_QNA} - {'Answered' if facts.answered else 'Unanswered'}"
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
    # Avro `extra` is a map<string,string>; flatten bool to "true"/"false"
    # and join the topic list into a comma-separated value.
    extras["qna_answered"] = "true" if facts.answered else "false"
    if facts.qna_topics:
        extras["qna_topics"] = ",".join(facts.qna_topics)
    if facts.user_message:
        extras["user_message"] = facts.user_message

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


def _build_detail(facts: QnAFacts) -> str:
    state = "Answered" if facts.answered else "Unanswered"
    if facts.qna_topics:
        topics = ", ".join(facts.qna_topics)
        return f"{state} Q&A on: {topics}"
    return f"{state} Q&A"


def extract_qna_events(
    workflow_codes: list[str] | None,
    *,
    context: SessionScope,
    qna_topics: list[str] | None = None,
    user_message: str | None = None,
) -> list[dict]:
    """Parse + derive common kwargs from `context`, then build the Q&A
    event when `qna_flow` is in `workflow_codes`. Empty list = no emit.
    """
    facts_list = parse_qna_facts(workflow_codes, qna_topics, user_message)
    if not facts_list:
        return []
    common_kwargs = build_common_event_context(context)
    return [build_qna_event(facts, **common_kwargs) for facts in facts_list]
