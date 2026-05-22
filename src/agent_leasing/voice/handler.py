"""VoiceHandler — thin orchestrator that wires all voice components together.

Implements ``VoiceCallbacks`` by delegating to the appropriate components.
Manages the concurrent loops (transport events, session events, pacer,
buffer flush, inactivity monitor).

``VoiceHandlerManager`` is a lightweight registry of active handlers,
matching ``TwilioWebSocketManager``'s API.
"""

from __future__ import annotations

import asyncio
import datetime
import time
from contextlib import nullcontext
from typing import Any

import langsmith as ls
import pydantic
import structlog
from agents import ModelBehaviorError, trace
from agents.realtime import (
    RealtimeModelExceptionEvent,
    UserMessageItem,
)
from agents.tracing import get_current_trace
from starlette.websockets import WebSocket

from agent_leasing.agent.resident_one_agent.agent import (
    ensure_disabled_modules_and_tools_loaded,
)
from agent_leasing.agent.resident_one_agent.realtime import (
    build_parallel_greeting_agent,
)
from agent_leasing.agent.util import SessionScope
from agent_leasing.api.model import AskRequest
from agent_leasing.kafka.fire_and_forget import drain_pending_publishes
from agent_leasing.kafka.kafka_context import kafka_application_context
from agent_leasing.kafka.task_event import (
    build_end_of_session_event,
    build_in_progress_event,
    publish_task_event_fire_and_forget,
)
from agent_leasing.settings import settings
from agent_leasing.util.realtime_util import realtime_history_to_input_list
from agent_leasing.util.tracing_utils import (
    DeferredSpanTree,
    build_openai_group_url,
    build_openai_trace_url,
    get_langsmith_trace_url,
    post_trace_marker,
    record_initial_greeting_latency,
)
from agent_leasing.voice.agent import VoiceAgent
from agent_leasing.voice.audio.buffer import AudioBuffer
from agent_leasing.voice.audio.pacer import AudioChunk, AudioPacer
from agent_leasing.voice.audio.playback import MarkData, PlaybackTracker
from agent_leasing.voice.config import VoiceConfig, voice_config_from_settings
from agent_leasing.voice.coordination.call_state import VoiceCallState
from agent_leasing.voice.coordination.event_dispatcher import EventDispatcher
from agent_leasing.voice.coordination.interaction_policy import (
    DefaultPolicy,
    GreetingPolicy,
    InteractionPolicy,
)
from agent_leasing.voice.coordination.interrupt import InterruptHandler
from agent_leasing.voice.filler.manager import FillerManager
from agent_leasing.voice.lifecycle.cleanup import cancel_tasks, close_with_timeout
from agent_leasing.voice.lifecycle.data_curation import await_data_curation, schedule_data_curation
from agent_leasing.voice.lifecycle.recording import start_recording
from agent_leasing.voice.lifecycle.setup import (
    transfer_call_on_init_failure,
    transfer_call_on_validation_failure,
)
from agent_leasing.voice.session.manager import SessionManager
from agent_leasing.voice.session.race_recovery import recover_from_active_response_race
from agent_leasing.voice.session.recovery import recover_session
from agent_leasing.voice.session.response_gate import ResponseGate
from agent_leasing.voice.tracing.langsmith import VoiceTracer
from agent_leasing.voice.transport.twilio import TwilioTransport
from agent_leasing.voice.transport.types import TransportEventType
from agent_leasing.voice.vad.openai_vad import OpenAIVAD

logger = structlog.get_logger(__name__)


def _has_task_event_context(ctx: SessionScope | None) -> bool:
    """Task events require ask_request because task.id is derived from it."""
    return bool(ctx and ctx.ask_request)


VOICE_STARTUP_PHASES: list[tuple[str, str, str]] = [
    ("process_start_payload", "start_event_received", "start_payload_processed"),
    ("prepare_greeting_context", "prepare_greeting_context_start", "prepare_greeting_context_end"),
    ("create_session", "create_session_start", "create_session_end"),
    ("session_enter", "session_enter_start", "session_enter_end"),
    ("trigger_greeting", "trigger_greeting_start", "trigger_greeting_end"),
    ("first_audio_received", "trigger_greeting_end", "first_audio_received"),
    ("first_utterance_sent", "first_audio_received", "first_utterance_sent"),
]

VOICE_PARALLEL_INIT_PHASES: list[tuple[str, str, str]] = [
    ("full_init", "agent_init_start", "agent_init_end"),
    ("agent_swap", "agent_swap_start", "agent_swap_end"),
]


ACTIVE_RESPONSE_RACE_ERROR_CODE = "conversation_already_has_active_response"
ACTIVE_RESPONSE_RACE_ERROR_SUBSTRING = "already has an active response in progress"


def _inner_error(event: Any) -> Any:
    """Return the inner error/exception payload from a session error event.

    ``_handle_session_error`` is invoked with either a ``RealtimeError``
    (``.error`` attribute, holding the OpenAI realtime ``Error`` model with
    a typed ``.code``) or a raw_model_event whose ``.data`` is a
    ``RealtimeModelExceptionEvent`` (``.exception`` attribute, holding a
    Python ``Exception``).
    """
    for attr in ("error", "exception"):
        val = getattr(event, attr, None)
        if val is not None:
            return val
    data = getattr(event, "data", None)
    if data is not None:
        for attr in ("error", "exception"):
            val = getattr(data, attr, None)
            if val is not None:
                return val
    return event


