"""create_voice_thinker_tool — new thinker tool factory using VoiceCallbacks.

Replaces ``realtime.py:create_thinker_tool()`` for the new voice path.
The key differences:

- Uses ``VoiceCallbacks`` instead of ``ctx._session_handler`` back-channel
- Uses ``ResponseGate.turn_id`` for stale result detection
- Filler handling strategies are dramatically simpler (cancel_filler callback
  encapsulates the interrupt + expected-cancel-flag coordination)
- Reads config from ``VoiceConfig`` instead of ``settings`` directly

The original ``create_thinker_tool()`` in realtime.py is untouched —
twilio_handler.py continues to use it when the feature flag is off.
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any

import structlog
from agents import (
    InputGuardrailTripwireTriggered,
    ItemHelpers,
    OutputGuardrailTripwireTriggered,
    RunConfig,
    RunContextWrapper,
    function_tool,
)
from langsmith import trace

from agent_leasing.agent.resident_one_agent.agent import ResidentAgent
from agent_leasing.agent.util import SessionScope
from agent_leasing.services.agent_service import (
    cleanup_orphan_after_guardrail_trip,
    ensure_conversation_id,
    run_agent_with_orphan_recovery,
)
from agent_leasing.services.analytics_service import add_metadata_into_context
from agent_leasing.settings import settings
from agent_leasing.voice.callbacks import VoiceCallbacks
from agent_leasing.voice.config import VoiceConfig
from agent_leasing.voice.coordination.call_state import VoiceCallState

logger = structlog.getLogger()

THINKER_TOOL_NAME = "resident_thinker_tool"
THINKER_TOOL_DESCRIPTION = (
    "Delegate all tasks to this thinker agent tool. "
    "The thinker agent can access property information, billing, service requests, "
    "community events, packages, and more. Always provide a detailed description "
    "of the user's request and relevant conversation context."
)

# Internal sentinel returned when the tool wants the realtime model to stay
# silent (e.g. stale-after-interrupt, concurrent-invocation). Not English
# speech — `gpt-realtime-2` is more literal about reading tool-result strings
# aloud than `gpt-realtime-1.5` was, so embedding "do not speak" instructions
# inside the tool result no longer works reliably. The realtime model is
# taught (in VOICE_RESPONDER.md) to recognize this token and say nothing.
# See issue #1642.
THINKER_NO_OUTPUT = "<thinker:no_output/>"

# Prefix for successful thinker results. The realtime model is taught
# (in VOICE_RESPONDER.md) to suppress any "still working on it" carry-over
# when it sees this prefix and deliver only the voice transcript that follows.
# Mirrors the THINKER_NO_OUTPUT approach from issue #1642.
# See issue #1641.
THINKER_RESULT_PREFIX = "<thinker:result/>"


def _build_voice_thinker_run_record(
    *,
    new_items: list[dict[str, Any]] | None = None,
    mcp_tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a serializable record for one voice thinker invocation."""
    return {
        "new_items": new_items or [],
        "mcp_tool_calls": mcp_tool_calls or [],
    }


def _serialize_run_new_items(new_items: list[Any]) -> list[dict[str, Any]]:
    """Serialize RunResult.new_items into plain dicts for test assertions."""
    serialized: list[dict[str, Any]] = []
    for item in new_items:
        if not hasattr(item, "to_input_item"):
            continue
        input_item = item.to_input_item()
        if isinstance(input_item, dict):
            serialized.append(input_item)
    return serialized


