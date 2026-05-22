"""Service functions for agent operations."""

import asyncio
import datetime
import time
import uuid
from typing import Any

import structlog
from agents import InputGuardrailTripwireTriggered, Runner, RunResult, RunResultStreaming, gen_trace_id
from agents.tracing.util import gen_group_id
from openai import BadRequestError

from agent_leasing.agent.util import (
    SessionScope,
    agent_selector,
    get_channel_from_context,
)
from agent_leasing.api.model import AskRequest, Flow
from agent_leasing.kafka.kafka_context import kafka_application_context
from agent_leasing.kafka.task_event import (
    build_in_progress_event,
    publish_task_event_fire_and_forget,
)
from agent_leasing.services.input_sanitizers import sanitize_input
from agent_leasing.settings import settings
from agent_leasing.util import memory

logger = structlog.getLogger()

TRACE_ID_HEADER = "X-OpenAPI-Trace-Id"
PREVIOUS_RESPONSE_ID_HEADER = "X-OpenAI-Previous-Response-Id"
CONVERSATION_ID_HEADER = "X-OpenAI-Conversation-Id"

_CONVERSATION_CREATE_MAX_RETRIES = 3
_CONVERSATION_CREATE_BASE_DELAY = 0.5


def get_flows(result: RunResult | RunResultStreaming) -> list[Flow]:
    """Extract flows from the result's raw responses.

    Looks for function calls that end with 'thinker_tool' or 'transfer_to_staff_text'
    and creates Flow objects from them.

    Args:
        result: The run result containing raw responses with tool calls

    Returns:
        list[Flow]: List of flows extracted from the result
    """
    flows = []
    try:
        model_response = result.raw_responses[0]
        for tool_call in model_response.output:
            if tool_call.type == "function_call" and tool_call.name.endswith(
                ("thinker_tool", "transfer_to_staff_text")
            ):
                flows.append(Flow(name=tool_call.name))
    except Exception as e:
        logger.warning(f"Error getting flows: {e}")
    return flows


async def save_previous_response_id(headers: dict, context: SessionScope, previous_response_id: str | None = None):
    """Save the previous response ID in the context and response headers.

    Args:
        headers: The response headers dictionary to update
        context: The session context to update
        previous_response_id: The previous response ID to save, if any
    """
    if previous_response_id:
        logger.debug(f"Previous response ID: {previous_response_id}")
        # If there is a previous_response_id, include it as a response header
        headers.update({PREVIOUS_RESPONSE_ID_HEADER: previous_response_id})
        # Set previous_response_id in the context
        context.previous_response_id = previous_response_id


def _build_conversation_metadata(context: SessionScope) -> dict[str, str]:
    """Build metadata for the OpenAI conversation from the session context.

    Reuses the same fields available in the trace metadata dict built later in
    build_agent_request, but is kept separate because it runs earlier (before
    agent selection) and must also work from the voice thinker path.
    """
    req = context.ask_request
    raw: dict[str, str | None] = {
        "app": settings.app_name,
        "environment": settings.environment,
        "chat_session_id": getattr(req, "chat_session_id", None),
        "product": getattr(req, "product", None),
        "property_id": getattr(req, "property_id", None),
        "property_name": getattr(req, "product_info", None) and req.product_info.property_name,
    }
    return {k: str(v) for k, v in raw.items() if v is not None and v != ""}


async def _create_conversation(context: SessionScope) -> str:
    """Call OpenAI to create a conversation. Retries with exponential backoff."""
    from agent_leasing.clients.openai import get_openai_client

    metadata = _build_conversation_metadata(context)
    client = get_openai_client()
    last_exc: Exception | None = None
    for attempt in range(_CONVERSATION_CREATE_MAX_RETRIES):
        try:
            conversation = await client.conversations.create(metadata=metadata or None)
            context.openai_conversation_id = conversation.id
            logger.info(f"Created OpenAI conversation: {conversation.id}")
            return conversation.id
        except Exception as exc:
            last_exc = exc
            delay = _CONVERSATION_CREATE_BASE_DELAY * (2**attempt)
            logger.warning(
                f"conversations.create attempt {attempt + 1}/{_CONVERSATION_CREATE_MAX_RETRIES} failed: {exc}, "
                f"retrying in {delay}s"
            )
            await asyncio.sleep(delay)

    logger.error(f"conversations.create failed after {_CONVERSATION_CREATE_MAX_RETRIES} attempts")
    raise last_exc  # type: ignore[misc]


