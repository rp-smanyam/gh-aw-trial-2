"""Payload builders for task-event Kafka messages.

Two publish points in a conversation's lifecycle:
  - Session start: IN_PROGRESS (no escalation).
  - Session end: one of
      * COMPLETED, no escalation            — conversation ended, no handoff attempted
      * COMPLETED, with escalation          — handoff attempted AND routing confirmed
      * PENDING,   with escalation          — handoff attempted but routing NOT confirmed
    Branch is driven by ``ctx.handoff_result`` + ``handoff_result.routing_confirmed``.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from agent_leasing.kafka.references import build_activity_references
from agent_leasing.kafka.task_id import build_task_id, derive_conversation_key
from agent_leasing.models.context import HandoffResult, SessionScope

TASK_STATUS_IN_PROGRESS = "IN_PROGRESS"
TASK_STATUS_PENDING = "PENDING"
TASK_STATUS_COMPLETED = "COMPLETED"

# Escalation reason symbols used by this service. PLACEHOLDER: RESIDENT_REQUESTED
# is per PM spec but not confirmed present in the Schema Registry EscalationReason
# enum (follow-up F1); may need to switch to PROSPECT_REQUESTED_DURING.
ESCALATION_REASON_RESIDENT_REQUESTED = "RESIDENT_REQUESTED"
ESCALATION_REASON_EMERGENCY = "EMERGENCY"

_TASK_NAME = "Resident Conversations"
_TASK_CODE = "RESIDENT_CONVERSATION"
_SOURCE_KNCK = "KNCK"
_PUBLISHER = "agent-leasing"
_ORIGINATING_SOURCE = "RESIDENT_AI"


def _channel(ctx: SessionScope) -> str:
    if ctx.ask_request is None:
        return "UNKNOWN"
    return ctx.ask_request.conversation_type.value.upper()


def _domain(ctx: SessionScope) -> str:
    persona = ctx.persona.value.upper() if ctx.persona else "UNKNOWN"
    return persona if persona in {"RESIDENT", "PROSPECT", "APPLICANT"} else "UNKNOWN"


def _build_extra(ctx: SessionScope) -> dict[str, str]:
    extra: dict[str, str] = {
        "originating_source": _ORIGINATING_SOURCE,
        "channel": _channel(ctx),
    }

    if ctx.ask_request:
        if ctx.ask_request.chat_session_id:
            extra["session_id"] = ctx.ask_request.chat_session_id
        product_info = ctx.ask_request.product_info
        if product_info and getattr(product_info, "call_sid", None):
            extra["call_sid"] = str(product_info.call_sid)
    return extra


def _build_references(ctx: SessionScope) -> list[dict[str, Any]]:
    product_info = ctx.ask_request.product_info if ctx.ask_request else None
    knock_company_id = getattr(product_info, "knock_company_id", None) if product_info else None
    knock_property_id = getattr(product_info, "knock_property_id", None) if product_info else None
    knock_resident_id = getattr(product_info, "knock_resident_id", None) if product_info else None
    return build_activity_references(
        knock_company_id=knock_company_id, knock_property_id=knock_property_id, knock_resident_id=knock_resident_id
    )


def _build_task(
    ctx: SessionScope,
    status: str,
    escalation: dict[str, Any] | None,
) -> dict[str, Any]:
    product_info = ctx.ask_request.product_info if ctx.ask_request else None
    knock_company_id = getattr(product_info, "knock_company_id", None) if product_info else None
    knock_property_id = getattr(product_info, "knock_property_id", None) if product_info else None

    channel, conversation_key = derive_conversation_key(ctx)
    return {
        "id": build_task_id(channel, conversation_key),
        "name": _TASK_NAME,
        "description": f"{channel.title()} conversation",
        "company_id": str(knock_company_id) if knock_company_id else None,
        "property_id": str(knock_property_id) if knock_property_id else None,
        "source": _SOURCE_KNCK,
        "code": _TASK_CODE,
        "status": status,
        "owner_type": "AI",
        "due_at": None,
        "escalation_cutoff_at": None,
        "domain": _domain(ctx),
        "parent_id": None,
        "staff_review": None,
        "escalation": escalation,
        "references": _build_references(ctx),
        "extra": _build_extra(ctx),
        "publisher": _PUBLISHER,
    }


def _envelope(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": str(uuid4()),
        "event_timestamp": int(time.time() * 1000),
        "task": task,
    }


def _escalation_from(result: HandoffResult) -> dict[str, Any]:
    return {"reason": result.reason, "summary": result.summary}


def build_in_progress_event(ctx: SessionScope) -> dict[str, Any]:
    """Session-start event. Always IN_PROGRESS, no escalation."""
    return _envelope(_build_task(ctx, TASK_STATUS_IN_PROGRESS, escalation=None))


def build_end_of_session_event(ctx: SessionScope) -> dict[str, Any]:
    """Voice session-end event, branched on handoff_result + routing_confirmed.

    No handoff           → COMPLETED, no escalation.
    Handoff + confirmed  → COMPLETED, with escalation.
    Handoff + unconfirmed → PENDING, with escalation.

    Voice-only because non-voice never emits COMPLETED — that status is
    produced downstream by the CRM team's task completion or the GenAI
    Service's nightly inactivity sweep. Use ``build_pending_handoff_event``
    for non-voice handoffs.
    """
    result = ctx.handoff_result
    if result is None:
        return _envelope(_build_task(ctx, TASK_STATUS_COMPLETED, escalation=None))
    escalation = _escalation_from(result)
    if result.routing_confirmed:
        return _envelope(_build_task(ctx, TASK_STATUS_COMPLETED, escalation=escalation))
    return _envelope(_build_task(ctx, TASK_STATUS_PENDING, escalation=escalation))


def build_pending_handoff_event(ctx: SessionScope) -> dict[str, Any]:
    """Non-voice handoff event. Always PENDING + escalation.

    Per spec, every non-voice handoff (regardless of internal routing
    success) emits PENDING because the conversation completion happens
    downstream — CRM closes the task or the GenAI Service's nightly job
    flips it to COMPLETED.

    Reads ``ctx.handoff_message`` for the escalation summary. The caller
    MUST verify ``ctx.handoff`` is True (a handoff was triggered this
    turn) before calling.

    NOTE: ``reason`` is hardcoded to ``RESIDENT_REQUESTED`` because text
    handoffs only flow through ``transfer_to_staff_text`` today, which is
    always a "user asked for staff" scenario. When more handoff entry
    points appear (out-of-scope, missing-data, etc.) the reason should
    come from a structured field on SessionScope (see follow-up F9).
    """
    escalation = {
        "reason": ESCALATION_REASON_RESIDENT_REQUESTED,
        "summary": ctx.handoff_message,
    }
    return _envelope(_build_task(ctx, TASK_STATUS_PENDING, escalation=escalation))


__all__ = [
    "ESCALATION_REASON_EMERGENCY",
    "ESCALATION_REASON_RESIDENT_REQUESTED",
    "TASK_STATUS_COMPLETED",
    "TASK_STATUS_IN_PROGRESS",
    "TASK_STATUS_PENDING",
    "build_end_of_session_event",
    "build_in_progress_event",
    "build_pending_handoff_event",
]
