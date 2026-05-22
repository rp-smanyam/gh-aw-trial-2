import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Callable, Union

import langsmith as ls
import structlog
from agents import (
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    Runner,
    trace,
)
from asgi_correlation_id import CorrelationIdMiddleware
from dotenv import load_dotenv
from fastapi import (
    Body,
    FastAPI,
    Header,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError
from lumina_common.logging import ASGILoggingMiddleware, setup_logging
from openai import BadRequestError
from starlette.responses import JSONResponse, PlainTextResponse, StreamingResponse

from agent_leasing.agent.guardrails.pii_guardrail.pii_guardrail import detect_pii
from agent_leasing.agent.tools.transfer_to_staff.handoff import (
    is_handoff_active,
    maybe_get_handoff_key,
)
from agent_leasing.agent.tools.transfer_to_staff.transfer_to_staff_text import (
    execute_handoff,
)
from agent_leasing.agent.util import (
    AgentArchitecture,
    ResidentResponderOutput,
    UnsupportedAgentException,
    get_channel_from_context,
    get_channel_from_product,
    log_internal_messages,
)
from agent_leasing.api.auth.auth_helper import get_knock_mcp_auth_token
from agent_leasing.api.model import (
    AskChatPayload,
    AskContent,
    AskRequest,
    AskResponse,
    Flow,
    HandoffReasonCode,
)
from agent_leasing.api.openapi import (
    AGENT_ASK_DESCRIPTION,
    AGENT_STREAM_DESCRIPTION,
    OPENAPI_EXAMPLES,
    OPENAPI_STREAMING_EXAMPLES,
)
from agent_leasing.clients.ldp import fetch_ldp_property_data
from agent_leasing.clients.mcp import CachingMCPServer
from agent_leasing.clients.openai import initialize_openai_client
from agent_leasing.kafka.fire_and_forget import drain_pending_publishes
from agent_leasing.kafka.kafka_context import kafka_application_context
from agent_leasing.kafka.task_activity.emit import publish_task_activity
from agent_leasing.kafka.task_activity.extractors import (
    extract_frustrated_user_events,
    extract_handoff_events,
    extract_qna_events,
)
from agent_leasing.kafka.task_event import (
    build_pending_handoff_event,
    publish_task_event_fire_and_forget,
)
from agent_leasing.models.context import HandoffResult, SessionScope
from agent_leasing.services.agent_service import (
    build_agent_request,
    cleanup_orphan_after_guardrail_trip,
    ensure_conversation_id,
    get_flows,
    is_orphan_function_call_error,
    reset_and_create_fresh_conversation,
    run_agent_with_orphan_recovery,
    save_conversation_id,
    save_previous_response_id,
)
from agent_leasing.services.analytics_service import (
    add_metadata_into_context,
    log_conversation_exchange,
)
from agent_leasing.services.backend_check_service import build_mcp_dependency_status
from agent_leasing.services.input_sanitizers import URL_REPLACEMENT
from agent_leasing.services.telemetry_service import emit_metrics
from agent_leasing.settings import settings
from agent_leasing.twilio_handler import TwilioWebSocketManager
from agent_leasing.util import memory
from agent_leasing.util.language_utils import translate_text
from agent_leasing.util.memory import setup_cache
from agent_leasing.util.otel_configuration import flush_traces, setup_opentelemetry
from agent_leasing.util.sms_consent import handle_sms_consent_gate
from agent_leasing.util.streaming_util import (
    DONE,
    StreamEventProcessor,
    aggregate_streaming_outputs,
    elapsed_ms,
    end,
    error,
    generating,
    handoff,
    start,
)
from agent_leasing.util.tracing_utils import (
    annotate_handoff_bypass,
    build_openai_trace_url,
    extract_langsmith_trace_id,
    get_langsmith_trace_url,
    is_langsmith_enabled,
    log_ai_message_span,
    normalize_metadata_keys,
    process_nonstreaming_outputs,
)
from agent_leasing.util.twilio_util import validate_twilio_request
from agent_leasing.voice.handler import VoiceHandlerManager
from agent_leasing.voice_ui.app.ui import voice_ui_app

PREVIOUS_RESPONSE_ID_HEADER = "X-OpenAI-Previous-Response-Id"
PRODUCT_HEADER = "X-RealPage-Product"
AGENT_HEADER = "X-RealPage-Agent"
FLOWS_HEADER = "X-RealPage-Flows"
LANGUAGE_HEADER = "X-RealPage-Language"
FALLBACK_RESPONSE = "I'm unable to provide a response for that. Could you please adjust your request for me?"
URL_HANDOFF_RESPONSE = "Thanks! I've forwarded this to a staff member for review."
JSON_ATTRIBUTE_TO_EXTRACT = "response"

load_dotenv()

# Logging setup
setup_logging(json_logs=settings.log_json_format, log_level=settings.log_level)

logger = structlog.getLogger()

# OpenAI limits trace metadata to 16 properties. This allowlist controls
# which keys from agent_request.metadata are forwarded to OpenAI traces.
# All keys are still sent to LangSmith for full observability.
OPENAI_TRACE_METADATA_KEYS = frozenset(
    {
        "environment",
        "chat-session-id",
        "property-id",
        "resident-id",
        "company-id",
        "product",
        "agent",
        "thread-id",
        "property-name",
        "input",
        "call-sid",
        "pmc-id",
        "pmc-name",
        "openai-group-url",
        "langsmith-trace-id",
        "openai-conversation-id",
    }
)


# In your FastAPI lifespan or startup event:
async def warmup_pii():
    logger.info("Warming up PII Guardrail NLP cache...")
    # This forces the thread pool to spin up its first thread AND
    # forces spaCy to fully load its memory map into RAM.
    await asyncio.to_thread(detect_pii, "My phone number is 555-555-5555")
    logger.info("PII Guardrail warmup complete.")


def _patch_anyio_deliver_cancellation() -> None:
    """Monkey-patch anyio's _deliver_cancellation to stop after 5 seconds.

    anyio bug https://github.com/agronholm/anyio/issues/695 causes
    _deliver_cancellation to reschedule itself via call_soon on every event
    loop tick, consuming ~65% CPU indefinitely after MCP cancel scope cleanup.

    This patch wraps the method with a time limit. If a cancel scope has been
    retrying _deliver_cancellation for more than 5 seconds, it stops
    rescheduling. Legitimate cancellation completes in milliseconds.

    IMPORTANT: We intentionally do NOT clear self._tasks here. Clearing the
    task set causes KNCK-39169: when orphaned tasks eventually complete, their
    anyio task_done callbacks assert `_task in cancel_scope._tasks` and crash
    with AssertionError. Instead we leave _tasks intact and set a permanent
    _deliver_stopped flag so task_done callbacks can clean up normally and
    _restart_cancellation_in_parent() cannot restart the spin loop.

    Works with any event loop implementation (asyncio, uvloop, etc.) since it
    patches the anyio CancelScope class directly rather than accessing event
    loop internals.
    """
    import time

    from anyio._backends._asyncio import CancelScope

    original = CancelScope._deliver_cancellation

    preserve_tasks = settings.anyio_patch_preserve_tasks_enabled

    def _patched_deliver_cancellation(self, origin):  # type: ignore[no-untyped-def]
        # Permanent stop — block re-entry from _restart_cancellation_in_parent
        if preserve_tasks and getattr(self, "_deliver_stopped", False):
            return False

        now = time.monotonic()
        start = getattr(self, "_deliver_start", None)
        if start is None:
            self._deliver_start = now
        elif now - start > 5.0:
            task_count = len(self._tasks) if self._tasks else 0
            if preserve_tasks:
                self._deliver_stopped = True
            else:
                self._tasks.clear()
            self._cancel_handle = None
            self._deliver_start = None
            logger.debug(
                f"Stopped stuck _deliver_cancellation after {now - start:.1f}s, tasks={task_count} (anyio bug #695)"
            )
            return False

        result = original(self, origin)

        # Reset timer if cancellation completed (no retry scheduled)
        if origin is self and self._cancel_handle is None:
            self._deliver_start = None

        return result

    CancelScope._deliver_cancellation = _patched_deliver_cancellation  # type: ignore[assignment]
    logger.debug("Patched anyio CancelScope._deliver_cancellation with 5s timeout")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Add setup for long-lived connections"""

    initialize_openai_client()
    setup_cache()  # Initialize cache once at startup
    kafka_application_context.start()

    # Set custom asyncio exception handler to filter benign MCP cleanup errors
    from lumina_common.logging import _asyncio_exception_handler

    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_asyncio_exception_handler)

    # Patch anyio's _deliver_cancellation to stop after 5s (anyio bug #695).
    _patch_anyio_deliver_cancellation()

    # check input and output guardrails have pii configured, if either does, do a warmup to load the NLP models and cache
    if "pii" in settings.enabled_input_guardrails or "pii" in settings.enabled_output_guardrails:
        await warmup_pii()  # Warm up PII Guardrail cache at startup to reduce latency on first request

    yield

    from agent_leasing.api.auth import auth_helper
    from agent_leasing.clients import ldp

    await auth_helper.close()
    await ldp.close()
    kafka_application_context.close()
    flush_traces()


app = FastAPI(
    title="agent-leasing",
    description="REST API to interact with agents.",
    version="0.0.1",
    lifespan=lifespan,
)

# Add middlewares in correct order: CorrelationId first, then logging
app.add_middleware(CorrelationIdMiddleware)  # noqa
app.add_middleware(ASGILoggingMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle validation exceptions. Abide by RFC 9457 Problem Details format."""

    # Convert errors to JSON-serializable format (ctx may contain non-serializable objects)
    errors = []
    for validation_error in exc.errors():
        serializable_error = {
            "type": validation_error.get("type"),
            "loc": validation_error.get("loc"),
            "msg": validation_error.get("msg"),
            "input": validation_error.get("input"),
        }
        errors.append(serializable_error)

    logger.warning(
        "Request validation error",
        path=request.url.path,
        method=request.method,
        errors=errors,
        body=exc.body,
    )

    return JSONResponse(
        status_code=422,
        content={
            "type": "about:blank",
            "title": "Validation Error",
            "status": 422,
            "detail": "Request validation failed",
            "instance": request.url.path,
            "errors": errors,
        },
        media_type="application/problem+json",
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle exceptions. Abide by RFC 9457 Problem Details format."""

    if exc.status_code >= 500:
        logger.error("HTTP exception", status_code=exc.status_code, detail=exc.detail, path=request.url.path)
    else:
        logger.warning("HTTP exception", status_code=exc.status_code, detail=exc.detail, path=request.url.path)

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "type": "about:blank",
            "title": "Error",
            "status": exc.status_code,
            "detail": exc.detail,
            "instance": request.url.path,
        },
        headers=exc.headers,
        media_type="application/problem+json",
    )


@app.get("/healthcheck", tags=["System"])
async def health():
    return {"message": "OK"}


@app.get("/status", tags=["System"])
async def status():
    all_tasks = asyncio.all_tasks()
    return {
        "status": "ok",
        "asyncio_tasks": {
            "total": len(all_tasks),
            "by_name": _count_tasks_by_name(all_tasks),
        },
        "active_voice_handlers": (
            len(twilio_realtime_manager.active_handlers) + len(voice_handler_manager.active_handlers)
        ),
        "active_voice_handlers_by_variant": {
            "v1": len(twilio_realtime_manager.active_handlers),
            "v2": len(voice_handler_manager.active_handlers),
        },
    }


@app.get("/debug/threads", tags=["System"])
async def debug_threads(samples: int = 5, interval_ms: int = 50):
    """Sample all thread stacks to identify what is consuming CPU.

    Takes multiple samples with a short interval to build a statistical
    profile of where threads are spending time (similar to py-spy).

    Args:
        samples: Number of stack snapshots to take (default 5).
        interval_ms: Milliseconds between samples (default 50).
    """
    import sys
    import threading
    import traceback

    samples = min(max(samples, 1), 20)
    interval_ms = min(max(interval_ms, 10), 500)

    thread_names = {t.ident: t.name for t in threading.enumerate()}

    # Collect multiple samples
    all_samples = []
    for i in range(samples):
        frames = sys._current_frames()
        sample = {}
        for thread_id, frame in frames.items():
            name = thread_names.get(thread_id, f"unknown-{thread_id}")
            stack = traceback.format_stack(frame)
            # Keep last 8 frames to avoid noise from deep framework stacks
            sample[name] = [line.strip() for line in stack[-8:]]
        all_samples.append(sample)
        if i < samples - 1:
            await asyncio.sleep(interval_ms / 1000.0)

    # Aggregate: count how often each (thread, top_frame) appears
    frame_counts: dict[str, dict[str, int]] = {}
    for sample in all_samples:
        for thread_name, stack in sample.items():
            if thread_name not in frame_counts:
                frame_counts[thread_name] = {}
            # Use the top 3 frames as the key for grouping
            top = "\n".join(stack[-3:]) if stack else "(empty)"
            frame_counts[thread_name][top] = frame_counts[thread_name].get(top, 0) + 1

    # Build response
    threads_summary = {}
    for thread_name, counts in sorted(frame_counts.items()):
        hotspots = sorted(counts.items(), key=lambda x: -x[1])
        threads_summary[thread_name] = [{"count": count, "frames": frames_str} for frames_str, count in hotspots[:3]]

    # Also collect asyncio task stacks
    all_tasks = asyncio.all_tasks()
    task_stacks: dict[str, int] = {}
    for task in all_tasks:
        coro = task.get_coro()
        if coro is not None:
            # Get the coroutine's current position
            cr_code = getattr(coro, "cr_code", None)
            cr_frame = getattr(coro, "cr_frame", None)
            if cr_code and cr_frame:
                location = f"{cr_code.co_filename}:{cr_frame.f_lineno} in {cr_code.co_name}"
            elif cr_code:
                location = f"{cr_code.co_filename} in {cr_code.co_name} (suspended)"
            else:
                location = str(coro)
        else:
            location = "(no coro)"
        task_stacks[location] = task_stacks.get(location, 0) + 1

    return {
        "thread_count": len(thread_names),
        "threads": dict(sorted(thread_names.items(), key=lambda x: x[1])),
        "samples": samples,
        "interval_ms": interval_ms,
        "hotspots": threads_summary,
        "asyncio_tasks": {
            "total": len(all_tasks),
            "by_location": dict(sorted(task_stacks.items(), key=lambda x: -x[1])),
        },
    }


def _count_tasks_by_name(tasks: set[asyncio.Task]) -> dict[str, int]:
    """Group asyncio tasks by name for diagnostics."""
    counts: dict[str, int] = {}
    for task in tasks:
        name = task.get_name()
        counts[name] = counts.get(name, 0) + 1
    return counts


@app.get("/backends", tags=["System"])
async def backends():
    healthcheck = await build_mcp_dependency_status()
    http_status = 503 if healthcheck["status"] == "degraded" else 200
    return JSONResponse(status_code=http_status, content=healthcheck)


def _get_langsmith_project_name(product: str) -> str:
    """Build LangSmith project name from product code."""
    return f"{settings.environment}_renter_ai_resident_{get_channel_from_product(product).lower()}"


def _build_agent_input(req: AskRequest, context: SessionScope) -> str:
    """Build the agent input string, prepending the email Subject line for EMAIL requests."""
    if get_channel_from_context(context) == "EMAIL":
        email_chat = context.ask_request.product_info.email_chat
        if email_chat and email_chat.email_subject:
            return f"Subject: {email_chat.email_subject}\n\n{req.prompt}"
    return req.prompt


def _build_response_model(
    req: AskRequest, chat_payload: AskChatPayload, flows: list[Flow], langsmith_trace_url: str
) -> AskResponse:
    """Build the response model."""
    return AskResponse(
        request_id=req.request_id,
        metadata={"executed_flow_names": [flow.display_name for flow in flows]},
        content=AskContent(chat=json.dumps(chat_payload)),
        flow_id=req.flow_id or str(uuid.uuid4()),
        flow_name=flows[0].name,
        chat_session_id=req.chat_session_id,
        langsmith_trace_url=langsmith_trace_url,  # None if LangSmith tracing disabled
    )


def _get_external_hostname(request: Union[Request, WebSocket]):
    """Resolve the external hostname from the request."""
    if settings.environment == "prod":
        return settings.prod_host
    elif settings.environment in ["local", "dev"]:
        if request.url.hostname:
            return request.url.hostname
        else:
            return "localhost"
    else:
        return settings.dev_host


async def _check_sms_consent_for_handoff(
    req: AskRequest, context: "SessionScope", knock_resident_id: str
) -> AskResponse | None:
    """Check SMS consent status during handoff and return consent response if needed.

    Creates a lightweight MCP server connection to check consent via handle_sms_consent_gate.

    Args:
        req: The ask request
        context: SessionScope for the current session
        knock_resident_id: The Knock resident ID

    Returns:
        AskResponse with consent message if consent not granted,
        AskResponse with fail-closed metadata if consent check fails,
        None if consent is granted (caller should proceed with handoff).
    """
    try:
        mcp_server = CachingMCPServer(
            name="Knock MCP Server",
            params={"url": settings.knock_mcp_server, "headers": {}},
            cache_tools_list=True,
            auth_function=get_knock_mcp_auth_token if settings.knock_mcp_auth_enabled else None,
        )
        async with mcp_server:
            gate_result = await handle_sms_consent_gate(req, context, mcp_server)

        if gate_result and gate_result.action == "return_message":
            logger.info(
                "SMS consent not granted during handoff - returning consent message",
                sms_consent_status=context.sms_consent_status,
                resident_id=knock_resident_id,
            )
            chat_payload = AskChatPayload(
                response=gate_result.message,
                languageCode=context.language_code,
            )
            return AskResponse(
                request_id=req.request_id,
                metadata={"sms_consent_required": True},
                content=AskContent(chat=chat_payload.model_dump_json()),
                flow_id=req.flow_id or str(uuid.uuid4()),
                flow_name="SMS_CONSENT_FLOW",
                chat_session_id=req.chat_session_id,
            )
        return None
    except Exception as e:
        logger.error(
            "Failed to check SMS consent for handoff - suppressing SMS response",
            error=str(e),
            resident_id=knock_resident_id,
        )
        # Fail closed - no SMS sent when consent can't be verified
        return AskResponse(
            request_id=req.request_id,
            metadata={"sms_consent_check_failed": True, "human_handoff": True},
            content=None,
            flow_id=req.flow_id or str(uuid.uuid4()),
            flow_name="HANDOFF_TO_HUMAN_FLOW",
            chat_session_id=req.chat_session_id,
        )


async def _handle_active_handoff(req: AskRequest, context: "SessionScope | None" = None) -> AskResponse | None:
    """Check if handoff is active and return early response if so.

    Args:
        req: The ask request
        context: Optional SessionScope (only needed for SMS consent check)

    Returns:
        AskResponse if handoff is active, None otherwise.
    """
    channel = get_channel_from_product(req.product)
    if channel not in {"SMS", "EMAIL"}:
        return None

    property_id = req.product_info.knock_property_id
    knock_resident_id = req.product_info.knock_resident_id
    ab_resident_id = getattr(req.product_info.ab_resident_id, "id", None)

    if not await is_handoff_active(req.product, property_id, knock_resident_id, ab_resident_id):
        return None

    logger.info(
        "Handoff active - routing message to inbox",
        channel=channel,
        chat_session_id=req.chat_session_id,
        property_id=property_id,
    )

    # Emit ALREADY_IN_HANDOFF activity as soon as the active handoff is
    # detected, BEFORE any channel-specific branching (SMS consent bounce
    # included). The activity stream tracks "resident pinged during an
    # active handoff", which is true regardless of whether the response
    # path delivers content or short-circuits on consent. Mirror the
    # in-tool pattern: record handoff_result first so session-end
    # task-event payload picks it up. Only emit when we have a
    # SessionScope (caller may pass None during legacy callers).
    if context is not None:
        context.handoff_result = HandoffResult(
            tool="_handle_active_handoff",
            reason=HandoffReasonCode.ALREADY_IN_HANDOFF.value,
            routing_confirmed=True,
            summary=req.prompt,
        )
        publish_task_activity(
            extract_handoff_events,
            req.prompt,
            context,
            reason=HandoffReasonCode.ALREADY_IN_HANDOFF,
        )

    # For EMAIL, don't include a response message - just metadata
    content = None
    if channel != "EMAIL":
        if channel == "SMS" and context is not None:
            consent_response = await _check_sms_consent_for_handoff(req, context, knock_resident_id)
            if consent_response:
                return consent_response

        chat_payload = AskChatPayload(
            response="Thanks for reaching out! We've notified the property staff and they'll get back to you soon. If this is urgent, please contact the office directly. We appreciate your patience!",
            languageCode="en",
        )
        content = AskContent(chat=chat_payload.model_dump_json())

    metadata = {"human_handoff": True}
    if channel == "EMAIL":
        metadata["email_route_back"] = True

    resp_model = AskResponse(
        request_id=req.request_id,
        metadata=metadata,
        content=content,
        flow_id=req.flow_id or str(uuid.uuid4()),
        flow_name="HANDOFF_TO_HUMAN_FLOW",
        chat_session_id=req.chat_session_id,
    )
    logger.info(
        "Static response generated for active handoff",
        response=resp_model.model_dump(),
    )
    return resp_model


def _publish_responder_output_activities(final_output, context: SessionScope, user_message: str | None) -> None:
    """Fan out the post-turn TaskActivityEvent emits driven by the
    responder's structured output. Called from both /v1/agent/ask
    (non-streaming) and /v1/agent/stream so a fourth signal stays a
    one-line addition rather than a two-site diff.
    """
    publish_task_activity(
        extract_qna_events,
        final_output.workflow_codes,
        context,
        qna_topics=final_output.qna_topics,
        user_message=user_message,
    )
    publish_task_activity(
        extract_frustrated_user_events,
        final_output.user_frustrated,
        context,
        user_message=user_message,
        # Delivery-time dedup — flag flips only after Kafka confirms the
        # publish, so a transient producer failure leaves the next turn
        # free to retry the FRUSTRATED_USER signal.
        on_success=_make_frustration_dedup_callback(context),
    )


def _make_frustration_dedup_callback(context: SessionScope) -> Callable[[], None]:
    def _flip() -> None:
        context.frustrated_user_emitted = True

    return _flip


@dataclass
class UrlHandoffResult:
    response_text: str
    language_code: str
    metadata: dict


async def _handle_url_transfer(req: AskRequest, context: SessionScope) -> UrlHandoffResult | None:
    """Detect URL placeholder in prompt and return handoff data.

    Checks CHAT/SMS/EMAIL channels only. For SMS/EMAIL writes a Redis key to
    pause AI responses. Returns a UrlHandoffResult when a URL was detected, None otherwise.
    """
    channel = get_channel_from_product(req.product)
    if channel not in {"CHAT", "SMS", "EMAIL"}:
        return None

    if URL_REPLACEMENT not in req.prompt:
        return None

    logger.info(
        "URL detected in resident message - routing message to inbox",
        channel=channel,
        chat_session_id=req.chat_session_id,
    )

    if channel in {"SMS", "EMAIL"}:
        property_id = req.product_info.knock_property_id
        knock_resident_id = req.product_info.knock_resident_id
        ab_resident_id = getattr(req.product_info.ab_resident_id, "id", None)
        handoff_key = maybe_get_handoff_key(
            req.product,
            property_id,
            knock_resident_id,
            ab_resident_id,
        )
        if handoff_key is not None:
            handoff_data = {
                "transferred": True,
                "handoff_time": datetime.now(UTC).isoformat(),
            }
            await memory.put(handoff_key, handoff_data, expire=settings.handoff_inactivity_ttl)
            logger.info(f"URL handoff state written to Redis for {channel} channel with key: {handoff_key}")

    language_code = context.language_code
    response_text = await translate_text(URL_HANDOFF_RESPONSE, language_code)

    return UrlHandoffResult(
        response_text=response_text,
        language_code=language_code,
        metadata={
            "human_handoff": True,
            "human_hand_off_message": "Resident submitted a url for review",
            "email_route_back": channel == "EMAIL",
        },
    )


@app.post(
    "/v1/agent/ask",
    description=AGENT_ASK_DESCRIPTION,
    tags=["Agent"],
    response_model=AskResponse,  # documents/enforces response shape
)
async def agent_ask(
    req: Annotated[AskRequest, Body(openapi_examples=OPENAPI_EXAMPLES)],
):
    """Non-streaming endpoint."""

    async def _agent_ask_impl(req):
        run = ls.get_current_run_tree()

        try:
            agent_request = await build_agent_request(req)
        except UnsupportedAgentException:
            raise HTTPException(status_code=422, detail=f"Unsupported agent: {req.product}")

        start_time = agent_request.start_time

        if is_langsmith_enabled() and run:
            run.add_metadata(normalize_metadata_keys(agent_request.metadata))

        # Check if handoff is active - return early without AI processing.
        # Pass the SessionScope for both SMS and EMAIL — needed by the
        # ALREADY_IN_HANDOFF TaskActivityEvent emit, plus the SMS-only
        # consent check. EMAIL ignores the consent path internally.
        channel = get_channel_from_product(req.product)
        context_param = agent_request.context if channel in {"SMS", "EMAIL"} else None
        if handoff_response := await _handle_active_handoff(req, context_param):
            handoff_input = _build_agent_input(req, context_param) if context_param else req.prompt
            handoff_response.langsmith_trace_url = annotate_handoff_bypass(run, handoff_input)
            logger.info(
                "Agent response complete",
                event_type="agent_response_complete",
                agent_response_ms=elapsed_ms(start_time),
                channel=req.product,
                exit_path="handoff_active",
            )
            return JSONResponse(content=handoff_response.model_dump())

        language_code = agent_request.language_code
        flows = agent_request.flows
        logging_metadata = agent_request.logging_metadata
        headers = agent_request.headers
        context = agent_request.context
        previous_response_id = agent_request.previous_response_id
        agent = agent_request.agent

        if is_langsmith_enabled() and run:
            context.langsmith_run_tree = run.to_headers()

        openai_trace_url = build_openai_trace_url(agent_request.trace_id)
        langsmith_trace_url = get_langsmith_trace_url(run)
        if langsmith_trace_url:
            langsmith_trace_id = extract_langsmith_trace_id(langsmith_trace_url)
            agent_request.metadata["langsmith-trace-id"] = langsmith_trace_id

        openai_metadata = {k: v for k, v in agent_request.metadata.items() if k in OPENAI_TRACE_METADATA_KEYS}

        with ls.trace(name="HumanMessage", run_type="llm") as human_trace:
            human_trace.end(outputs={"message": _build_agent_input(req, context)})

        # Check for URL sanitization - trigger handoff
        if url_handoff := await _handle_url_transfer(req, context):
            with ls.trace(name="AIMessage", run_type="llm") as url_trace:
                url_trace.end(outputs={"message": "URL detected in resident message - routing message to inbox"})
            chat_payload = AskChatPayload(
                response=url_handoff.response_text, languageCode=url_handoff.language_code
            ).model_dump()
            url_handoff_response = AskResponse(
                request_id=req.request_id,
                metadata=url_handoff.metadata,
                content=AskContent(chat=json.dumps(chat_payload)),
                flow_id=req.flow_id or str(uuid.uuid4()),
                flow_name="HANDOFF_TO_HUMAN_FLOW",
                chat_session_id=req.chat_session_id,
            )
            # Drain task-event publishes (IN_PROGRESS scheduled at SessionScope
            # creation) so they finish before the response closes — this branch
            # short-circuits before the agent block's drain finally.
            await drain_pending_publishes(context.pending_activity_publishes)
            logger.info(
                "Agent response complete",
                event_type="agent_response_complete",
                agent_response_ms=elapsed_ms(start_time),
                channel=req.product,
                exit_path="url_handoff",
            )
            return JSONResponse(content=url_handoff_response.model_dump())

        with trace(
            workflow_name=agent_request.workflow_name,
            trace_id=agent_request.trace_id,
            group_id=agent_request.group_id,
            metadata=openai_metadata,
        ):
            async with agent as agent_wth_mcp:
                # Resolve conversation_id now — the background task started in
                # build_agent_request has been running in parallel with MCP connections.
                conversation_id = await ensure_conversation_id(context)
                if conversation_id:
                    structlog.contextvars.bind_contextvars(openai_conversation_id=conversation_id)
                    openai_metadata["openai-conversation-id"] = conversation_id

                result = None
                try:
                    try:
                        channel = get_channel_from_context(context)

                        # SMS consent gate - blocks agent unless status is "granted"
                        gate_result = None
                        if channel == "SMS" and context.ask_request.product_info.source != "AIRR":
                            property_mcp_server = agent_wth_mcp.mcp_servers.get("knock_mcp_server")
                            if property_mcp_server:
                                gate_result = await handle_sms_consent_gate(req, context, property_mcp_server)

                        if gate_result and gate_result.action == "return_message":
                            # Gate returned a message directly - skip agent entirely
                            logger.info(
                                "Returning message from SMS consent gate, skipping agent",
                                sms_consent_status=context.sms_consent_status,
                            )
                            result_response = gate_result.message
                            # IMPORTANT: Store pending query in context so when user sends START,
                            # the agent can process it with awareness of this consent request/grant
                            if hasattr(context, "pending_sms_query") and context.pending_sms_query:
                                logger.info(
                                    "SMS consent gate blocked agent, storing pending query for later",
                                    pending_query=context.pending_sms_query,
                                    sms_consent_status=context.sms_consent_status,
                                )
                        else:
                            # Normal path - run agent (status is "granted" or gate didn't apply)
                            # Use req.prompt instead of agent_input because gate may have updated it
                            agent_input = _build_agent_input(req, context)
                            result = await run_agent_with_orphan_recovery(
                                agent_wth_mcp.agent_instance,
                                input=agent_input,
                                context=context,
                                previous_response_id=previous_response_id,
                                conversation_id=conversation_id,
                                openai_metadata=openai_metadata,
                            )

                            result_response = result.final_output
                            if isinstance(result.final_output, ResidentResponderOutput):
                                language_code = result.final_output.language_code
                                context.language_code = language_code
                                result_response = result.final_output.response

                                recorded_flows = result.final_output.extract_flows()
                                logger.debug(f"Recorded flows: {recorded_flows}")
                                if recorded_flows:
                                    flows = recorded_flows

                                _publish_responder_output_activities(result.final_output, context, req.prompt)

                            if agent_wth_mcp.agent_architecture == AgentArchitecture.RESPONDER_THINKER:
                                flows = get_flows(result) or flows
                            if agent_wth_mcp.agent_architecture == AgentArchitecture.SINGLE_AGENT:
                                add_metadata_into_context(context, result)

                            logger.info(f"Flows: {[flow.name for flow in flows]}")

                            logging_metadata = list(result.context_wrapper.context.logging_metadata.values())

                            previous_response_id = result.last_response_id

                    except (
                        InputGuardrailTripwireTriggered,
                        OutputGuardrailTripwireTriggered,
                    ) as exc:
                        # Issue #1569 Layer 1. Input-only is intentional: output
                        # guardrails run after every function_call has its output, so
                        # there's no orphan to clean.
                        await cleanup_orphan_after_guardrail_trip(exc, context.openai_conversation_id, site="ask")

                        result = exc.run_data
                        guardrail_result = exc.guardrail_result
                        logger.info(
                            "Guardrail triggered",
                            guardrail=str(exc.guardrail_result.guardrail.name),  # __name__ doesn't exist
                            reasoning=getattr(guardrail_result.output.output_info, "reasoning", None),
                            labels=getattr(guardrail_result.output.output_info, "labels", None),
                        )
                        result_response = (
                            guardrail_result.output.output_info.safe_response
                        )  # no need to look into the RunResult for the safe_response

                    if result is not None:
                        log_internal_messages(result)
                        await emit_metrics(result, req.chat_session_id)

                    logger.info(f"Output: {result_response}")

                    chat_payload = AskChatPayload(response=result_response, languageCode=language_code).model_dump()
                    resp_model = _build_response_model(req, chat_payload, flows, langsmith_trace_url)

                    await save_previous_response_id(headers, context, previous_response_id)
                    save_conversation_id(headers, context)

                    if context.handoff:
                        resp_model = execute_handoff(req.conversation_type, context.handoff_message, resp_model)
                        # Non-voice handoff: emit PENDING + escalation per spec.
                        # Build the event before clearing handoff/handoff_message
                        # so the escalation summary can be derived from them.
                        publish_task_event_fire_and_forget(
                            kafka_application_context.task_event_producer,
                            build_pending_handoff_event(context),
                            context.pending_activity_publishes,
                        )
                        context.handoff = False
                        context.handoff_message = None

                    # Add to the header
                    headers.update({PRODUCT_HEADER: req.product})
                    headers.update({AGENT_HEADER: agent.name})
                    headers.update({FLOWS_HEADER: ",".join([flow.name for flow in flows])})
                    headers.update({LANGUAGE_HEADER: language_code})
                    if langsmith_trace_url:
                        headers["X-LangSmith-Trace-Url"] = langsmith_trace_url

                    # Suppress bot message for Kafka and LangSmith when email is handed off
                    kafka_bot_message = result_response
                    if channel == "EMAIL" and resp_model.metadata.get("email_route_back"):
                        kafka_bot_message = "User requested handoff. No message was sent to the user."

                    await log_conversation_exchange(
                        chat_session_id=req.chat_session_id,
                        conversation_type=req.conversation_type,
                        user_message=req.prompt,
                        bot_message=kafka_bot_message,
                        call_sid=req.product_info.call_sid,
                        property_id=req.property_id,
                        applicant_id=req.resident_id,
                        bot_type=context.persona,
                        flows=flows,
                        language=language_code,
                        bot_metadata=logging_metadata,
                        openai_trace_url=openai_trace_url,
                        langsmith_trace_url=langsmith_trace_url,
                    )

                    # AIMessage span created AFTER Runner.run so it appears after
                    # ChatPromptTemplate in the trace: HumanMessage → ChatPromptTemplate → AIMessage
                    log_ai_message_span(
                        _build_agent_input(req, context), kafka_bot_message, context.rendered_system_prompt
                    )

                    logger.info(
                        "Agent response complete",
                        event_type="agent_response_complete",
                        agent_response_ms=elapsed_ms(start_time),
                        channel=req.product,
                        exit_path="success",
                    )
                    return JSONResponse(content=resp_model.model_dump(), headers=headers)

                except asyncio.CancelledError:
                    # Propagate cancellation - let the request be cancelled properly
                    raise
                except Exception:
                    logger.exception("Reverted to fallback response due to error")
                    chat_payload = AskChatPayload(response=FALLBACK_RESPONSE).model_dump()

                    fallback_model = _build_response_model(req, chat_payload, flows, langsmith_trace_url)
                    if langsmith_trace_url:
                        headers["X-LangSmith-Trace-Url"] = langsmith_trace_url

                    await log_conversation_exchange(
                        chat_session_id=req.chat_session_id,
                        conversation_type=req.conversation_type,
                        user_message=req.prompt,
                        bot_message=FALLBACK_RESPONSE,
                        call_sid=req.product_info.call_sid,
                        property_id=req.property_id,
                        applicant_id=req.resident_id,
                        bot_type=context.persona,
                        flows=flows,
                        language=language_code,
                        openai_trace_url=openai_trace_url,
                        langsmith_trace_url=langsmith_trace_url,
                    )

                    await save_previous_response_id(headers, context, previous_response_id)
                    save_conversation_id(headers, context)

                    log_ai_message_span(
                        _build_agent_input(req, context), FALLBACK_RESPONSE, context.rendered_system_prompt
                    )

                    logger.info(
                        "Agent response complete",
                        event_type="agent_response_complete",
                        agent_response_ms=elapsed_ms(start_time),
                        channel=req.product,
                        exit_path="fallback",
                    )
                    return JSONResponse(content=fallback_model.model_dump(), headers=headers)

                finally:
                    # Store the context in memory
                    await memory.put_context(memory.context_cache_key(req), context, agent_request.expire)
                    # Drain any in-flight task-event publishes scheduled during
                    # this turn (IN_PROGRESS on first turn / PENDING on handoff)
                    # so background tasks finish before the request returns.
                    await drain_pending_publishes(context.pending_activity_publishes)

    if not req.is_load_test:
        _agent_ask_impl = ls.traceable(
            name=req.product,
            run_type="chain",
            project_name=_get_langsmith_project_name(req.product),
            process_inputs=lambda _: {"message": req.prompt},
            process_outputs=process_nonstreaming_outputs,
        )(_agent_ask_impl)

    with ls.tracing_context(enabled=not req.is_load_test):
        return await _agent_ask_impl(req)


@app.post(
    "/v1/agent/stream",
    description=AGENT_STREAM_DESCRIPTION,
    tags=["Agent"],
)
async def agent_stream(
    req: Annotated[AskRequest, Body(openapi_examples=OPENAPI_STREAMING_EXAMPLES)],
):
    """Streaming endpoint."""
    try:
        agent_request = await build_agent_request(req)
    except UnsupportedAgentException:
        raise HTTPException(status_code=422, detail=f"Unsupported agent: {req.product}")

    async def generate(req, agent_request):
        start_time = agent_request.start_time
        language_code = agent_request.language_code
        flows = agent_request.flows
        logging_metadata = agent_request.logging_metadata
        headers = agent_request.headers
        context = agent_request.context
        previous_response_id = agent_request.previous_response_id
        agent = agent_request.agent

        openai_trace_url = build_openai_trace_url(agent_request.trace_id)

        run = ls.get_current_run_tree()

        if is_langsmith_enabled() and run:
            context.langsmith_run_tree = run.to_headers()

        langsmith_trace_url = get_langsmith_trace_url(run)
        if langsmith_trace_url:
            langsmith_trace_id = extract_langsmith_trace_id(langsmith_trace_url)
            agent_request.metadata["langsmith-trace-id"] = langsmith_trace_id

        with ls.trace(name="HumanMessage", run_type="llm") as human_trace:
            human_trace.end(outputs={"message": _build_agent_input(req, context)})

        if url_handoff := await _handle_url_transfer(req, context):
            with ls.trace(name="AIMessage", run_type="llm") as url_trace:
                url_trace.end(outputs={"message": "URL detected in resident message - routing message to inbox"})
            yield start(elapsed_ms(start_time))
            yield generating(content=url_handoff.response_text, elapsed=elapsed_ms(start_time))
            yield handoff(elapsed=elapsed_ms(start_time), metadata=url_handoff.metadata)
            url_handoff_elapsed = elapsed_ms(start_time)
            logger.info(
                "Agent response complete",
                event_type="agent_response_complete",
                agent_response_ms=url_handoff_elapsed,
                channel=req.product,
                exit_path="url_handoff",
            )
            yield end(elapsed=url_handoff_elapsed)
            # Drain task-event publishes (IN_PROGRESS scheduled at SessionScope
            # creation) so they finish before the stream closes — this branch
            # short-circuits before the agent block's drain points.
            await drain_pending_publishes(context.pending_activity_publishes)
            yield DONE
            return

        openai_metadata = {k: v for k, v in agent_request.metadata.items() if k in OPENAI_TRACE_METADATA_KEYS}

        with trace(
            workflow_name=agent_request.workflow_name,
            trace_id=agent_request.trace_id,
            group_id=agent_request.group_id,
            metadata=openai_metadata,
        ):
            async with agent as agent_wth_mcp:
                # Resolve conversation_id now — the background task started in
                # build_agent_request has been running in parallel with MCP connections.
                conversation_id = await ensure_conversation_id(context)
                if conversation_id:
                    structlog.contextvars.bind_contextvars(openai_conversation_id=conversation_id)
                    openai_metadata["openai-conversation-id"] = conversation_id

                try:
                    yield start(elapsed_ms(start_time))

                    chunks_yielded = 0
                    attempted_orphan_recovery = False
                    try:
                        while True:
                            try:
                                result = Runner.run_streamed(
                                    agent_wth_mcp.agent_instance,
                                    input=_build_agent_input(req, context),
                                    context=context,
                                    previous_response_id=previous_response_id if not conversation_id else None,
                                    conversation_id=conversation_id,
                                )

                                # Process streaming events using the event processor
                                processor = StreamEventProcessor(json_attribute=JSON_ATTRIBUTE_TO_EXTRACT)
                                async for processed_chunk in processor.process_events(result):
                                    if processed_chunk is not None:
                                        chunks_yielded += 1
                                        yield generating(
                                            content=processed_chunk,
                                            elapsed=elapsed_ms(start_time),
                                        )
                                break  # success
                            except BadRequestError as exc:
                                # Issue #1569 Layer 2 (stream): retry once with a fresh
                                # conversation if the orphan error fires before any chunk
                                # has gone to the client. After chunks are yielded, the SSE
                                # stream is already in flight to the client and we cannot
                                # restart cleanly — let it bubble to the fallback path.
                                if (
                                    attempted_orphan_recovery
                                    or chunks_yielded > 0
                                    or not conversation_id
                                    or not is_orphan_function_call_error(exc)
                                ):
                                    raise
                                attempted_orphan_recovery = True
                                logger.warning(
                                    "OpenAI conversation has orphan function_call on stream; "
                                    "resetting and retrying with fresh conversation",
                                    corrupted_conversation_id=conversation_id,
                                    error=str(exc),
                                )
                                # The retry must NOT chain off the corrupted conversation:
                                # mirror the wrapper's behavior and force previous_response_id
                                # to None. Otherwise a recovery that returns no fresh
                                # conversation_id would still send the stale response id and
                                # 400 again on the chained request.
                                previous_response_id = None
                                conversation_id = await reset_and_create_fresh_conversation(context)
                                if conversation_id:
                                    structlog.contextvars.bind_contextvars(openai_conversation_id=conversation_id)
                                    openai_metadata["openai-conversation-id"] = conversation_id

                        # Extract final output and flows
                        final_output = processor.final_output
                        final_output_response = processor.final_output_response

                        # If the output is of the type ResidentResponderOutput,
                        # make use the attributes in there
                        if isinstance(final_output, ResidentResponderOutput):
                            language_code = final_output.language_code
                            context.language_code = language_code
                            recorded_flows = final_output.extract_flows()
                            logger.debug(f"Recorded flows: {recorded_flows}")
                            if recorded_flows:
                                flows = recorded_flows

                            _publish_responder_output_activities(final_output, context, req.prompt)

                        if agent_wth_mcp.agent_architecture == AgentArchitecture.RESPONDER_THINKER:
                            flows = get_flows(result) or flows
                        if agent_wth_mcp.agent_architecture == AgentArchitecture.SINGLE_AGENT:
                            add_metadata_into_context(context, result)

                        logger.info(f"Flows: {[flow.name for flow in flows]}")

                        logging_metadata = list(result.context_wrapper.context.logging_metadata.values())

                        previous_response_id = result.last_response_id

                    except (
                        InputGuardrailTripwireTriggered,
                        OutputGuardrailTripwireTriggered,
                    ) as exc:
                        # Issue #1569 Layer 1 — see /ask handler.
                        await cleanup_orphan_after_guardrail_trip(exc, context.openai_conversation_id, site="stream")

                        result = exc.run_data
                        guardrail_result = exc.guardrail_result
                        logger.info(
                            "Guardrail triggered",
                            guardrail=str(exc.guardrail_result.guardrail.name),  # __name__ doesn't exist
                            reasoning=getattr(guardrail_result.output.output_info, "reasoning", None),
                            labels=getattr(guardrail_result.output.output_info, "labels", None),
                        )
                        final_output_response = (
                            guardrail_result.output.output_info.safe_response
                        )  # no need to look into the RunResult for the safe_response

                        yield generating(
                            content=final_output_response,
                            elapsed=elapsed_ms(start_time),
                        )

                    log_internal_messages(result)
                    logger.info(f"Output: {final_output_response}")

                    await emit_metrics(result, req.chat_session_id)

                    await log_conversation_exchange(
                        chat_session_id=req.chat_session_id,
                        conversation_type=req.conversation_type,
                        user_message=req.prompt,
                        bot_message=final_output_response,
                        call_sid=req.product_info.call_sid,
                        property_id=req.property_id,
                        applicant_id=req.resident_id,
                        bot_type=context.persona,
                        flows=flows,
                        language=language_code,
                        bot_metadata=logging_metadata,
                        openai_trace_url=openai_trace_url,
                        langsmith_trace_url=langsmith_trace_url,
                    )

                    await save_previous_response_id(headers, context, previous_response_id)
                    save_conversation_id(headers, context)

                    if context.handoff:
                        yield handoff(
                            elapsed=elapsed_ms(start_time),
                            metadata={
                                "human_handoff": True,
                                "human_hand_off_message": context.handoff_message,
                            },
                        )
                        # Non-voice handoff: emit PENDING + escalation per spec.
                        # Build the event before clearing handoff/handoff_message
                        # so the escalation summary can be derived from them.
                        publish_task_event_fire_and_forget(
                            kafka_application_context.task_event_producer,
                            build_pending_handoff_event(context),
                            context.pending_activity_publishes,
                        )
                        context.handoff = False
                        context.handoff_message = None

                    main_elapsed = elapsed_ms(start_time)
                    logger.info(
                        "Agent response complete",
                        event_type="agent_response_complete",
                        agent_response_ms=main_elapsed,
                        channel=req.product,
                        exit_path="success",
                    )
                    yield end(elapsed=main_elapsed)

                    # AIMessage span created AFTER Runner.run so it appears after
                    # ChatPromptTemplate in the trace: HumanMessage → ChatPromptTemplate → AIMessage
                    log_ai_message_span(
                        _build_agent_input(req, context), final_output_response, context.rendered_system_prompt
                    )

                    yield DONE

                    await memory.put_context(memory.context_cache_key(req), context, agent_request.expire)
                    # Drain any in-flight task-event publishes scheduled during
                    # this turn (IN_PROGRESS / PENDING) so background tasks
                    # finish before the streaming response closes.
                    await drain_pending_publishes(context.pending_activity_publishes)

                    return

                except Exception as e:  # noqa
                    logger.exception("Reverted to fallback response due to error")

                    await log_conversation_exchange(
                        chat_session_id=req.chat_session_id,
                        conversation_type=req.conversation_type,
                        user_message=req.prompt,
                        bot_message=FALLBACK_RESPONSE,
                        call_sid=req.product_info.call_sid,
                        property_id=req.property_id,
                        applicant_id=req.resident_id,
                        bot_type=context.persona,
                        flows=flows,
                        language=language_code,
                        openai_trace_url=openai_trace_url,
                        langsmith_trace_url=langsmith_trace_url,
                    )

                    await save_previous_response_id(headers, context, previous_response_id)
                    save_conversation_id(headers, context)

                    yield generating(
                        content=FALLBACK_RESPONSE,
                        elapsed=elapsed_ms(start_time),
                    )
                    fallback_elapsed = elapsed_ms(start_time)
                    logger.info(
                        "Agent response complete",
                        event_type="agent_response_complete",
                        agent_response_ms=fallback_elapsed,
                        channel=req.product,
                        exit_path="fallback",
                    )
                    yield end(elapsed=fallback_elapsed)
                    log_ai_message_span(
                        _build_agent_input(req, context), FALLBACK_RESPONSE, context.rendered_system_prompt
                    )

                    # Drain task-event publishes on the fallback path too, so
                    # IN_PROGRESS scheduled at SessionScope creation completes.
                    await drain_pending_publishes(context.pending_activity_publishes)

                    yield DONE

                    await memory.put_context(memory.context_cache_key(req), context, agent_request.expire)

                    return

    if not req.is_load_test:
        generate = ls.traceable(
            name=req.product,
            run_type="chain",
            project_name=_get_langsmith_project_name(req.product),
            metadata=normalize_metadata_keys(agent_request.metadata),
            process_inputs=lambda _: {"message": req.prompt},
            reduce_fn=aggregate_streaming_outputs,
        )(generate)

    try:
        with ls.tracing_context(enabled=not req.is_load_test):
            return StreamingResponse(
                generate(req, agent_request),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )

    except asyncio.CancelledError:
        # Propagate cancellation - let the request be cancelled properly
        raise
    except Exception as e:
        logger.exception(f"Streaming error: {e}")
        return StreamingResponse(
            (chunk for chunk in [error("There was an error"), DONE]),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )


@app.get(
    "/v1/cache/property/{property_id}",
    description="Warms cache for a specific property",
    summary="Warm property cache",
    tags=["System"],
)
async def warm_property_cache(property_id: Annotated[int, "Unique property identifier"]):
    """Warm cache for specified property by pre-fetching LDP data (modules, PTE, resident summary)."""
    await fetch_ldp_property_data(str(property_id))
    return {"status": "ok"}


@app.post(
    "/v1/cache/property/{property_id}",
    description="Returns cache invalidation statuses. True indicates something was found in the cache.",
    summary="Invalidate property caches",
    tags=["System"],
)
async def invalidate_property_cache(property_id: Annotated[int, "Unique property identifier"]) -> dict:
    """Invalidate property caches."""

    logger.info(f"Invalidating caches for property {property_id}")
    invalidate_property_caches = await CachingMCPServer.invalidate_property_caches(property_id)
    return {"status": invalidate_property_caches}


# Manager for RealTime Voice Connections.
# Both managers are always constructed so the v1 and v2 endpoints can
# coexist.  Per-call routing (which endpoint a given call lands on) is
# driven upstream in cai-genai-service via a feature flag; ``use_voice_refactor``
# is the agent-leasing-side kill-switch that forces the v2 endpoint to
# fall back to v1 regardless of upstream routing.
twilio_realtime_manager = TwilioWebSocketManager()
voice_handler_manager = VoiceHandlerManager()


@app.api_route(
    "/realtime-incoming-call",
    methods=["GET", "POST"],
    summary="Handle Realtime Incoming Voice Call ",
    tags=["Voice"],
)
async def handle_incoming_call_realtime(request: Request, x_twilio_signature: str = Header(None)):
    """
    Handles incoming voice calls from Twilio using realtime agents.

    - **GET**: Returns TwiML to connect the call to the agent voice assistant via WebSocket.
    - **POST**: Same as GET, but can be used for Twilio webhook POSTs.
    Returns TwiML XML that instructs Twilio to connect the call to the assistant.

    ## How it works
    This endpoint generates TwiML instructing Twilio to establish a WebSocket connection to `/media-stream/websocket`.
    On the server, each WebSocket connection is managed by a RealtimeManager instance, which:
      - Handles the real-time audio stream from Twilio
      - Manages the session state and payload for the call
      - Coordinates the agent response and audio streaming back to Twilio

    Note that this is only provided for testing, as Knock returns the TwiML to Twilio.
    """

    await validate_twilio_request(
        f"https://{_get_external_hostname(request)}{request.url.path}",
        await request.form(),
        x_twilio_signature,
    )

    host = _get_external_hostname(request)

    # Mirror the kill-switch semantics used by the production v2 endpoint:
    # when USE_VOICE_REFACTOR is true this test-only TwiML generator points
    # Twilio at /v2; otherwise at the legacy path.  Keeps local testing
    # parity with production routing without touching cai-genai-service.
    voice_path = "/media-stream/websocket/v2" if settings.use_voice_refactor else "/media-stream/websocket"

    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Connect>
            <Stream url="wss://{host}{voice_path}" />
        </Connect>
    </Response>"""
    return PlainTextResponse(content=twiml_response, media_type="text/xml")


async def _run_media_stream(
    websocket: WebSocket,
    manager: TwilioWebSocketManager | VoiceHandlerManager,
    variant: str,
    x_twilio_signature: str | None,
) -> None:
    """Shared handling for the v1 and v2 Twilio media-stream endpoints."""
    await validate_twilio_request(
        f"wss://{_get_external_hostname(websocket)}{websocket.url.path}",
        None,
        x_twilio_signature,
    )
    logger.info("Voice handler routed", voice_handler_variant=variant)
    handler = None
    try:
        handler = await manager.new_session(websocket)
        # Tag the handler so its LangSmith trace metadata includes the variant.
        handler.variant = variant
        await handler.start()
        # Legacy handler: start() creates background tasks, wait_until_done() blocks.
        # Voice refactor: start() blocks until the call ends, wait_until_done() is a no-op.
        await handler.wait_until_done()

    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # Ensure handler cleanup happens even if there's an exception
        # This prevents memory leaks from handlers not being properly cleaned up
        if handler:
            # The handler's _cleanup_call is already invoked when the "stop" event
            # is received from Twilio, but we ensure it here as well for safety
            if handler.call_active:
                try:
                    await handler._cleanup_call()
                except Exception as cleanup_error:
                    logger.warning(f"Error during handler cleanup in finally block: {cleanup_error}")
            # Regardless of call_active, ensure the handler is deregistered from the manager
            try:
                await manager.cleanup_handler(str(id(handler)))
            except Exception as cleanup_error:
                logger.warning(f"Error during handler deregistration in finally block: {cleanup_error}")


@app.websocket("/media-stream/websocket")
async def media_stream_endpoint(websocket: WebSocket, x_twilio_signature: str = Header(None)):
    """V1 WebSocket endpoint — always routes to ``twilio_handler.py``."""
    await _run_media_stream(websocket, twilio_realtime_manager, "v1", x_twilio_signature)


@app.websocket("/media-stream/websocket/v2")
async def media_stream_endpoint_v2(websocket: WebSocket, x_twilio_signature: str = Header(None)):
    """Refactored voice-package endpoint (KNCK-39531).

    Routed to by cai-genai-service when its feature flag resolves to
    ``v2`` for a given property.  When ``use_voice_refactor`` is False
    (kill-switch), this endpoint silently falls back to the v1 manager
    so upstream flag changes cannot cause traffic loss.
    """
    if settings.use_voice_refactor:
        manager, variant = voice_handler_manager, "v2"
    else:
        manager, variant = twilio_realtime_manager, "v1_fallback"
    await _run_media_stream(websocket, manager, variant, x_twilio_signature)


if settings.chatbot_enabled:
    from agent_leasing import chatbot

    chatbot.init(app)

# Configure OpenTelemetry
setup_opentelemetry(app)

# Mount Voice UI sub-application
app.mount("/voice-ui", voice_ui_app)


def main():
    """Package entry point for running the server."""
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port, loop="uvloop", http="httptools")


if __name__ == "__main__":
    main()
