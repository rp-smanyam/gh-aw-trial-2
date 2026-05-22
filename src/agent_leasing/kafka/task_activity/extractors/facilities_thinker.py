"""Public extractor for the facilities-thinker API response.

The facilities-thinker call site (`call_facilities_thinker_via_api`) is
ONE function but the response can describe several distinct activities
(SR created today; SR-status fetched / self-service / etc. later) keyed
on `action_taken`. So this module IS the extractor for that surface — it
dispatches internally to the matching sub-extractor and returns whatever
events that activity implies.

Adding a new thinker action = register a sub-extractor in
`_ACTION_DISPATCH`; the call site stays a one-liner.
"""

from __future__ import annotations

from collections.abc import Callable

from agent_leasing.kafka.task_activity.extractors.sr_created import (
    ACTION_SR_CREATED,
    extract_sr_created_events,
)
from agent_leasing.models.context import SessionScope

_ACTION_DISPATCH: dict[str, Callable[..., list[dict]]] = {
    ACTION_SR_CREATED: extract_sr_created_events,
}


def extract_facilities_thinker_events(
    response,
    *,
    context: SessionScope,
    user_request: str | None = None,
) -> list[dict]:
    """Dispatch on `response["action_taken"]` to the matching sub-extractor.

    Returns `[]` when the response shape isn't a dict, the action field
    is missing, or no sub-extractor is registered for that action — the
    publisher treats an empty list as "nothing to emit".
    """
    if not isinstance(response, dict):
        return []
    action = response.get("action_taken")
    if not action:
        return []
    sub_extractor = _ACTION_DISPATCH.get(action)
    if sub_extractor is None:
        return []
    return sub_extractor(response, context=context, user_request=user_request)
