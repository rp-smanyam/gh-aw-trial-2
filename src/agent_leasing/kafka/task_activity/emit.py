"""TaskActivityEvent publishing bridge.

Two surfaces:

- `task_activity_post_processor(tool_name)` — factory for the MCP
  post-processor protocol. Looks up the extractor in `MCP_EXTRACTORS`
  and returns a closure the MCP harness can call.
- `publish_task_activity(extractor, tool_output, context, **caller_kwargs)` —
  generic publish helper for every other caller. The caller picks the
  extractor (direct import for local tools, meta-extractor for
  content-keyed surfaces like the facilities thinker).

Activity-specific logic — context derivations, caller-arg name mapping
(e.g., `chat_summary` vs `message`) — lives in the extractor module.
"""

from __future__ import annotations

from collections.abc import Callable

import structlog
from mcp.types import CallToolResult

from agent_leasing.agent.tools.mcp_post_processors import parse_tool_result_json
from agent_leasing.kafka.kafka_context import kafka_application_context
from agent_leasing.kafka.task_activity.extractors import MCP_EXTRACTORS
from agent_leasing.kafka.task_activity.publish import publish_task_activity_fire_and_forget
from agent_leasing.models.context import SessionScope
from agent_leasing.settings import settings

logger = structlog.getLogger(__name__)


# TODO: rename to make the factory shape obvious in the call site —
# e.g. `make_task_activity_post_processor` or `task_activity_post_processor_factory`.
# Sibling factories (`voice_sms_consent_confirmed_post_processor`,
# `voice_normalize_post_processor` in `mcp_post_processors.py`) use the
# same misleading no-suffix convention; rename all three together as a
# separate codebase-wide cleanup.
def task_activity_post_processor(tool_name: str) -> Callable[..., CallToolResult]:
    """Factory: returns an MCP post-processor that emits a `TaskActivityEvent`
    for the named tool after a successful call. Errors and parse failures
    log and drop — the post-processor never modifies `result`.
    """
    extractor = MCP_EXTRACTORS.get(tool_name)
    if extractor is None:
        raise ValueError(f"No task-activity extractor registered for MCP tool: {tool_name}")

    def emit_task_activity(result: CallToolResult, **kwargs) -> CallToolResult:
        # Flag-off short-circuit before parsing JSON or walking SessionScope —
        # this runs on every successful tool call once the flag flips on.
        if not settings.task_activity_event_publishing_enabled:
            return result
        tool_output = parse_tool_result_json(result, warn_label=f"task_activity_{tool_name}")
        if tool_output is None:
            return result
        context: SessionScope | None = kwargs.get("context")
        if context is None:
            return result
        publish_task_activity(
            extractor,
            tool_output,
            context,
            mcp_arguments=kwargs.get("arguments") or {},
        )
        return result

    emit_task_activity.__name__ = f"emit_task_activity_for_{tool_name}"
    return emit_task_activity


def publish_task_activity(
    extractor: Callable[..., list[dict]] | None,
    tool_output,
    context: SessionScope,
    *,
    on_success: Callable[[], None] | None = None,
    **caller_kwargs,
) -> None:
    """Run the extractor + fire-and-forget publish each event it returns.

    Returns silently when the publishing flag is off, the extractor is
    None, or the extractor raises.

    `on_success` is invoked from each event's task done-callback once
    delivery is confirmed. Used for delivery-time dedup (FRUSTRATED_USER
    once-per-conversation): pass a callback that flips the dedup flag,
    so a publish failure leaves the flag clear and the next turn retries.
    """
    if not settings.task_activity_event_publishing_enabled:
        return
    if extractor is None:
        return
    try:
        events = extractor(tool_output, context=context, **caller_kwargs)
    except Exception:
        logger.exception(
            "task_activity_extractor_failed",
            extractor=getattr(extractor, "__name__", repr(extractor)),
        )
        return

    producer = kafka_application_context.task_activity_producer
    for event in events:
        publish_task_activity_fire_and_forget(
            producer,
            event,
            context.pending_activity_publishes,
            on_success=on_success,
        )