def start_conversation_creation(context: SessionScope) -> None:
    """Fire off conversation creation as a background task so it runs in parallel
    with MCP connections, LDP calls, and other setup work.  The result is awaited
    later by ensure_conversation_id.
    """
    if not settings.use_conversations_api:
        return
    if context.openai_conversation_id or context.previous_response_id:
        return
    context._conversation_creation_task = asyncio.create_task(_create_conversation(context))


async def ensure_conversation_id(context: SessionScope) -> str | None:
    """Return the conversation ID, awaiting the background task if one is pending.

    If start_conversation_creation was called earlier, this awaits that task.
    Otherwise it creates the conversation synchronously (fallback for paths
    that didn't call start_conversation_creation, e.g. voice thinker).
    """
    if not settings.use_conversations_api:
        return None

    if context.openai_conversation_id:
        return context.openai_conversation_id

    # Don't switch an in-flight legacy session to conversations mid-conversation
    if context.previous_response_id:
        logger.info("Skipping conversation creation for in-flight legacy session")
        return None

    # Await background task if one was started
    task = getattr(context, "_conversation_creation_task", None)
    if task is not None:
        return await task

    # Fallback: create synchronously (e.g. voice thinker on first call)
    return await _create_conversation(context)


def save_conversation_id(headers: dict, context: SessionScope) -> None:
    """Add the conversation ID to the response headers if available."""
    if context.openai_conversation_id:
        headers[CONVERSATION_ID_HEADER] = context.openai_conversation_id


_ORPHAN_FUNCTION_CALL_ERROR_FRAGMENT = "No tool output found for function call"
_ORPHAN_CLEANUP_TIMEOUT_S = 3.0


def is_orphan_function_call_error(exc: BaseException) -> bool:
    """Detect the 400 OpenAI returns when a server-side conversation contains
    a function_call without a matching function_call_output (Issue #1569).

    OpenAI does not expose a machine-readable error code for this case; we
    match on status, error type, and the message fragment.
    """
    if not isinstance(exc, BadRequestError):
        return False
    if exc.status_code != 400 or exc.type != "invalid_request_error":
        return False
    return _ORPHAN_FUNCTION_CALL_ERROR_FRAGMENT in str(exc)


async def _delete_one_orphan(client, conversation_id: str, orphan) -> bool:
    try:
        await client.conversations.items.delete(conversation_id=conversation_id, item_id=orphan.id)
        logger.info(
            "Deleted orphan function_call from OpenAI conversation",
            conversation_id=conversation_id,
            item_id=orphan.id,
            call_id=getattr(orphan, "call_id", None),
            tool_name=getattr(orphan, "name", None),
        )
        return True
    except Exception as e:
        logger.warning(
            "Failed to delete orphan function_call",
            conversation_id=conversation_id,
            item_id=orphan.id,
            call_id=getattr(orphan, "call_id", None),
            error=str(e),
        )
        return False


async def clean_orphan_function_calls(conversation_id: str, scan_limit: int = 20) -> int:
    """Tail-scan a server-side OpenAI conversation and delete any function_call
    items that lack a matching function_call_output.

    Recovery path for Issue #1569. When an input guardrail trips while the model
    task is concurrently running with conversation_id set, OpenAI persists the
    model's emitted function_call but no function_call_output follows. Every
    subsequent turn chained on that conversation 400s.

    Bounded by ``_ORPHAN_CLEANUP_TIMEOUT_S`` so a slow OpenAI never stalls a
    guardrail-trip response. Layer 2 recovery is the backstop if cleanup fails
    or times out. Returns the number of orphan items deleted.
    """
    if not conversation_id:
        return 0

    from agent_leasing.clients.openai import get_openai_client

    client = get_openai_client()

    async def _do_cleanup() -> int:
        items = [
            item async for item in client.conversations.items.list(conversation_id, limit=scan_limit, order="desc")
        ]
        if not items:
            return 0

        output_call_ids = {getattr(item, "call_id", None) for item in items if item.type == "function_call_output"}
        orphans = [
            item
            for item in items
            if item.type == "function_call" and getattr(item, "call_id", None) not in output_call_ids
        ]
        if not orphans:
            return 0

        results = await asyncio.gather(
            *(_delete_one_orphan(client, conversation_id, orphan) for orphan in orphans),
            return_exceptions=False,
        )
        return sum(1 for ok in results if ok)

    try:
        return await asyncio.wait_for(_do_cleanup(), timeout=_ORPHAN_CLEANUP_TIMEOUT_S)
    except TimeoutError:
        logger.warning(
            "Orphan cleanup timed out; relying on Layer 2 recovery",
            conversation_id=conversation_id,
            timeout_s=_ORPHAN_CLEANUP_TIMEOUT_S,
        )
        return 0