def _is_active_response_race(event: Any) -> bool:
    """True if *event* is the recoverable "active response in progress" race.

    Prefers the typed ``code`` field on the OpenAI realtime ``Error`` model;
    falls back to a substring match for shapes that surface only a message
    string (e.g. ``RealtimeModelExceptionEvent``).
    """
    inner = _inner_error(event)
    if getattr(inner, "code", None) == ACTIVE_RESPONSE_RACE_ERROR_CODE:
        return True
    return ACTIVE_RESPONSE_RACE_ERROR_SUBSTRING in str(inner)


def _log_task_exception(task: asyncio.Task[None]) -> None:
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc:
        logger.warning(f"Background task failed: {exc}")


class VoiceHandler:
    """Orchestrates a single voice call.

    All complexity lives in the components.  The handler's job is:
    1. Create and wire components
    2. Run concurrent loops
    3. Implement VoiceCallbacks (delegating to components)
    4. Handle top-level errors
    """

    def __init__(self, transport: TwilioTransport, config: VoiceConfig) -> None:
        self.transport = transport
        self.config = config
        self.call_active = True

        # Core components
        self.call_state = VoiceCallState()
        self.session_manager = SessionManager(config)
        self.response_gate = ResponseGate()
        self.playback = PlaybackTracker()
        self.pacer = AudioPacer(
            config, send_frame=self._send_frame_marked, send_mark=transport.request_playback_notification
        )
        self.buffer = AudioBuffer(config, send_audio=self.session_manager.send_audio)
        self.filler = FillerManager(config, self.session_manager, self.call_state)
        self.tracer = VoiceTracer()
        self.interrupt_handler = InterruptHandler(
            pacer=self.pacer,
            transport=self.transport,
            playback=self.playback,
            session_manager=self.session_manager,
            call_state=self.call_state,
            filler=self.filler,
            response_gate=self.response_gate,
        )
        self.dispatcher = EventDispatcher()
        self.vad = OpenAIVAD(config)
        self.policy: InteractionPolicy = DefaultPolicy()

        # Agent (set during setup)
        self.voice_agent: VoiceAgent | None = None
        self.ctx: SessionScope | None = None

        # Background tasks
        self._pacer_task: asyncio.Task[None] | None = None
        self._buffer_task: asyncio.Task[None] | None = None
        self._session_loop_task: asyncio.Task[None] | None = None
        self._inactivity_task: asyncio.Task[None] | None = None
        self._recording_task: asyncio.Task[None] | None = None
        self._data_curation_task: asyncio.Task[None] | None = None
        self._langsmith_url_task: asyncio.Task[None] | None = None
        # Greeting-agent fast path: full agent init runs here while the
        # greeting agent handles the first turn.  None when sequential.
        self._full_agent_task: asyncio.Task[None] | None = None

        # Tracing
        self.root_run: ls.RunTree | None = None
        self._session_start_time: float | None = None
        self._shutdown_reason: str | None = None
        self._cleanup_called = False
        self._startup_span = DeferredSpanTree("welcome_agent_init", VOICE_STARTUP_PHASES)
        self._parallel_init_span: DeferredSpanTree | None = None
        self._initial_greeting_latency_recorded: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start processing the voice call.

        Wraps the entire call in a LangSmith trace (matching the original's
        ``_twilio_message_loop``). Iterates transport events; the STARTED event
        triggers agent setup, session creation, and the background loops.
        """
        # Lazy import — Product triggers loading the examples module which is heavy
        from agent_leasing.api.model import Product

        with ls.trace(
            project_name=f"{settings.environment}_renter_ai_resident_voice",
            name=Product.RESIDENT_ONE_VOICE.value,
            run_type="chain",
        ) as run:
            self.root_run = run
            try:
                async for event in self.transport.receive_events():
                    if event.type == TransportEventType.CONNECTED:
                        logger.debug("Voice transport connected")

                    elif event.type == TransportEventType.STARTED:
                        await self._on_transport_started()

                    elif event.type == TransportEventType.AUDIO_RECEIVED:
                        await self._on_audio_received(event.data.get("audio", b""))

                    elif event.type == TransportEventType.PLAYBACK_MILESTONE:
                        await self._on_playback_milestone(event.data.get("notification_id", ""))

                    elif event.type == TransportEventType.STOPPED:
                        self.call_active = False
                        self._shutdown_reason = self._shutdown_reason or "transport_stopped"
                        break
            except asyncio.CancelledError:
                logger.debug("Voice transport loop cancelled")
            except Exception as e:
                logger.warning(f"Voice transport loop error: {e}", exc_info=True)
            finally:
                # Always clean up — _cleanup_called guard prevents double-cleanup.
                # This must run on STOPPED too (call_active is already False),
                # otherwise background tasks (session loop, tools) keep running
                # and the model can surface errors to the caller after disconnect.
                await self._cleanup_call()

    async def wait_until_done(self) -> None:
        """No-op — ``start()`` blocks until the call ends."""

    async def _cleanup_call(self) -> None:
        """Ordered teardown sequence."""
        if self._cleanup_called:
            return
        self._cleanup_called = True
        logger.info("Voice cleaning up call")

        # End-of-session task event: PENDING + escalation when handoff routing
        # could not be confirmed; COMPLETED (with or without escalation) otherwise.
        # Scheduled before teardown so a failure in teardown doesn't swallow the
        # event. Only fires if ctx was created (skips early-exit paths like
        # payload validation failure). Fire-and-forget — actual publish runs in
        # a background task drained at the end of cleanup.
        if _has_task_event_context(self.ctx):
            publish_task_event_fire_and_forget(
                kafka_application_context.task_event_producer,
                build_end_of_session_event(self.ctx),
                self.ctx.pending_activity_publishes,
            )

        # Trace shutdown reason
        if self._shutdown_reason and self.root_run:
            inputs: dict[str, Any] = {}
            if self.filler.consecutive_fillers_without_user_audio > 0:
                inputs["consecutive_fillers"] = self.filler.consecutive_fillers_without_user_audio
                inputs["max_allowed"] = self.config.max_consecutive_fillers_without_user_audio
            post_trace_marker(
                self.root_run,
                self._shutdown_reason,
                inputs=inputs,
                message=f"Call terminated: {self._shutdown_reason}",
            )

        # Schedule data curation before closing session
        if self.session_manager.session:
            self._data_curation_task = await schedule_data_curation(
                session=self.session_manager.session,
                transcript_cache=self.session_manager.transcript_cache,
            )

        # Deactivate
        self.call_active = False
        self.filler.reset()
        self.call_state.reset()
        self.pacer.stop()
        self.buffer.stop()

        # Cancel background tasks
        await cancel_tasks(
            [
                ("pacer", self._pacer_task),
                ("buffer", self._buffer_task),
                ("session_loop", self._session_loop_task),
                ("inactivity", self._inactivity_task),
                ("recording", self._recording_task),
                ("langsmith_url", self._langsmith_url_task),
                ("full_agent_init", self._full_agent_task),
            ]
        )

        # Finalize deferred startup spans (welcome_agent_init + full_agent_init_parallel).
        # Must happen before _finalize_tracing so message trace posts aren't reordered.
        await self._startup_span.finalize(self.root_run)
        if self._parallel_init_span:
            await self._parallel_init_span.finalize(self.root_run)

        # Finalize tracing
        await self.dispatcher.shutdown()
        history = self._get_final_history()
        await self.tracer.finalize(
            history=history,
            root_run=self.root_run,
            message_start_times=self.playback.message_start_times,
            message_end_times=self.playback.message_end_times,
            filler_item_ids=self.filler.filler_item_ids,
            rendered_system_prompt=getattr(self.ctx, "rendered_system_prompt", None) if self.ctx else None,
        )

        # Clear audio state
        self.pacer.clear()
        self.buffer.clear()
        self.playback.reset()

        # Close session
        await close_with_timeout(self.session_manager.close(), timeout=1.5, label="session")

        # Close agent
        if self.voice_agent:
            await close_with_timeout(self.voice_agent.cleanup(), timeout=5.0, label="agent")

        # Clear context circular references
        if self.ctx:
            self.ctx.history = []
            self.ctx.mcp_tool_calls = []
            self.ctx.call_state_manager = None

        # Await data curation
        await await_data_curation(self._data_curation_task)

        # Drain any in-flight task-event publishes scheduled during the call
        # (IN_PROGRESS at start + end-of-session above). Awaited last so the
        # background tasks have the maximum window to complete before this
        # coroutine returns and the WebSocket teardown finishes.
        if _has_task_event_context(self.ctx):
            await drain_pending_publishes(self.ctx.pending_activity_publishes)

        logger.info("Voice cleanup complete")

    # ------------------------------------------------------------------
    # Transport event handlers
    # ------------------------------------------------------------------

    async def _on_transport_started(self) -> None:
        """Handle the STARTED event — set up agent, session, and background loops."""
        self._startup_span.mark("start_event_received")
        payload = self.transport.payload
        call_sid = self.transport.call_metadata.call_sid

        if not payload:
            logger.error("Voice: no payload in start event")
            self.call_active = False
            return

        # Validate payload
        try:
            ask_request = AskRequest(**payload)
        except (pydantic.ValidationError, ValueError) as e:
            await transfer_call_on_validation_failure(
                e,
                payload,
                call_sid,
                root_run=self.root_run,
                variant=getattr(self, "variant", "v2"),
            )
            self.call_active = False
            return
        self._startup_span.mark("start_payload_processed")

        # Add early tracing metadata to root_run
        trace_id = self.session_manager.trace_id
        group_id = self.session_manager.group_id
        if self.root_run:
            self.root_run.add_metadata(
                {
                    "openai_trace_id": trace_id,
                    "openai_group_id": group_id,
                    "voice_handler_variant": getattr(self, "variant", "v2"),
                }
            )

        # Set up structured logging context
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            openai_trace_id=trace_id,
            chat_session_id=ask_request.chat_session_id,
            product=ask_request.product,
            call_sid=call_sid,
        )

        # Create SessionScope with LangSmith linkage
        self.ctx = SessionScope(
            ask_request=ask_request,
            langsmith_run_tree=self.root_run.to_headers() if self.root_run else None,
        )
        self.ctx.call_state_manager = self.call_state
        # Direct root_run reference so the shared agent prompt logger
        # (``_log_voice_prompt_trace`` in ``resident_one_agent/agent.py``) can
        # post ChatPromptTemplate child runs without the legacy
        # ``ctx._session_handler`` back-reference.
        self.ctx.root_run = self.root_run
        self._session_start_time = time.time()

        logger.info(
            "Setting up real-time agent",
            event_type="call_entry",
            channel="resident_one_voice",
            payload=payload,
        )

        # Set tracing URLs on ctx (used by thinker tool and Kafka curation)
        self.ctx.openai_trace_url = build_openai_trace_url(trace_id=trace_id)
        self.ctx.openai_group_id = group_id
        self.ctx.openai_group_url = build_openai_group_url(group_id=group_id)
        logger.info(f"Open AI Trace: {self.ctx.openai_trace_url}")
        logger.info(f"Open AI Group: {self.ctx.openai_group_url}")
        self._langsmith_url_task = asyncio.create_task(self._resolve_langsmith_url(), name="resolve-langsmith-url")
        self._langsmith_url_task.add_done_callback(_log_task_exception)

        # Resident AI has picked up the conversation — publish IN_PROGRESS event.
        # Fire-and-forget to keep the voice hot path off the publish thread.
        if _has_task_event_context(self.ctx):
            publish_task_event_fire_and_forget(
                kafka_application_context.task_event_producer,
                build_in_progress_event(self.ctx),
                self.ctx.pending_activity_publishes,
            )

        # Start recording
        self._recording_task = await start_recording(payload, call_sid)

        # Start pacer immediately (silence keeps Twilio healthy)
        self._pacer_task = asyncio.create_task(self.pacer.run(), name="voice_pacer")
        self._pacer_task.add_done_callback(_log_task_exception)

        # Start buffer flush loop
        self._buffer_task = asyncio.create_task(self.buffer.run(), name="voice_buffer")
        self._buffer_task.add_done_callback(_log_task_exception)

        # Start conversation creation in background
        # Lazy import — avoids circular import with agent_service → settings
        from agent_leasing.services.agent_service import start_conversation_creation

        start_conversation_creation(self.ctx)

        # Create voice agent
        self.policy = GreetingPolicy()
        self.buffer.suppress_flush = True  # Suppress during greeting

        self.voice_agent = VoiceAgent(self.ctx, self, self.call_state, self.config)

        # Build metadata and add to LangSmith root run
        metadata = self._build_metadata(ask_request)
        if self.root_run:
            from agent_leasing.util.tracing_utils import normalize_metadata_keys

            self.root_run.add_metadata(normalize_metadata_keys(metadata))

        # Greeting-agent fast path: start the call with a lightweight agent so
        # the caller hears the greeting while the full agent (LDP + MCP + prefetch)
        # initialises in the background.  The full agent is swapped in when the
        # greeting finishes playing (see _on_response_completed).
        #
        # The agents-SDK ``trace()`` context manager is opened here so that any
        # spans recorded during agent setup (MCP connect, prompt rendering,
        # session creation) are attached to the call's trace.  Background tasks
        # created inside this block (``_init_full_voice_agent``) inherit the
        # contextvar via ``asyncio.create_task``'s context snapshot.
        with trace(
            workflow_name="Resident One Voice",
            trace_id=trace_id,
            group_id=group_id,
        ):
            if self.config.greeting_agent_enabled:
                self._parallel_init_span = DeferredSpanTree("full_agent_init_parallel", VOICE_PARALLEL_INIT_PHASES)
                self._startup_span.mark("prepare_greeting_context_start")
                with self._startup_span.attach(self.root_run):
                    await ensure_disabled_modules_and_tools_loaded(self.ctx)
                self._startup_span.mark("prepare_greeting_context_end")
                greeting_agent = build_parallel_greeting_agent(self.ctx)
                self._full_agent_task = asyncio.create_task(
                    self._init_full_voice_agent(trace_id=trace_id, group_id=group_id),
                    name="voice_full_agent_init",
                )
                self._full_agent_task.add_done_callback(_log_task_exception)
                session_agent = greeting_agent
            else:
                with self._startup_span.attach(self.root_run):
                    session_agent = await self.voice_agent.setup()

            self._startup_span.mark("create_session_start")
            await self.session_manager.create(
                agent=session_agent,
                context=self.ctx,
                metadata=metadata,
            )
            self._startup_span.mark("create_session_end")

            self._startup_span.mark("session_enter_start")
            await self.session_manager.enter()
            self._startup_span.mark("session_enter_end")

        # Wire playback inject so tools can prompt the model to speak
        self.call_state._send_message_fn = self.session_manager.send_message

        # Register event handlers and start session event loop
        self._register_event_handlers()
        self.dispatcher.start()
        self._session_loop_task = asyncio.create_task(self._session_event_loop(), name="voice_session")
        self._session_loop_task.add_done_callback(_log_task_exception)

        # Start inactivity monitor
        self._inactivity_task = asyncio.create_task(self._inactivity_loop(), name="voice_inactivity")
        self._inactivity_task.add_done_callback(_log_task_exception)

        # Trigger greeting
        self._startup_span.mark("trigger_greeting_start")
        await self._trigger_greeting()
        self._startup_span.mark("trigger_greeting_end")
        self.filler.schedule()

    async def _send_frame_marked(self, audio: bytes) -> None:
        # Gate on first_audio_received so silence frames sent before the greeting
        # arrives don't trip first_utterance_sent — which would precede
        # first_audio_received and yield a negative-duration phase span.
        if self._startup_span.has_mark("first_audio_received"):
            self._startup_span.mark("first_utterance_sent")
            if not self._initial_greeting_latency_recorded:
                if record_initial_greeting_latency(self.root_run, self._startup_span) is not None:
                    self._initial_greeting_latency_recorded = True
        await self.transport.send_audio(audio)

    async def _on_audio_received(self, audio: bytes) -> None:
        """Handle inbound audio from the transport."""
        if not audio:
            return
        should_flush = self.buffer.append(audio)
        if should_flush:
            await self.buffer.flush()
        # Reschedule filler when user is actually speaking (VAD confirmed)
        if self.call_state.is_user_speaking:
            self.filler.schedule()

    async def _on_playback_milestone(self, notification_id: str) -> None:
        """Handle a mark played back by the transport."""
        await self.playback.on_mark_played(notification_id)
        self.filler.schedule()

    # ------------------------------------------------------------------
    # Session event handlers (registered on EventDispatcher)
    # ------------------------------------------------------------------

    def _register_event_handlers(self) -> None:
        """Wire session event types to their handlers on the dispatcher."""
        self.dispatcher.register("audio", self._handle_session_audio)
        self.dispatcher.register("audio_interrupted", self._handle_session_interrupted)
        self.dispatcher.register("audio_end", self._handle_session_audio_end)
        self.dispatcher.register("history_updated", self._handle_history_updated)
        self.dispatcher.register("guardrail_tripped", self._handle_guardrail_tripped)
        self.dispatcher.register("raw_model_event", self._handle_raw_model_event)
        self.dispatcher.register("agent_end", self._handle_agent_end)
        self.dispatcher.register("error", self._handle_session_error)

    async def _handle_session_audio(self, event: Any) -> None:
        """Handle audio from OpenAI — enqueue on pacer, update state."""
        audio_data = event.audio.data
        if not audio_data:
            return

        self._startup_span.mark("first_audio_received")

        # Clear user speaking flag (fallback for missed history_updated)
        self.call_state.is_user_speaking = False

        item_id = event.audio.item_id
        content_index = event.audio.content_index

        # First audio for this item — update state
        if not self.call_state.is_agent_speaking:
            is_filler = self.filler.next_speech_is_filler
            self.filler.next_speech_is_filler = False
            if is_filler:
                self.filler.mark_filler_item(item_id)
            self.call_state.mark_agent_speaking_started(is_filler=is_filler)
            self.playback.record_start_time(item_id)

        # Enqueue audio on pacer + register mark on playback tracker
        chunk = AudioChunk(audio=audio_data, item_id=item_id, content_index=content_index)
        mark_id = self.pacer.enqueue(chunk)
        if mark_id:
            self.playback.register_mark(
                mark_id,
                MarkData(
                    item_id=item_id,
                    content_index=content_index,
                    byte_count=len(audio_data),
                ),
            )

    async def _handle_session_interrupted(self, event: Any) -> None:
        """Handle audio_interrupted — delegate to InterruptHandler."""
        await self.interrupt_handler.handle_interrupt(self.policy)
        # Fire trace for interrupted items
        self.tracer.fire_trace_task(
            history=self._get_trace_history(),
            root_run=self.root_run,
            message_start_times=self.playback.message_start_times,
            message_end_times=self.playback.message_end_times,
            filler_item_ids=self.filler.filler_item_ids,
            rendered_system_prompt=getattr(self.ctx, "rendered_system_prompt", None) if self.ctx else None,
        )

    async def _handle_session_audio_end(self, event: Any) -> None:
        """Handle audio_end — flush partial frame, reschedule filler."""
        self.pacer.flush_partial()
        self.filler.schedule()

    async def _handle_history_updated(self, event: Any) -> None:
        """Handle history_updated — update history, track user messages."""
        if not self.ctx:
            return
        self.ctx.history = realtime_history_to_input_list(
            event.history, transcript_cache=self.session_manager.transcript_cache
        )
        self.session_manager.on_history_updated(
            realtime_history_to_input_list(
                event.history, include_item_id=True, transcript_cache=self.session_manager.transcript_cache
            )
        )
        for item in event.history:
            if not isinstance(item, UserMessageItem) or item.role != "user":
                continue
            # Record start time from VAD timestamp
            if item.item_id not in self.playback.message_start_times:
                self.playback.message_start_times[item.item_id] = (
                    self.call_state.last_user_speaking_started_at or datetime.datetime.now(datetime.UTC)
                )
            if getattr(item, "status", None) == "completed":
                # Record end time from VAD timestamp
                started, stopped = self.call_state.consume_user_speaking_timestamps()
                if stopped and item.item_id not in self.playback.message_end_times:
                    self.playback.message_end_times[item.item_id] = stopped
                self.call_state.mark_user_speaking_stopped()
                self.call_state.mark_agent_processing_started()
                self.filler.schedule()
                # Trace immediately so HumanMessage spans reflect actual speech
                # duration rather than the full pipeline time waited until agent_end.
                # The tracer dedups via _viewed_messages — agent_end won't re-trace.
                self.tracer.fire_trace_task(
                    history=self._get_trace_history(),
                    root_run=self.root_run,
                    message_start_times=self.playback.message_start_times,
                    message_end_times=self.playback.message_end_times,
                    filler_item_ids=self.filler.filler_item_ids,
                    rendered_system_prompt=getattr(self.ctx, "rendered_system_prompt", None) if self.ctx else None,
                )

    async def _handle_guardrail_tripped(self, event: Any) -> None:
        """Handle guardrail_tripped — send guardrail message to session.

        Runs in a fire-and-forget task so the 1s pause doesn't block
        the deferred event queue.  The task is not tracked for cancellation
        because ``session_manager.send_message`` already no-ops when the
        session is None — if the call ends during the 1s sleep, the send
        is harmlessly skipped.
        """
        from agent_leasing.voice.filler.messages import GUARDRAIL_TRIPPED_MESSAGE

        guardrail_message = getattr(event, "message", "Unknown guardrail violation")
        language_code = getattr(self.ctx, "conversation_language", "en") if self.ctx else "en"
        msg = GUARDRAIL_TRIPPED_MESSAGE.format(guardrail_message=guardrail_message, language_code=language_code)

        async def _send_guardrail_response() -> None:
            await asyncio.sleep(1)  # Brief pause before guardrail response
            await self.session_manager.send_message(msg)

        task = asyncio.create_task(_send_guardrail_response())
        task.add_done_callback(_log_task_exception)

    async def _handle_raw_model_event(self, event: Any) -> None:
        """Handle raw_model_event — VAD speech events, transcript deltas, errors."""
        raw = event.data
        if isinstance(raw, RealtimeModelExceptionEvent):
            await self._handle_session_error(event)
            return

        raw_type = getattr(raw, "type", None) or (raw.get("type") if isinstance(raw, dict) else None)
        if raw_type == "transcript_delta":
            item_id = getattr(raw, "item_id", None)
            delta = getattr(raw, "delta", None)
            if item_id and delta:
                cache = self.session_manager.transcript_cache
                cache[item_id] = cache.get(item_id, "") + delta
        elif raw_type in {"input_audio_buffer.speech_started", "input_audio_buffer.speech_start"}:
            if not self.call_state.is_user_speaking:
                self.call_state.mark_user_speaking_started()
        elif raw_type in {"input_audio_buffer.speech_stopped", "input_audio_buffer.speech_stop"}:
            self.call_state.last_user_speaking_stopped_at = datetime.datetime.now(datetime.UTC)

    async def _handle_agent_end(self, event: Any) -> None:
        """Handle agent_end — fire trace."""
        self.tracer.fire_trace_task(
            history=self.session_manager.history,
            root_run=self.root_run,
            message_start_times=self.playback.message_start_times,
            message_end_times=self.playback.message_end_times,
            filler_item_ids=self.filler.filler_item_ids,
            rendered_system_prompt=getattr(self.ctx, "rendered_system_prompt", None) if self.ctx else None,
        )

    async def _handle_session_error(self, event: Any) -> None:
        """Handle session errors — attempt recovery."""
        logger.warning(f"Voice session error: {event}")

        # Recoverable race: ``response.create`` was issued while a previous
        # response was still in flight (e.g. guardrail or filler timing).
        # A full ``recover_session()`` rebuild is too heavy here — delegate
        # to the cancel-and-retry path instead.
        if _is_active_response_race(event):
            logger.info("Race condition detected: active response in progress — forcing cancel and retrying")
            await recover_from_active_response_race(
                session_manager=self.session_manager,
                interrupt_handler=self.interrupt_handler,
            )
            return

        if self.voice_agent and self.ctx:
            try:
                language_code = getattr(self.ctx, "conversation_language", "en")
                await recover_session(
                    session_manager=self.session_manager,
                    agent=self.voice_agent.agent(),
                    context=self.ctx,
                    language_code=language_code,
                )
            except Exception as recovery_error:
                logger.warning(f"Voice recovery failed: {recovery_error}")

    # ------------------------------------------------------------------
    # Playback completion callback
    # ------------------------------------------------------------------

    async def _on_response_completed(self, item_id: str) -> None:
        """Called by PlaybackTracker when the last mark for an item plays."""
        # Fire trace
        self.tracer.fire_trace_task(
            history=self.session_manager.history,
            root_run=self.root_run,
            message_start_times=self.playback.message_start_times,
            message_end_times=self.playback.message_end_times,
            filler_item_ids=self.filler.filler_item_ids,
            rendered_system_prompt=getattr(self.ctx, "rendered_system_prompt", None) if self.ctx else None,
        )
        # Update speaking state
        if not self.playback.has_pending_items():
            self.call_state.mark_agent_speaking_stopped()
        # Policy transition (greeting → default)
        self.policy = await self.policy.on_playback_complete(self.call_state)
        # Clear greeting buffer suppression on transition to default
        if isinstance(self.policy, DefaultPolicy) and self.buffer.suppress_flush:
            if self.buffer.pending_bytes > 0:
                logger.debug(f"Voice discarding audio buffered during greeting ({self.buffer.pending_bytes} bytes)")
                self.buffer.clear()
            self.buffer.suppress_flush = False
            # Refresh agent instructions with welcome_greeting_delivered
            if self.ctx:
                self.ctx.welcome_greeting_delivered = True
            # Greeting-agent fast path: the full agent init was deferred to a
            # background task so the greeting could fire immediately.  Wait for
            # it to finish before swapping in the full agent.  Transfer the
            # call to staff on timeout or init failure.
            if self._full_agent_task is not None:
                if not await self._await_full_agent_ready():
                    return
            if self.voice_agent and self.session_manager.session:
                if self._parallel_init_span:
                    self._parallel_init_span.mark("agent_swap_start")
                await self.session_manager.update_agent(self.voice_agent.agent())
                if self._parallel_init_span:
                    self._parallel_init_span.mark("agent_swap_end")

    async def _resolve_langsmith_url(self) -> None:
        """Resolve the LangSmith trace URL in the background and log it.

        Mirrors ``twilio_handler._resolve_langsmith_url``.  Runs as a
        fire-and-forget task so the sync HTTP call doesn't block startup.
        Sets ``ctx.langsmith_trace_url`` when done.
        """
        if self.root_run is None or self.ctx is None:
            return
        try:
            url = await asyncio.to_thread(get_langsmith_trace_url, self.root_run)
            self.ctx.langsmith_trace_url = url
            logger.info(f"Langsmith Trace: {url}")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Failed to resolve LangSmith trace URL", exc_info=True)

    async def _init_full_voice_agent(self, *, trace_id: str, group_id: str) -> None:
        """Run the full VoiceAgent setup (LDP + MCP + prefetch) in the background.

        Called as a background task when ``greeting_agent_enabled=True`` so the
        greeting can fire immediately while MCP connections are being established.

        Spans attach to the call's trace via the inherited ``contextvar`` that
        ``asyncio.create_task`` snapshots from the parent task.  Only opens a
        fresh ``trace()`` if no trace is active in the inherited context (which
        happens once the outer ``with trace()`` in ``_setup_voice_agent`` exits
        before this background task finishes).  Skipping the fresh ``trace()``
        when one is already active prevents the SDK's "Trace already exists"
        warning.
        """
        assert self.voice_agent is not None
        assert self._parallel_init_span is not None
        self._parallel_init_span.mark("agent_init_start")
        trace_ctx = (
            nullcontext()
            if get_current_trace() is not None
            else trace(workflow_name="Resident One Voice", trace_id=trace_id, group_id=group_id)
        )
        with trace_ctx, self._parallel_init_span.attach(self.root_run):
            await self.voice_agent.setup()
        self._parallel_init_span.mark("agent_init_end")
        logger.info("Full voice agent initialised (background)")

    async def _await_full_agent_ready(self) -> bool:
        """Await the background full-agent init before the greeting→default swap.

        Returns True when the full agent is ready and the caller should proceed
        with ``session_manager.update_agent``.  Returns False when init timed
        out or failed — in that case the call has already been transferred to
        staff and the caller must not touch the session.
        """
        task = self._full_agent_task
        if task is None:
            return True
        if not task.done():
            logger.info("Greeting finished before full agent ready — waiting for init")
        try:
            await asyncio.wait_for(task, timeout=self.config.greeting_agent_init_timeout_seconds)
        except TimeoutError:
            logger.error("Full agent init timed out — transferring to staff")
            task.cancel()
            self._full_agent_task = None
            await self._transfer_on_init_failure()
            return False
        except Exception:
            logger.exception("Full agent init failed — transferring to staff")
            self._full_agent_task = None
            await self._transfer_on_init_failure()
            return False
        self._full_agent_task = None
        return True

    async def _transfer_on_init_failure(self) -> None:
        """End the call and hand it off to staff after full-agent init fails."""
        call_sid = self.transport.call_metadata.call_sid if self.transport else ""
        await transfer_call_on_init_failure(call_sid)
        self.call_active = False
        self._shutdown_reason = self._shutdown_reason or "agent_init_failure"

    # ------------------------------------------------------------------
    # Background loops
    # ------------------------------------------------------------------

    async def _session_event_loop(self) -> None:
        """Read events from the OpenAI session and dispatch them."""
        try:
            async for event in self.session_manager.events():
                try:
                    await self.dispatcher.dispatch(event)
                except ModelBehaviorError as e:
                    logger.debug(f"Model behavior error: {e}")
                    await self._handle_session_error(event)
                except Exception as e:
                    logger.debug(f"Session event error: {e}")
                    try:
                        await self._handle_session_error(event)
                    except Exception as recovery_error:
                        logger.warning(f"Recovery failed: {recovery_error}")
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise  # External cancellation — must propagate
            logger.debug("Session event loop cancelled during cleanup")
        except Exception as e:
            logger.debug(f"Session event loop error: {e}")

    async def _inactivity_loop(self) -> None:
        """Monitor for extended silence, dead lines, and session expiration."""
        try:
            while self.call_active:
                await asyncio.sleep(1.0)

                if not self.session_manager._session_ready.is_set():
                    continue

                # Session expiration
                if self._session_start_time and (
                    time.time() - self._session_start_time >= self.config.max_session_duration_seconds
                ):
                    logger.warning(f"Session expired after {self.config.max_session_duration_seconds}s")
                    self._shutdown_reason = "session_timeout"
                    await self._cleanup_call()
                    break

                # WebSocket health
                if not self.transport.is_connected:
                    logger.debug("Transport disconnected — cleanup")
                    self._shutdown_reason = "transport_disconnect"
                    await self._cleanup_call()
                    break

                # Dead line
                if self.filler.is_dead_line():
                    logger.warning(
                        f"Dead line detected ({self.filler.consecutive_fillers_without_user_audio} consecutive fillers)"
                    )
                    self._shutdown_reason = "filler_deadline"
                    await self._cleanup_call()
                    break

                # Filler
                if self.ctx:
                    await self.filler.send_if_due(
                        language_code=getattr(self.ctx, "conversation_language", "en"),
                        thinker_running=getattr(self.ctx, "thinker_running", False),
                        transfer_summary_flow_active=getattr(self.ctx, "transfer_summary_requested", False),
                        destructive_handoff_in_progress=getattr(self.ctx, "handoff_in_progress", False),
                        call_active=self.call_active,
                        session_ready=self.session_manager._session_ready.is_set(),
                    )
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise  # External cancellation — must propagate
            logger.debug("Inactivity monitor cancelled during cleanup")
        except Exception:
            logger.warning("Inactivity monitor crashed", exc_info=True)

    # ------------------------------------------------------------------
    # VoiceCallbacks implementation
    # ------------------------------------------------------------------

    async def schedule_filler(self) -> None:
        self.filler.schedule()

    async def cancel_filler(self) -> None:
        """Cancel active filler — coordinates interrupt handler + session.

        The poll loop (up to 500ms) waits for the interrupt to propagate
        through the OpenAI session and back as an ``audio_interrupted``
        event.  A poll is used instead of an asyncio.Event because the
        state transition happens across two independent async paths (the
        session event loop sets ``is_agent_speaking=False`` via the
        interrupt handler, while this method runs in the thinker tool's
        task).  The poll is bounded and the 50ms granularity is well
        within acceptable latency for a voice call.

        Caller is responsible for only invoking this when audio is in
        flight — issuing the underlying ``send_interrupt()`` against a
        quiet session causes gpt-realtime-2 to regenerate the prior
        assistant audio (issue #1641 duplicate playback).
        """
        self.filler.cancel_schedule()
        self.filler.next_speech_is_filler = False
        self.interrupt_handler.expecting_cancel_interrupt = True
        await self.session_manager.send_interrupt()
        for _ in range(10):
            await asyncio.sleep(0.05)
            if not self.call_state.is_agent_speaking:
                break
        self.interrupt_handler.expecting_cancel_interrupt = False

    async def on_thinker_started(self) -> None:
        pass  # thinker_running is set on ctx by the tool itself

    async def on_thinker_completed(self) -> None:
        self.filler.on_thinker_completed()

    async def suppress_filler_temporarily(self, seconds: float) -> None:
        self.filler.suppress_temporarily(seconds)

    async def request_response(self, snapshot_turn_id: int) -> bool:
        ok = await self.response_gate.acquire(snapshot_turn_id)
        if ok:
            await self.session_manager.create_response()
        return ok

    @property
    def turn_id(self) -> int:
        return self.response_gate.turn_id

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _trigger_greeting(self) -> None:
        """Trigger the initial greeting via response.create with audio modality."""
        if not self.session_manager.session:
            return
        logger.info("Voice triggering greeting")
        await self.session_manager.create_response(output_modalities=["audio"])

    def _build_metadata(self, ask_request: AskRequest) -> dict[str, Any]:
        """Build metadata dict for tracing.

        Must match the fields in twilio_handler.py ``_agent_setup`` so
        LangSmith traces are searchable by the same keys.
        """
        import uuid

        start_time_iso = datetime.datetime.fromtimestamp(
            self._session_start_time or time.time(), tz=datetime.UTC
        ).isoformat()

        return {
            "environment": self.config.environment,
            "property-id": ask_request.property_id,
            "resident-id": ask_request.product_info.knock_resident_id,
            "company-id": ask_request.product_info.uc_company_id.id
            if ask_request.product_info.uc_company_id
            else None,
            "product": ask_request.product,
            "property-name": ask_request.product_info.property_name,
            "start-time": start_time_iso,
            "call-sid": ask_request.product_info.call_sid,
            "pmc-id": ask_request.product_info.pmc_id,
            "pmc-name": ask_request.product_info.pmc_name,
            "openai-group-url": getattr(self.ctx, "openai_group_url", None) if self.ctx else None,
            "chat-session-id": ask_request.chat_session_id,
            "openai-trace-id": self.session_manager.trace_id,
            "caller": ask_request.product_info.caller,
            "thread-id": ask_request.chat_session_id,
            "request-id": str(uuid.uuid4()),
        }

    def _get_final_history(self) -> list[Any]:
        """Get the best available history for final tracing."""
        if self.session_manager.session:
            return realtime_history_to_input_list(
                self.session_manager.session._history,
                include_item_id=True,
                transcript_cache=self.session_manager.transcript_cache,
            )
        return self.session_manager.history

    def _get_trace_history(self) -> list[Any]:
        """Get history for mid-call tracing (uses session._history for completeness)."""
        if self.session_manager.session:
            return realtime_history_to_input_list(
                self.session_manager.session._history,
                include_item_id=True,
                transcript_cache=self.session_manager.transcript_cache,
            )
        return self.session_manager.history


class VoiceHandlerManager:
    """Lightweight registry of active VoiceHandler instances.

    Matches ``TwilioWebSocketManager``'s API so server.py can use
    either interchangeably.
    """

    def __init__(self) -> None:
        self.active_handlers: dict[str, VoiceHandler] = {}

    async def new_session(self, websocket: WebSocket) -> VoiceHandler:
        """Create a new VoiceHandler for an incoming call.

        Async to match ``TwilioWebSocketManager.new_session`` so server.py
        can use either manager interchangeably without sync/async branching.
        """
        config = voice_config_from_settings(settings)
        transport = TwilioTransport(websocket)
        handler = VoiceHandler(transport, config)
        handler.playback.on_response_completed = handler._on_response_completed
        handler_id = str(id(handler))
        self.active_handlers[handler_id] = handler
        logger.info(f"Voice handler created {handler_id} (active: {len(self.active_handlers)})")
        return handler

    async def cleanup_handler(self, handler_id: str) -> None:
        """Remove a handler from the registry.

        Async to match ``TwilioWebSocketManager.cleanup_handler``.
        """
        if handler_id in self.active_handlers:
            self.active_handlers.pop(handler_id)
            logger.info(f"Voice handler removed {handler_id} (active: {len(self.active_handlers)})")
