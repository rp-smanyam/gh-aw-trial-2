#!/usr/bin/env python3
"""Extractor library for the resident TaskActivityEvent backfill.

Imported by `publish_backfill_events.py` (both `--source langsmith` and
`--source db_s3` paths use the same extractor functions). The public
surface is `fetch_thread_runs`, `resolve_thread_ctx`, `parse_responder_output`,
`rewrite_task_id`, `fill_property_timezone`, the `replay_*` dispatch
functions, and the `HANDOFF_TOOLS` / `THINKER_TOOL` / `CALL_TOOL` /
`FACILITIES_THINKER_TOOL` / `PREFETCH_CHAIN_NAME` constants.

Every event carries a backfill `task.id` derived as
`uuid5(NS, "<channel>:<thread_id>")` — channel partitions match
live's `kafka/task_id.build_task_id`, so SMS and EMAIL turns sharing
one LangSmith thread (Redis cache is channel-blind on `chat_session_id`)
still get distinct task.ids. See README for design notes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid5

from langsmith import Client

sys.path.insert(0, str(Path(__file__).parent))
from property_lookup import PropertyTimezoneLookup  # noqa: E402

HANDOFF_STAFF_TOOLS = {"transfer_to_staff_voice", "transfer_to_staff_text"}
HANDOFF_ESR_TOOLS = {
    "emergency_service_transfer_basic",
    "emergency_service_transfer_advanced",
    "emergency_service_transfer_rpcc",
}
HANDOFF_TOOLS = HANDOFF_STAFF_TOOLS | HANDOFF_ESR_TOOLS

THINKER_TOOL = "resident_thinker_tool"
CALL_TOOL = "call_tool"
FACILITIES_THINKER_TOOL = "call_facilities_thinker_via_api"

# Live's CachingMCPServer invokes tools under this chain with
# skip_post_processors=True so the activity emitter never fires. The flag
# itself isn't serialized into trace inputs, so we detect by ancestor name.
PREFETCH_CHAIN_NAME = "prefetch_property_overview_and_insights"


def fetch_thread_runs(client: Client, project: str, thread_id: str, run_type: str | None = None) -> list[Any]:
    fql = f"and(eq(metadata_key, 'thread_id'), eq(metadata_value, '{thread_id}'))"
    kwargs = {"project_name": project, "filter": fql, "order_by": ["start_time"]}
    if run_type is not None:
        kwargs["run_type"] = run_type
    return list(client.list_runs(**kwargs))


def parse_responder_output(outputs: dict | None) -> dict | None:
    """Decode a ChatOpenAI run's structured ResidentResponderOutput, or None.

    Returns the parsed dict only when it has both `workflow_codes` and
    `response`, so intermediate llm calls (tool-call decisions, guardrail
    eval) are filtered out.
    """
    if not isinstance(outputs, dict):
        return None
    out_arr = outputs.get("output")
    if not isinstance(out_arr, list):
        return None
    for item in out_arr:
        if not isinstance(item, dict):
            continue
        for c0 in item.get("content") or []:
            if not isinstance(c0, dict) or c0.get("type") != "output_text":
                continue
            try:
                parsed = json.loads(c0.get("text") or "")
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and "workflow_codes" in parsed and "response" in parsed:
                return parsed
    return None


def reconstruct_session_scope(ctx_context: dict) -> Any:
    from agent_leasing.models.context import SessionScope

    cleaned = dict(ctx_context)
    for opaque_key in ("call_state_manager", "pending_activity_publishes", "mcp_tool_calls"):
        cleaned.pop(opaque_key, None)
    # Older traces serialized these as bool/int; current SessionScope declares
    # them dict. Extractors don't read them, so dropping the legacy-typed
    # values lets old traces round-trip into the current model.
    for legacy_typed_key in ("identity_verified", "identity_verified_with_birth_year", "verification_attempts"):
        if not isinstance(cleaned.get(legacy_typed_key), dict):
            cleaned.pop(legacy_typed_key, None)
    # uc_lease_id became required after historic traces were captured; backfill
    # a sentinel so old ctx round-trips without failing persona validation.
    SYNTH = {"id": "synthesized", "source": "synthesized"}
    ask_request = cleaned.get("ask_request") or {}
    product_info = ask_request.get("product_info") or {}
    if isinstance(product_info, dict) and "uc_lease_id" not in product_info:
        product_info = {**product_info, "uc_lease_id": SYNTH}
        ask_request = {**ask_request, "product_info": product_info}
        cleaned = {**cleaned, "ask_request": ask_request}
    return SessionScope.model_validate(cleaned)


VALID_CHANNELS = ("VOICE", "CHAT", "SMS", "EMAIL")


def derive_run_channel(run: Any, fallback_ctx: dict | None) -> str:
    """Resolve the channel for one LangSmith run.

    Resolution order (most-specific first):
      1. `run.inputs.ctx.context.ask_request.product` (tool runs with ctx)
      2. `run.extra.metadata.product` (LLM and ctxless runs)
      3. `fallback_ctx['ask_request']['product']` (last resort)

    Returns one of VOICE/CHAT/SMS/EMAIL. Raises ValueError if no source
    yields a recognized channel — silent fallthrough would re-introduce
    the channel-collapse bug.
    """
    from agent_leasing.agent.util import get_channel_from_product

    inputs = getattr(run, "inputs", None) or {}
    ctx_product = (((inputs.get("ctx") or {}).get("context") or {}).get("ask_request") or {}).get("product")
    md_product = ((getattr(run, "extra", None) or {}).get("metadata") or {}).get("product")
    fb_product = ((fallback_ctx or {}).get("ask_request") or {}).get("product")
    for candidate in (ctx_product, md_product, fb_product):
        # get_channel_from_product defaults to CHAT for unrecognized products;
        # only accept when a known channel token is present so unknown products
        # don't silently collapse to CHAT and re-introduce the channel bug.
        if candidate and any(tok in candidate.upper() for tok in VALID_CHANNELS):
            return get_channel_from_product(candidate)
    raise ValueError(
        f"derive_run_channel: no product found on run "
        f"(ctx={ctx_product!r}, metadata={md_product!r}, fallback={fb_product!r})"
    )


def compute_backfill_task_id(langsmith_thread_id: str, channel: str) -> str:
    """Backfill task.id formula: one task per (channel, LangSmith thread).

    Channel rides in the hash input to match live's
    `build_task_id(channel, conversation_key)` — SMS and EMAIL turns
    on the same thread (Redis cache is channel-blind on `chat_session_id`)
    get distinct task.ids, mirroring live emission.

    Diverges from live's `derive_conversation_key` body because
    `session_marker` isn't serialized into trace inputs; the backfill
    window closed before live publishing began so the two surfaces
    never collide on the same conversation.
    """
    from agent_leasing.kafka.task_id import AGENT_LEASING_TASK_NAMESPACE

    if channel not in VALID_CHANNELS:
        raise ValueError(f"compute_backfill_task_id: channel must be one of {VALID_CHANNELS}, got {channel!r}")
    return str(uuid5(AGENT_LEASING_TASK_NAMESPACE, f"{channel}:{langsmith_thread_id}"))


def get_langsmith_thread_id(run: Any) -> str | None:
    extra = run.extra or {}
    return (extra.get("metadata") or {}).get("thread_id")


def synthesize_ctx_from_metadata(run: Any, thread_id: str) -> dict:
    """Minimal ctx_context from `extra.metadata`, used when no run carries ctx.

    Loses first_name / last_name / unit / property_timezone. Events are
    valid for downstream tenant filtering but degrade brief UX.
    """
    md = (run.extra or {}).get("metadata") or {}
    # AskRequest's persona validator requires several UCReference fields;
    # extractors never read them, so sentinel values clear validation.
    SYNTH = {"id": "synthesized", "source": "synthesized"}
    # Live voice handlers leave SessionScope.thread_id=None, so no
    # extra.thread_id appears in voice events. We omit it here too.
    return {
        "ask_request": {
            "product": md.get("product") or "resident_one_chat",
            "chat_session_id": md.get("chat_session_id") or "",
            "product_info": {
                "knock_property_id": md.get("property_id") or "synthesized",
                "knock_resident_id": md.get("resident_id"),
                "property_name": md.get("property_name"),
                "pmc_id": md.get("pmc_id"),
                "pmc_name": md.get("pmc_name"),
                "call_sid": md.get("call_sid"),
                "uc_company_id": SYNTH,
                "uc_property_id": SYNTH,
                "uc_resident_household_id": SYNTH,
                "uc_resident_member_id": SYNTH,
                "ab_resident_id": SYNTH,
                "uc_lease_id": SYNTH,
                "uc_portal_base_url": "synthesized",
            },
        },
    }


def resolve_thread_ctx(runs: list[Any], thread_id: str) -> tuple[dict | None, str]:
    """Pick a single ctx_context to seed every replay in this thread.

    - **borrowed**: first run with `inputs.ctx.context` populated. Resident/
      property attributes don't change within a thread, so reusable.
    - **synthesized**: no run has ctx, build from `extra.metadata`.
    - **no-interaction**: no run has even property_id; voice short-circuit
      trace where the agent never engaged. Caller skips the thread.
    """
    for r in runs:
        ctx = (r.inputs or {}).get("ctx", {}).get("context") if r.inputs else None
        if ctx:
            return ctx, "borrowed"
    if not runs:
        return None, "missing"
    if not any((r.extra or {}).get("metadata", {}).get("property_id") for r in runs):
        return None, "no-interaction"
    return synthesize_ctx_from_metadata(runs[0], thread_id), "synthesized"


def rewrite_task_id(events: list[dict], langsmith_thread_id: str, channel: str) -> list[dict]:
    """Stamp every event with the channel-aware backfill task.id and
    overwrite `extra.channel` so events whose extractor used the thread's
    fallback ctx (LLM runs without inputs.ctx, etc.) carry their own
    run's channel rather than the borrowed/synthesized ctx's.
    """
    backfill_id = compute_backfill_task_id(langsmith_thread_id, channel)
    for event in events:
        event["task"]["id"] = backfill_id
        event.setdefault("extra", {})["channel"] = channel
    return events


def fill_property_timezone(events: list[dict], lookup: PropertyTimezoneLookup) -> None:
    """No-op when extra.property_timezone already set (preserves live values)."""
    for event in events:
        extras = event.setdefault("extra", {})
        if extras.get("property_timezone"):
            continue
        property_id = next(
            (r.get("id") for r in event.get("references", []) if r.get("type") == "PROPERTY"),
            None,
        )
        tz = lookup.get(property_id)
        if tz:
            extras["property_timezone"] = tz


def replay_handoff(tool_name: str, run_inputs: dict, fallback_ctx: dict | None = None) -> list[dict]:
    """Reproduce a live handoff extractor call.

    Covers `transfer_to_staff_{voice,text}` and the ESR family. Argument
    shape differs across them (voice→`summary`, text→`transfer_message`,
    ESR→`service_request_summary`) but they all converge on
    `extract_handoff_events(transfer_message, context, reason, ...)`.
    """
    from agent_leasing.api.model import HandoffReasonCode, HandoffTopic
    from agent_leasing.kafka.task_activity.extractors import extract_handoff_events

    ctx_context = run_inputs.get("ctx", {}).get("context") or fallback_ctx
    if not ctx_context:
        return []
    session_scope = reconstruct_session_scope(ctx_context)

    if tool_name in HANDOFF_ESR_TOOLS:
        transfer_message = run_inputs.get("service_request_summary") or ""
        return extract_handoff_events(
            transfer_message,
            context=session_scope,
            reason=HandoffReasonCode.EMERGENCY,
        )

    summary = run_inputs.get("transfer_message") or run_inputs.get("summary")
    # Fallback string mirrors transfer_to_staff_voice when caller refuses summary.
    transfer_message = summary or "Resident requested transfer to staff and refused to provide a reason"

    reason_raw = run_inputs.get("reason")
    reason = HandoffReasonCode(reason_raw) if reason_raw else HandoffReasonCode.RESIDENT_REQUESTED

    topic_raw = run_inputs.get("handoff_topic")
    topic = HandoffTopic(topic_raw) if topic_raw else None

    return extract_handoff_events(
        transfer_message,
        context=session_scope,
        reason=reason,
        topic=topic,
    )


def replay_thinker_qna(run_inputs: dict, run_outputs: dict | None, fallback_ctx: dict | None = None) -> list[dict]:
    """Voice qna replay. Voice never emits frustrated_user.

    The thinker doesn't serialize its RunContextWrapper arg, so always
    relies on `fallback_ctx`.
    """
    from agent_leasing.kafka.task_activity.extractors import extract_qna_events

    ctx_context = run_inputs.get("ctx", {}).get("context") or fallback_ctx
    if not ctx_context:
        return []

    raw_message = (run_outputs or {}).get("message")
    if not raw_message:
        return []
    try:
        parsed = json.loads(raw_message) if isinstance(raw_message, str) else raw_message
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    workflow_codes = parsed.get("workflow_codes") or []
    qna_topics = parsed.get("qna_topics") or []

    raw_input = run_inputs.get("input")
    if isinstance(raw_input, list) and raw_input:
        first = raw_input[0]
        user_message = first.get("content") if isinstance(first, dict) else None
    elif isinstance(raw_input, str):
        user_message = raw_input
    else:
        user_message = None

    session_scope = reconstruct_session_scope(ctx_context)
    return extract_qna_events(
        workflow_codes,
        context=session_scope,
        qna_topics=qna_topics,
        user_message=user_message,
    )


def replay_mcp_business_tool(run_inputs: dict, run_outputs: dict | None, fallback_ctx: dict | None) -> list[dict]:
    """Replay an MCP business tool call_tool span via MCP_EXTRACTORS dispatch."""
    from agent_leasing.kafka.task_activity.extractors import MCP_EXTRACTORS

    tool_name = run_inputs.get("tool_name")
    extractor = MCP_EXTRACTORS.get(tool_name)
    if extractor is None:
        return []

    if not fallback_ctx:
        return []

    if not run_outputs or run_outputs.get("isError"):
        return []
    content = run_outputs.get("content") or []
    if not content:
        return []
    try:
        tool_output = json.loads(content[0].get("text") or "")
    except (json.JSONDecodeError, AttributeError, IndexError):
        return []
    if not isinstance(tool_output, dict):
        return []

    session_scope = reconstruct_session_scope(fallback_ctx)
    arguments = run_inputs.get("arguments") or {}
    return extractor(tool_output, context=session_scope, mcp_arguments=arguments)


def replay_facilities_thinker(run_inputs: dict, run_outputs: dict | None, fallback_ctx: dict | None) -> list[dict]:
    from agent_leasing.kafka.task_activity.extractors import extract_facilities_thinker_events

    ctx_context = run_inputs.get("ctx", {}).get("context") or fallback_ctx
    if not ctx_context:
        return []
    response = None
    if isinstance(run_outputs, dict):
        if isinstance(run_outputs.get("output"), dict):
            response = run_outputs["output"]
        elif "action_taken" in run_outputs:
            response = run_outputs
    if not isinstance(response, dict):
        return []
    user_request = run_inputs.get("message")
    session_scope = reconstruct_session_scope(ctx_context)
    return extract_facilities_thinker_events(response, context=session_scope, user_request=user_request)


def replay_responder_output(parsed_output: dict, user_message: str | None, fallback_ctx: dict | None) -> list[dict]:
    """Non-voice qna + frustrated_user from a single parsed ResidentResponderOutput.

    Voice publishes qna via the thinker tool path, so callers must skip
    voice traces (filtered upstream by `trace_id IN voice_trace_ids`).
    """
    from agent_leasing.kafka.task_activity.extractors import (
        extract_frustrated_user_events,
        extract_qna_events,
    )

    if not fallback_ctx:
        return []
    session_scope = reconstruct_session_scope(fallback_ctx)
    workflow_codes = parsed_output.get("workflow_codes") or []
    qna_topics = parsed_output.get("qna_topics") or []
    user_frustrated = bool(parsed_output.get("user_frustrated"))

    events = list(
        extract_qna_events(
            workflow_codes,
            context=session_scope,
            qna_topics=qna_topics,
            user_message=user_message,
        )
    )
    events.extend(
        extract_frustrated_user_events(
            user_frustrated,
            context=session_scope,
            user_message=user_message,
        )
    )
    return events
