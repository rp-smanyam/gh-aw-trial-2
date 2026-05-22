"""Shared SessionScope → TaskActivityEvent context binding.

Returns the common identifier kwargs every extractor needs on every
event (channel, company/property/resident IDs, names,
session/thread/call_sid). Activity-specific fields are added by the
extractor itself.

`task_id` is derived via the shared `derive_conversation_key(ctx)` +
`build_task_id(channel, conversation_key)` pair in `kafka/task_id.py`,
the same surface the sibling task-event topic uses — so both topics
end up with the same `task.id` per conversation and downstream joins
line up.
"""

from __future__ import annotations

import structlog

from agent_leasing.kafka.task_id import build_task_id, derive_conversation_key
from agent_leasing.models.context import SessionScope

logger = structlog.getLogger(__name__)


def build_common_event_context(context: SessionScope) -> dict:
    """Common kwargs every TaskActivityEvent needs — channel, IDs, names,
    session/thread/call_sid.
    """
    channel, conversation_key = derive_conversation_key(context)
    task_id = build_task_id(channel, conversation_key)

    ask_request = context.ask_request
    product_info = ask_request.product_info if ask_request else None

    return {
        "task_id": task_id,
        "channel": channel,
        "knock_company_id": getattr(product_info, "knock_company_id", None),
        "knock_property_id": getattr(product_info, "knock_property_id", None),
        "knock_resident_id": getattr(product_info, "knock_resident_id", None),
        "first_name": getattr(product_info, "uc_first_name", None),
        "last_name": getattr(product_info, "uc_last_name", None),
        "ab_unit_number": getattr(product_info, "ab_unit_number", None),
        "ab_building_number": getattr(product_info, "ab_building_number", None),
        "chat_session_id": getattr(ask_request, "chat_session_id", None),
        "thread_id": context.thread_id,
        "call_sid": getattr(product_info, "call_sid", None),
        "property_name": getattr(product_info, "property_name", None),
        "property_timezone": getattr(product_info, "property_timezone", None),
        "resident_stream_id": getattr(product_info, "thread_id", None),
        "os_company_id": _uc_id(product_info, "uc_company_id"),
        "os_property_id": _uc_id(product_info, "uc_property_id"),
        "resident_household_id": _uc_id(product_info, "uc_resident_household_id"),
        "resident_member_id": _uc_id(product_info, "uc_resident_member_id"),
    }


def _uc_id(product_info, attr: str) -> str | None:
    ref = getattr(product_info, attr, None)
    ref_id = getattr(ref, "id", None) if ref is not None else None
    return str(ref_id) if ref_id not in (None, "") else None