def create_voice_thinker_tool(
    context: SessionScope,
    thinker_agent: ResidentAgent,
    callbacks: VoiceCallbacks,
    call_state: VoiceCallState,
    config: VoiceConfig,
):
    """Create the thinker function tool for the voice responder agent.

    This is the new voice path equivalent of ``realtime.py:create_thinker_tool()``.
    The closure captures ``callbacks`` and ``call_state`` instead of reaching
    through ``ctx._session_handler``.

    Args:
        context: The session context.
        thinker_agent: A pre-initialized ResidentAgent (already entered via __aenter__).
        callbacks: The handler's VoiceCallbacks implementation.
        call_state: The call state machine for preamble detection.
        config: Voice configuration.
    """

    @function_tool(
        name_override=THINKER_TOOL_NAME,
        description_override=THINKER_TOOL_DESCRIPTION,
    )
    async def resident_thinker_tool(
        run_context: RunContextWrapper[SessionScope],
        input: Annotated[
            str,
            (
                "A detailed description of the user's request, "
                "including a detailed summary of the relevant parts of the conversation"
            ),
        ],
    ) -> str:
        """Run the ResidentAgent as a thinker tool."""
        thinker_run_record = _build_voice_thinker_run_record() if context.track_voice_thinker_runs else None

        # Concurrency guard
        if config.thinker_concurrency_guard_enabled and context.thinker_running:
            # Concurrency guard hit — designed behavior, not an application error.
            logger.info("Thinker already running, skipping concurrent invocation")
            if thinker_run_record is not None:
                context.voice_thinker_runs.append(thinker_run_record)
            return THINKER_NO_OUTPUT

        context.thinker_running = True
        snapshot_turn_id = callbacks.turn_id
        mcp_tool_calls_start = len(context.mcp_tool_calls) if thinker_run_record is not None else 0

        async with trace(name=THINKER_TOOL_NAME, run_type="tool") as run:
            try:
                logger.info(f"Thinker called: {input[:100]}...")

                # Preamble detection — wait for responder to start speaking before processing
                if config.preamble_speech_detection_enabled:
                    started = await call_state.wait_for_agent_speaking_started(timeout=0.25)
                    if not started:
                        logger.warning("Preamble speech never started before thinker")
                        return (
                            f"You must speak a preamble message before calling {THINKER_TOOL_NAME}. "
                            "Say something like 'Let me check on that' first, then call the tool again."
                        )

                # Reschedule filler timer to prevent fillers during processing
                await callbacks.schedule_filler()
                await callbacks.on_thinker_started()

                # Build input items
                thinker = thinker_agent.agent()
                # Preserve original_input before appending the transcript suffix so
                # add_metadata_into_context keys the metadata under the same string that
                # realtime_util extracts from RealtimeToolCallItem.arguments.
                original_input = input
                latest_user_transcript = _extract_latest_user_transcript(run_context.context.history)
                if latest_user_transcript:
                    input = f'{input}\n\n[Latest user transcript: "{latest_user_transcript}"]'

                logger.debug(f"Thinker input: {input}")
                input_items: list[dict[str, Any]] = [{"role": "user", "content": input}]
                run.add_inputs({"input": input_items})

                previous_response_id = run_context.context.previous_response_id
                if run_context.context.history and previous_response_id is None:
                    input_items = run_context.context.history + input_items

                # Run the thinker
                thinker_trace_metadata = _build_trace_metadata(run_context.context)
                thinker_run_config = RunConfig(
                    group_id=getattr(run_context.context, "openai_group_id", None),
                    workflow_name="Resident One Voice Thinker",
                    trace_metadata=thinker_trace_metadata,
                )

                conversation_id = await ensure_conversation_id(run_context.context)
                if conversation_id:
                    structlog.contextvars.bind_contextvars(openai_conversation_id=conversation_id)

                output = await run_agent_with_orphan_recovery(
                    thinker,
                    context=run_context.context,
                    conversation_id=conversation_id,
                    input=input_items,
                    previous_response_id=previous_response_id,
                    run_config=thinker_run_config,
                    max_turns=settings.resident_one_max_turns,
                )

                if thinker_run_record is not None:
                    thinker_run_record = _build_voice_thinker_run_record(
                        new_items=_serialize_run_new_items(output.new_items),
                        mcp_tool_calls=context.mcp_tool_calls[mcp_tool_calls_start:],
                    )

                add_metadata_into_context(context, output, original_input)
                run_context.context.previous_response_id = output.last_response_id

                response = ItemHelpers.text_message_outputs(output.new_items)
                logger.info(f"Thinker response: {response[:100]}...")
                run.add_outputs({"message": response})

                # Extract response field from structured JSON if present
                try:
                    parsed = json.loads(response)
                    if isinstance(parsed, dict) and "response" in parsed:
                        response = parsed["response"]
                except (json.JSONDecodeError, TypeError):
                    pass

                # Check for staleness before handling filler and responding.
                # If stale, cancel any active response first — returning a string
                # from the tool still triggers response.create, which will collide
                # with the barge-in response if we don't clear it.
                if callbacks.turn_id != snapshot_turn_id:
                    logger.info("Thinker result stale (user interrupted during processing)")
                    await callbacks.cancel_filler()
                    return THINKER_NO_OUTPUT

                # Handle active filler before delivering response
                await _handle_filler_before_response(callbacks, call_state, config)

                return f"{THINKER_RESULT_PREFIX}**CRITICAL:** Communicate this VERBATIM to the user. Do NOT repeat any prior filler or 'still working' phrase — speak ONLY this voice transcript: {response}"

            except (InputGuardrailTripwireTriggered, OutputGuardrailTripwireTriggered) as exc:
                # Issue #1569 Layer 1 — see /ask handler.
                await cleanup_orphan_after_guardrail_trip(
                    exc, run_context.context.openai_conversation_id, site="voice_thinker"
                )

                guardrail_result = exc.guardrail_result
                guardrail_name = guardrail_result.guardrail.get_name()
                output_info = getattr(guardrail_result.output, "output_info", None)
                reasoning = getattr(output_info, "reasoning", None) if output_info else None
                logger.info(f"Thinker guardrail triggered: {guardrail_name}", reasoning=reasoning)

                await _handle_filler_before_response(callbacks, call_state, config)

                safe_response = getattr(output_info, "safe_response", None) if output_info else None
                fallback = "I'm not able to help with that request. Is there something else I can assist you with?"
                result = safe_response or fallback
                run.add_outputs({"message": result})
                return result

            except Exception as e:
                logger.error(f"Thinker error: {e}", exc_info=True)
                await _handle_filler_before_response(callbacks, call_state, config)
                run.add_outputs({"message": "I encountered an issue processing your request."})
                return "I encountered an issue processing your request. Please try again or ask differently."

            finally:
                if thinker_run_record is not None:
                    if not thinker_run_record["mcp_tool_calls"]:
                        thinker_run_record["mcp_tool_calls"] = context.mcp_tool_calls[mcp_tool_calls_start:]
                    context.voice_thinker_runs.append(thinker_run_record)
                context.thinker_running = False
                context.thinker_finished_at = time.monotonic()
                await callbacks.on_thinker_completed()

    return resident_thinker_tool


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _handle_filler_before_response(
    callbacks: VoiceCallbacks,
    call_state: VoiceCallState,
    config: VoiceConfig,
) -> None:
    """Handle active filler before delivering the thinker response.

    Only interrupt when filler audio is actually playing — issuing a session
    interrupt against a quiet session causes gpt-realtime-2 to regenerate the
    prior assistant audio (issue #1641 duplicate playback).
    """
    # Reschedule to prevent new fillers during handling
    await callbacks.schedule_filler()

    strategy = config.filler_handling_strategy
    logger.debug(f"Handling filler before response (strategy={strategy})")

    if strategy == "wait":
        await call_state.wait_for_agent_speaking_stopped(timeout=5.0)
    elif strategy == "hybrid":
        await call_state.wait_for_agent_speaking_stopped(timeout=config.filler_wait_timeout_seconds)
    # "cancel" strategy: no wait, immediate cancel

    if call_state.is_agent_speaking or call_state.is_filler_playing:
        await callbacks.cancel_filler()

    # Reschedule again to cover the window before the thinker response plays
    await callbacks.schedule_filler()


def _extract_latest_user_transcript(history: list[Any]) -> str | None:
    """Find the latest user message transcript from conversation history."""
    if not history:
        return None
    for item in reversed(history):
        if item.get("role") == "user" and item.get("content"):
            return item["content"]
    return None


def _build_trace_metadata(ctx: SessionScope) -> dict[str, str]:
    """Build metadata dict for the thinker's OpenAI trace."""
    raw = {
        "product": getattr(ctx.ask_request, "product", None),
        "property-id": getattr(ctx.ask_request, "property_id", None),
        "property-name": getattr(ctx.ask_request.product_info, "property_name", None),
        "openai-group-url": getattr(ctx, "openai_group_url", None),
    }
    return {k: str(v) for k, v in raw.items() if v is not None}