async def cleanup_orphan_after_guardrail_trip(exc: BaseException, conversation_id: str | None, *, site: str) -> None:
    """Layer 1 entry point used by every guardrail handler. No-op unless the
    guardrail was an input tripwire and a server-side conversation is active.
    Output guardrails run after the conversation is already consistent.
    """
    if not isinstance(exc, InputGuardrailTripwireTriggered):
        return
    if not conversation_id:
        return
    try:
        await clean_orphan_function_calls(conversation_id)
    except Exception as cleanup_exc:
        logger.warning(
            "Orphan cleanup failed after guardrail trip",
            site=site,
            error=str(cleanup_exc),
        )


async def reset_and_create_fresh_conversation(context: SessionScope) -> str | None:
    """Clear the corrupted conversation_id and previous_response_id on the
    context, then create a fresh conversation (Issue #1569).

    Both fields point into the corrupted conversation and would 400 if forwarded
    on the next chained call. Other code paths (e.g. facilities API tool headers
    at api_call.py:148) read these from the context.
    """
    context.openai_conversation_id = None
    context.previous_response_id = None
    return await _create_conversation(context)


async def run_agent_with_orphan_recovery(
    agent_instance: Any,
    *,
    context: SessionScope,
    conversation_id: str | None,
    openai_metadata: dict[str, Any] | None = None,
    **runner_kwargs: Any,
) -> RunResult:
    """Runner.run wrapper that recovers from a corrupted server-side conversation.

    Issue #1569 (Layer 2 — fallback): if the OpenAI conversation contains an
    orphaned function_call, OpenAI returns 400 "No tool output found ...".
    Without recovery, every subsequent turn for that resident permanently
    returns the fallback string. This wrapper catches that specific error,
    creates a fresh conversation, and retries the agent run once.

    Layer 1 (preserve history) lives in the InputGuardrailTripwireTriggered
    handler at the call site via ``cleanup_orphan_after_guardrail_trip``.

    ``runner_kwargs`` are forwarded to ``Runner.run`` on both attempts EXCEPT
    ``previous_response_id`` and ``conversation_id`` — the wrapper manages those.
    """
    previous_response_id = runner_kwargs.pop("previous_response_id", None)
    runner_kwargs.pop("conversation_id", None)
    runner_kwargs.setdefault("context", context)

    try:
        return await Runner.run(
            agent_instance,
            previous_response_id=previous_response_id if not conversation_id else None,
            conversation_id=conversation_id,
            **runner_kwargs,
        )
    except BadRequestError as exc:
        if not is_orphan_function_call_error(exc):
            raise
        logger.warning(
            "OpenAI conversation has orphan function_call; resetting and retrying with fresh conversation",
            corrupted_conversation_id=conversation_id,
            error=str(exc),
        )
        new_conversation_id = await reset_and_create_fresh_conversation(context) if conversation_id else None
        if new_conversation_id:
            structlog.contextvars.bind_contextvars(openai_conversation_id=new_conversation_id)
            if openai_metadata is not None:
                openai_metadata["openai-conversation-id"] = new_conversation_id
        return await Runner.run(
            agent_instance,
            previous_response_id=None,
            conversation_id=new_conversation_id,
            **runner_kwargs,
        )


class AgentRequest:
    """Container for agent request components."""

    def __init__(
        self,
        trace_id: str,
        language_code: str,
        workflow_name: str,
        flows: list[Flow],
        logging_metadata: list,
        group_id: str,
        headers: dict[str, str],
        context: SessionScope,
        thread_id: str,
        previous_response_id: str | None,
        agent: Any,
        metadata: dict[str, Any],
        start_time: float,
        expire: str = "10m",
    ):
        self.trace_id = trace_id
        self.language_code = language_code
        self.workflow_name = workflow_name
        self.flows = flows
        self.logging_metadata = logging_metadata
        self.group_id = group_id
        self.headers = headers
        self.context = context
        self.thread_id = thread_id
        self.previous_response_id = previous_response_id
        self.agent = agent
        self.metadata = metadata
        self.start_time = start_time
        self.expire = expire


async def build_agent_request(req: AskRequest) -> AgentRequest:
    """
    Set up common components for agent requests (both streaming and non-streaming).

    This function handles all the initialization steps up to (and including) llm_message.post(),
    which is common between agent_ask and agent_stream endpoints.

    Args:
        req: The request containing all necessary information for agent interaction

    Returns:
        AgentRequest containing all initialized components needed for agent execution

    Raises:
        UnsupportedAgentException: If the requested agent product is not supported
    """
    # Sanitize input early, before any logging or tracing captures raw input
    req.prompt = sanitize_input(req.prompt)

    start_time = time.time()
    start_time_iso = datetime.datetime.fromtimestamp(start_time, tz=datetime.UTC).isoformat()

    language_code = "en"

    workflow_name = req.product.upper()
    flows = [Flow(name=workflow_name)]
    logging_metadata = []
    group_id = req.chat_session_id if req.chat_session_id else gen_group_id()

    # Get context from memory
    context = await memory.get_context(memory.context_cache_key(req))
    # If thread ID is in the context use it; otherwise create one
    thread_id = context.thread_id if context else str(uuid.uuid4())
    trace_id = gen_trace_id()

    structlog.contextvars.bind_contextvars(
        openai_trace_id=trace_id,
        chat_session_id=req.chat_session_id,
        product=req.product,
        uc_property_id=getattr(req.product_info.uc_property_id, "id", None),
        knock_property_id=req.product_info.knock_property_id,
        knock_resident_id=req.product_info.knock_resident_id,
    )

    logger.debug(f"Generated trace_id: {trace_id}")

    # Make headers & langsmith_trace_url available to both try and except branches
    headers = {TRACE_ID_HEADER: trace_id}

    if not context:
        context = SessionScope(ask_request=req, thread_id=thread_id, openai_trace_id=trace_id)
        logger.info(f"Created new context for chat_session_id: {req.chat_session_id}")
        # Resident AI is picking up a brand-new conversation (Redis cache
        # miss). Per spec, emit IN_PROGRESS exactly once per non-voice
        # session at first turn — subsequent turns hit the cached SessionScope
        # branch below and don't re-emit. Voice paths construct SessionScope
        # in their own handlers (twilio_handler / voice/handler), so this
        # injection only affects /v1/agent/ask and /v1/agent/stream.
        publish_task_event_fire_and_forget(
            kafka_application_context.task_event_producer,
            build_in_progress_event(context),
            context.pending_activity_publishes,
        )
    else:
        # Update parts of session context pulled from memory
        context.ask_request = req
        context.ask_request.product = req.product
        context.ask_request.prompt = req.prompt
        logger.info(f"Found context in memory for chat_session_id: {req.chat_session_id}")
        context.reset()

    # Start conversation creation early so it runs in parallel with agent setup
    start_conversation_creation(context)

    # Log the input and the whole payload here (once)
    logger.info(
        f"Input: {req.prompt}",
        event_type="call_entry",
        channel=req.product,
        payload=req.model_dump(),
    )

    # Collect previous_response_id so it can be passed to a runner to support OpenAI message history
    previous_response_id = None
    # If previous_response_id is in memory, use it
    if context.previous_response_id:
        previous_response_id = context.previous_response_id

    context.logging_metadata = {}
    logger.debug(f"Using previous_response_id: {previous_response_id} for chat_session_id: {req.chat_session_id}")

    logger.debug(f"Context loaded for thread_id: {context.thread_id}, chat_session_id: {req.chat_session_id}")

    structlog.contextvars.bind_contextvars(persona=context.persona)

    agent = agent_selector(req.product, context)

    channel = get_channel_from_context(context).lower()

    cache_expiration = settings.cache_expiration(channel)

    # Add metadata to trace
    metadata = {
        "environment": settings.environment,
        "chat-session-id": req.chat_session_id,
        "property-id": req.property_id,
        "resident-id": req.product_info.knock_resident_id,
        "company-id": req.product_info.uc_company_id.id if req.product_info.uc_company_id else None,
        "product": req.product,
        "agent": agent.name,
        "thread-id": thread_id,
        "property-name": req.product_info.property_name,
        "input": req.prompt[:512] if req.prompt else "",
        "start-time": start_time_iso,
        "call-sid": req.product_info.call_sid,
        "pmc-id": req.product_info.pmc_id,
        "pmc-name": req.product_info.pmc_name,
        "request-id": str(uuid.uuid4()),
        "openai-trace-id": trace_id,
        "openai-conversation-id": context.openai_conversation_id,
    }

    return AgentRequest(
        trace_id=trace_id,
        language_code=language_code,
        workflow_name=workflow_name,
        flows=flows,
        logging_metadata=logging_metadata,
        group_id=group_id,
        headers=headers,
        context=context,
        thread_id=thread_id,
        previous_response_id=previous_response_id,
        agent=agent,
        metadata=metadata,
        start_time=start_time,
        expire=cache_expiration,
    )
