from __future__ import annotations

import asyncio
import base64
import contextvars
import copy
import datetime
import json
import random
import time
import uuid
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import langsmith as ls
import orjson
import pydantic
import structlog
from agents import ModelBehaviorError, gen_trace_id, trace
from agents.realtime import (
    RealtimeError,
    RealtimeInputAudioNoiseReductionConfig,
    RealtimeInputAudioTranscriptionConfig,
    RealtimeModelConfig,
    RealtimeModelExceptionEvent,
    RealtimeModelSendInterrupt,
    RealtimeModelSendRawMessage,
    RealtimeModelTracingConfig,
    RealtimePlaybackTracker,
    RealtimeRunner,
    RealtimeSession,
    RealtimeSessionEvent,
    RealtimeSessionModelSettings,
    RealtimeTurnDetectionConfig,
    UserMessageItem,
)
from agents.realtime.config import RealtimeReasoningConfig
from agents.tracing.util import gen_group_id
from langsmith.run_helpers import tracing_context
from starlette.websockets import WebSocket, WebSocketDisconnect
from twilio.rest import Client as TwilioClient

from agent_leasing.agent.resident_one_agent.agent import (
    ensure_disabled_modules_and_tools_loaded,
)
from agent_leasing.agent.resident_one_agent.realtime import (
    build_parallel_greeting_agent,
)
from agent_leasing.agent.tools.transfer_to_staff.transfer_to_staff_voice import (
    _build_transfer_twiml,
)
from agent_leasing.agent.util import SessionScope, agent_selector
from agent_leasing.api.model import (
    AskRequest,
    Product,
    examples,
)
from agent_leasing.kafka.fire_and_forget import drain_pending_publishes
from agent_leasing.kafka.kafka_context import kafka_application_context
from agent_leasing.kafka.task_event import (
    build_end_of_session_event,
    build_in_progress_event,
    publish_task_event_fire_and_forget,
)
from agent_leasing.settings import settings
from agent_leasing.util.audio_noise_reduction import apply_noise_reduction
from agent_leasing.util.call_state_manager import CallStateManager
from agent_leasing.util.realtime_util import (
    log_data_curation_event_for_realtime_history,
    realtime_history_to_input_list,
)
from agent_leasing.util.tracing_utils import (
    DeferredSpanTree,
    build_openai_group_url,
    build_openai_trace_url,
    build_validation_failure_marker_inputs,
    get_langsmith_trace_url,
    normalize_metadata_keys,
    post_trace_marker,
    record_initial_greeting_latency,
)
from agent_leasing.util.twilio_util import get_twilio_credentials

logger = structlog.getLogger()

ASYNCIO_SLEEP_GUARDRAIL_TRIPPED_TIME = 1


def _has_task_event_context(ctx: SessionScope | None) -> bool:
    """Task events require ask_request because task.id is derived from it."""
    return bool(ctx and ctx.ask_request)


VOICE_STARTUP_PHASES: list[tuple[str, str, str]] = [
    ("process_start_payload", "start_event_received", "start_payload_processed"),
    ("agent_init", "agent_init_start", "agent_init_end"),
    ("configure_session", "configure_session_start", "configure_session_end"),
    ("create_session", "create_session_start", "create_session_end"),
    ("session_enter", "session_enter_start", "session_enter_end"),
    ("trigger_greeting", "trigger_greeting_start", "trigger_greeting_end"),
    ("first_audio_received", "trigger_greeting_end", "first_audio_received"),
    ("first_utterance_sent", "first_audio_received", "first_utterance_sent"),
]

# Greeting-agent path: critical path only in welcome_agent_init.
# agent_init runs in parallel — tracked in a SEPARATE span tree so it renders
# as a sibling of welcome_agent_init (underneath it in LangSmith).
VOICE_STARTUP_PHASES_GREETING_AGENT: list[tuple[str, str, str]] = [
    ("process_start_payload", "start_event_received", "start_payload_processed"),
    ("prepare_greeting_context", "prepare_greeting_context_start", "prepare_greeting_context_end"),
    ("configure_session", "configure_session_start", "configure_session_end"),
    ("create_session", "create_session_start", "create_session_end"),
    ("session_enter", "session_enter_start", "session_enter_end"),
    ("trigger_greeting", "trigger_greeting_start", "trigger_greeting_end"),
    ("first_audio_received", "trigger_greeting_end", "first_audio_received"),
    ("first_utterance_sent", "first_audio_received", "first_utterance_sent"),
]

# Separate span tree for the parallel agent init — sibling of welcome_agent_init.
VOICE_PARALLEL_INIT_PHASES: list[tuple[str, str, str]] = [
    ("full_init", "agent_init_start", "agent_init_end"),
    ("agent_swap", "agent_swap_start", "agent_swap_end"),
]

# Twilio-specific message constants

GUARDRAIL_TRIPPED_MESSAGE = (
    "The following guardrails were tripped:\n{guardrail_message}.\n"
    "Respond to the user with a creative variation of the following in {language_code}: "
    "I cannot answer the previous question. How else can I help you?"
)

FILLER_HANDOFF_MESSAGE = """
**IMPORTANT**: Do not acknowledge this message.
A transfer to staff is in progress — the caller was just asked to provide a summary.
Respond in {language_code}.
- If the caller provided a summary, call `transfer_to_staff_voice` with that summary NOW.
- If the caller has not responded (silence), call `transfer_to_staff_voice(summary=None)` NOW to honor the original transfer request. Do NOT re-ask the summary question.
Do NOT call any other tools. Do NOT change the subject. Stay focused on the transfer.
"""

FILLER_THINKER_ACTIVE_MESSAGE = """
**IMPORTANT**: Do not acknowledge this message.
Just send a short, natural filler line in {language_code}.
Vary the tone and word choice each time so it does not sound scripted.
We are waiting for a tool or internal action to finish. Deliver a fresh paraphrase of:
`I'm still working on that—it'll just be a little bit longer.`
"""

FILLER_IDLE_MESSAGE = """
**IMPORTANT**: Do not acknowledge this message.
Just send a short, natural filler line in {language_code}.
Vary the tone and word choice each time so it does not sound scripted, and reference any relevant context when it helps.
1) If the resident asked a question or made a request that hasn't been addressed yet, call `resident_thinker_tool` with a summary of their request instead of sending a filler.
2) Otherwise, deliver a new paraphrase of:
`I'm still here for you—let me know if there's anything else I can help with.`
"""

FILLER_ESCALATION_MESSAGE = """
**CRITICAL**: You have been sending filler messages for a while. Review the conversation NOW.
Respond in {language_code}.
- If the resident provided information, asked a question, or made a request that hasn't been fully resolved \
with a tool call, you MUST call `resident_thinker_tool` NOW with a summary of their request.
- If the resident's last request was already completed and you are waiting for them to speak, say: \
"I'm still here — is there anything else I can help you with?"
Do NOT send another "please wait" filler unless a tool is actively processing.
"""

RECOVERY_MESSAGE = (
    "The agent crashed.  Here is the conversation state:\n{history}\n"
    "Please continue the conversation with the user where it left off as naturally as possible. "
    "Only recognize the error when absolutely necessary (ideally, in a charismatic or disarming way). "
    "**IMPORTANT**: Do not call any tools as part of this response, as that may be the reason for the crash. "
    "**IMPORTANT**: Respond in {language_code}."
)


def _log_background_task_exception(task: asyncio.Task) -> None:
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc:
        logger.warning(f"Background task failed: {exc}")


class TwilioHandler:
    """Manages real-time WebSocket connections, sessions, and interactions with Twilio."""

    def __init__(self, twilio_websocket: WebSocket):
        self.twilio_websocket = twilio_websocket

        self._message_loop_task: asyncio.Task[None] | None = None
        self._realtime_session_task: asyncio.Task[None] | None = None
        self._buffer_flush_task: asyncio.Task[None] | None = None
        self._recording_task: asyncio.Task[None] | None = None
        self._data_curation_task: asyncio.Task[None] | None = None
        self._langsmith_url_task: asyncio.Task[None] | None = None
        self._full_agent_task: asyncio.Task[None] | None = None
        self._pending_trace_tasks: set[asyncio.Task[None]] = set()

        self.session: RealtimeSession | None = None
        self.playback_tracker = RealtimePlaybackTracker()

        # Event to signal when session is ready
        self._session_ready = asyncio.Event()

        # Audio buffering configuration (matching CLI demo)
        self.CHUNK_LENGTH_S = 0.05  # 50ms chunks like CLI demo
        self.SAMPLE_RATE = 8000  # Twilio uses 8kHz for g711_ulaw
        self.BUFFER_SIZE_BYTES = int(self.SAMPLE_RATE * self.CHUNK_LENGTH_S)  # 50 ms worth of audio

        self._stream_sid: str | None = None
        self._call_sid: str | None = None
        self._payload: dict[str, Any] | None = None
        self._audio_buffer: bytearray = bytearray()
        # KNCK-39464: serialize _flush_audio_buffer across the message loop and the
        # periodic buffer flush loop. Without this, concurrent flushes can snapshot
        # the same bytes and double-send them to OpenAI's input_audio_buffer.
        self._flush_lock = asyncio.Lock()
        self._last_buffer_send_time = time.time()
        self._inactivity_monitor_task: asyncio.Task[None] | None = None
        self._last_audio_time = time.time()  # Track latest audio activity from either side
        self._next_filler_time: float | None = None

        # Mark event tracking for playback
        self._mark_counter = 0
        self._mark_data: dict[str, tuple[str, int, int]] = {}  # mark_id -> (item_id, content_index, byte_count)
        self._mark_data_max_size = 1000  # Prevent unbounded growth if Twilio doesn't send mark events

        self.call_active = True

        self.agent = None

        self.history = []
        self.viewed_messages = set()
        # Real timestamps for LangSmith traces, keyed by item_id
        self._message_start_times: dict[str, datetime.datetime] = {}
        self._message_end_times: dict[str, datetime.datetime] = {}
        # SDK bug workaround: accumulate transcripts independently of session._item_transcripts
        # which gets cleared on turn_ended before item_updated can use them (KNCK-38461)
        self._transcript_cache: dict[str, str] = {}
        self._last_user_speaking_started_at: datetime.datetime | None = None
        self._last_user_speaking_stopped_at: datetime.datetime | None = None

        # Tracing identifiers (set during _agent_setup)
        self.trace_id: str | None = None
        self.group_id: str | None = None
        self._session_metadata: dict = {}  # Stored by _setup_realtime_session for retry

        # --- Audio pacer configuration ---
        self._frame_bytes = 160  # 20ms @ 8kHz μ-law mono
        self._tick_seconds = 0.020  # 20ms per frame
        self._prebuffer_frames = 6  # ~120ms prebuffer
        self._pacer_startup_timeout_sec = 0.120  # 120ms startup timeout
        self._pacer_underrun_grace_sec = 0.003  # 3ms underrun grace period
        self._silence_byte = 0xFF  # μ-law silence byte

        # Outgoing μ-law frame queue (FIFO) + partial accumulator for alignment
        # Queue stores tuples: (frame_bytes, (item_id, content_index)) to track which event each frame belongs to
        self._out_frame_q: deque[tuple[bytes, tuple[str, str, int]]] = (
            deque()
        )  # holds (frame_bytes, (mark_id, item_id, content_index))
        self._out_partial = bytearray()
        self._current_partial_event: tuple[str, str, int] | None = None  # tracks event metadata for partial frame
        self._pacer_task: asyncio.Task[None] | None = None
        self._pacer_running = False
        self._first_ulaw_rx_ts: float | None = None

        # Initial greeting interruption control
        self._is_initial_greeting = False

        # General response tracking - maps item_id to its last mark_id
        self._response_last_mark_ids: dict[str, str] = {}

        # Call state manager tracks speaking/processing states
        self._call_state = CallStateManager()

        # Track when next speech will be a filler message
        self._next_speech_is_filler = False
        self._filler_item_ids: set[str] = set()

        # Track when we're expecting an audio_interrupted from our own cancel operation
        # This prevents us from incorrectly marking the user as speaking when WE triggered the interrupt
        self._expecting_cancel_interrupt = False

        # Langsmith state
        self.root_run: ls.RunTree | None = None
        self._agent_trace = None
        self._user_trace = None
        self._user_message = ""
        self._startup_span = DeferredSpanTree("welcome_agent_init", VOICE_STARTUP_PHASES)
        self._parallel_init_span: DeferredSpanTree | None = None
        self._pre_startup_ctx: contextvars.Context | None = None
        self._initial_greeting_latency_recorded: bool = False

        self._cleanup_called = False
        self._shutdown_reason: str | None = None

        self._session_start_time: float | None = None
        self._consecutive_fillers_without_user_audio: int = 0

    @property
    def is_agent_speaking(self) -> bool:
        """Whether agent is currently speaking (delegates to CallStateManager)."""
        return self._call_state.is_agent_speaking

    @is_agent_speaking.setter
    def is_agent_speaking(self, value: bool) -> None:
        """Set agent speaking state (delegates to CallStateManager)."""
        self._call_state.is_agent_speaking = value

    @property
    def is_user_speaking(self) -> bool:
        """Whether user is currently speaking (delegates to CallStateManager)."""
        return self._call_state.is_user_speaking

    @is_user_speaking.setter
    def is_user_speaking(self, value: bool) -> None:
        """Set user speaking state (delegates to CallStateManager)."""
        self._call_state.is_user_speaking = value

    @property
    def _interrupt_suppression_active(self) -> bool:
        """Whether caller-interrupt suppression is active (flag enabled + handoff in progress)."""
        return (
            settings.interrupt_suppression_enabled
            and hasattr(self, "ctx")
            and getattr(self.ctx, "handoff_in_progress", False) is True
        )

    def _get_language_code(self) -> str:
        """Return language code with a safe default."""
        return getattr(getattr(self, "ctx", None), "language_code", None) or "en"

    async def start(self) -> None:
        """Start the session."""

        self.call_active = True

        await self.twilio_websocket.accept()
        logger.info("Twilio WebSocket connection accepted")

        # removing realtime session loop, because it's created
        # in the self._twilio_message_loop(), AFTER agent initialization
        self._message_loop_task = asyncio.create_task(self._twilio_message_loop())
        self._message_loop_task.add_done_callback(_log_background_task_exception)

        self._buffer_flush_task = asyncio.create_task(self._buffer_flush_loop())
        self._buffer_flush_task.add_done_callback(_log_background_task_exception)

        # _inactivity_monitor_task is created after bind_contextvars in _setup_realtime_session
        # so that it inherits structlog context (call_sid, etc.)

    async def _trigger_initial_greeting(self) -> None:
        """Trigger the initial greeting using response.create instead of a fake user message.

        This method sends a response.create event to the OpenAI Realtime API to proactively
        generate the welcome greeting when the Twilio call starts. This avoids polluting
        conversation history with a fake "START" user message.

        The greeting content is defined entirely in VOICE_RESPONDER.md's Welcome Workflow.
        """
        if not self.session or not hasattr(self.session, "_model"):
            logger.warning("Cannot trigger greeting: session or model not available")
            return

        logger.info("Triggering initial greeting via response.create")

        try:
            # Send response.create WITHOUT the "instructions" field.
            # The "instructions" field overrides (replaces) the session-level
            # instructions for that response, which strips VOICE_RESPONDER.md
            # and the full Welcome Workflow definition.
            # Instead, VOICE_RESPONDER.md handles the trigger:
            #   "IF AND ONLY IF no conversation history exists, you MUST use the Welcome Workflow"
            await self.session._model.send_event(
                RealtimeModelSendRawMessage(
                    message={
                        "type": "response.create",
                        "other_data": {
                            "response": {
                                "output_modalities": ["audio"],
                            },
                        },
                    }
                )
            )
            logger.info("Initial greeting response.create sent successfully")
        except Exception as e:
            logger.error(f"Failed to trigger initial greeting: {e}")
            raise

        self._schedule_next_filler()

    async def _prepare_greeting_context(self, ask_request: AskRequest) -> None:
        """Load the minimal context needed before rendering the greeting workflow."""
        if ask_request.product != Product.RESIDENT_ONE_VOICE:
            return

        self._startup_span.mark("prepare_greeting_context_start")
        await ensure_disabled_modules_and_tools_loaded(self.ctx)
        self._startup_span.mark("prepare_greeting_context_end")

    async def _init_full_agent(self, ask_request: AskRequest) -> None:
        """Initialise the full agent (LDP + MCP + prefetch) in the background.

        Sets self.agent when complete. Called as a background task when
        greeting_agent_enabled=True so the greeting can fire immediately.
        """
        self._parallel_init_span.mark("agent_init_start")
        agent = agent_selector(ask_request.product, self.ctx)

        with trace(
            workflow_name="Resident One Voice",
            trace_id=self.trace_id,
            group_id=self.group_id,
        ):
            try:
                await agent.__aenter__()
            except BaseException:
                # Clean up partially-initialised MCP connections
                try:
                    await agent.__aexit__(None, None, None)
                except Exception:
                    logger.warning("Error cleaning up partially-initialized agent")
                raise
            # Only assign after successful init so other code paths never see
            # a truthy but half-initialised self.agent.
            self.agent = agent
            logger.info("Full agent initialised (background)", agent=str(self.agent))
        self._parallel_init_span.mark("agent_init_end")

    async def _agent_setup(self, payload: dict[str, Any]):
        """
        Set up the agent with the necessary information.
        """
        self.trace_id = gen_trace_id()
        self.group_id = gen_group_id()

        try:
            ask_request = AskRequest(**payload)
        except (pydantic.ValidationError, ValueError) as e:
            await self._transfer_call_on_validation_failure(e, payload)
            self.call_active = False
            return

        # Add OpenAI tracing identifiers to LangSmith root run early,
        # so they appear in LangSmith even if the call crashes later.
        self.root_run.add_metadata(
            {
                "openai_trace_id": self.trace_id,
                "openai_group_id": self.group_id,
                "voice_handler_variant": getattr(self, "variant", "v1"),
            }
        )

        self.ctx = SessionScope(
            ask_request=ask_request,
            langsmith_run_tree=self.root_run.to_headers(),
        )
        self.ctx.call_state_manager = self._call_state

        # Pass session handler reference to context for thinker tool to access
        # This allows the thinker to cancel any active filler messages before responding
        self.ctx._session_handler = self

        # Resident AI has picked up the conversation — publish IN_PROGRESS event.
        # Fire-and-forget so the publish never blocks the voice hot path.
        if _has_task_event_context(self.ctx):
            publish_task_event_fire_and_forget(
                kafka_application_context.task_event_producer,
                build_in_progress_event(self.ctx),
                self.ctx.pending_activity_publishes,
            )

        structlog.contextvars.clear_contextvars()

        structlog.contextvars.bind_contextvars(
            openai_trace_id=self.trace_id,
            chat_session_id=ask_request.chat_session_id,
            product=ask_request.product,
            uc_property_id=getattr(ask_request.product_info.uc_property_id, "id", None),
            knock_property_id=ask_request.product_info.knock_property_id,
            knock_resident_id=ask_request.product_info.knock_resident_id,
            call_sid=ask_request.product_info.call_sid,
        )

        logger.info(
            "Setting up real-time agent",
            event_type="call_entry",
            channel="resident_one_voice",
            payload=payload,
        )

        self._session_start_time = time.time()

        # Start conversation creation early so it runs in parallel with
        # MCP connections, LDP calls, and other voice setup work.
        # The thinker tool will await the result via ensure_conversation_id.
        from agent_leasing.services.agent_service import start_conversation_creation

        start_conversation_creation(self.ctx)

        # Snapshot context BEFORE entering the startup span so background tasks
        # (session loop, inactivity monitor) don't inherit welcome_agent_init.
        self._pre_startup_ctx = contextvars.copy_context()

        if settings.greeting_agent_enabled:
            # Switch to the greeting-agent phase list so traces show parallel spans.
            # Preserve marks already recorded (e.g. process_start_payload).
            existing_marks = dict(self._startup_span._marks)
            self._startup_span = DeferredSpanTree("welcome_agent_init", VOICE_STARTUP_PHASES_GREETING_AGENT)
            self._startup_span._marks.update(existing_marks)

        with self._startup_span.attach(self.root_run):
            if settings.greeting_agent_enabled:
                await self._agent_setup_greeting_path(ask_request)
            else:
                await self._agent_setup_sequential_path(ask_request)

    def _configure_session(self, ask_request: AskRequest) -> dict[str, Any]:
        """Build model config, set trace URLs, and return session metadata."""
        self._startup_span.mark("configure_session_start")
        self.model_config = self._build_model_config()
        if settings.openai_base_wss_url:
            self.model_config["url"] = settings.openai_wss_full_endpoint

        self.ctx.openai_trace_url = build_openai_trace_url(trace_id=self.trace_id)
        self.ctx.openai_group_id = self.group_id
        self.ctx.openai_group_url = build_openai_group_url(group_id=self.group_id)
        self._langsmith_url_task = asyncio.create_task(self._resolve_langsmith_url(), name="resolve-langsmith-url")
        logger.info(f"Open AI Trace: {self.ctx.openai_trace_url}")
        logger.info(f"Open AI Group: {self.ctx.openai_group_url}")

        metadata = self._build_session_metadata(ask_request)
        self.root_run.add_metadata(normalize_metadata_keys(metadata))
        self._startup_span.mark("configure_session_end")
        return metadata

    async def _enter_and_greet(self) -> None:
        """Enter the realtime session, start the event loop, and trigger the greeting."""
        await self._enter_realtime_session()
        self._start_realtime_session_loop()

        self._startup_span.mark("trigger_greeting_start")
        await self._trigger_initial_greeting()
        self._startup_span.mark("trigger_greeting_end")

    async def _agent_setup_sequential_path(self, ask_request: AskRequest) -> None:
        """Original sequential startup: full agent init -> WSS -> greeting."""
        self._startup_span.mark("agent_init_start")
        self.agent = agent_selector(ask_request.product, self.ctx)

        with trace(
            workflow_name="Resident One Voice",
            trace_id=self.trace_id,
            group_id=self.group_id,
        ):
            await self.agent.__aenter__()
            logger.debug(f"Resident Agent: {self.agent}")
            logger.debug(f"Agent SDK Agent: {self.agent.agent()}")
            starting_agent = self.agent.agent()
            self._startup_span.mark("agent_init_end")

            metadata = self._configure_session(ask_request)
            await self._setup_realtime_session(starting_agent, metadata)

        await self._enter_and_greet()

    async def _agent_setup_greeting_path(self, ask_request: AskRequest) -> None:
        """Fast startup: greeting agent fires immediately, full agent init in parallel."""
        await self._prepare_greeting_context(ask_request)
        metadata = self._configure_session(ask_request)

        # Create the full_agent_init_parallel span eagerly under root_run so the
        # background task's real spans (MCP connect, prefetch, etc.) nest inside it.
        self._parallel_init_span = DeferredSpanTree("full_agent_init_parallel", VOICE_PARALLEL_INIT_PHASES)
        self._parallel_init_span._run = self.root_run.create_child(name="full_agent_init_parallel", run_type="chain")

        greeting_agent = build_parallel_greeting_agent(self.ctx)
        # Snapshot a context with full_agent_init_parallel as the tracing parent.
        # Agent init spans will nest under it, not pollute welcome_agent_init.
        with tracing_context(parent=self._parallel_init_span._run):
            init_ctx = contextvars.copy_context()
        self._full_agent_task = asyncio.create_task(
            self._init_full_agent(ask_request),
            name="full-agent-init",
            context=init_ctx,
        )
        self._full_agent_task.add_done_callback(_log_background_task_exception)

        await self._setup_realtime_session(greeting_agent, metadata)
        await self._enter_and_greet()

    def _build_model_config(self) -> RealtimeModelConfig:
        """Build the RealtimeModelConfig from settings (no agent needed)."""
        return RealtimeModelConfig(
            api_key=settings.openai_api_key,
            initial_model_settings=RealtimeSessionModelSettings(
                model_name=settings.realtime_model,
                reasoning=RealtimeReasoningConfig(effort=settings.realtime_reasoning_effort),
                voice=settings.realtime_voice,
                speed=settings.realtime_voice_speed,
                input_audio_format=settings.openai_audio_format,
                output_audio_format=settings.openai_audio_format,
                input_audio_transcription=RealtimeInputAudioTranscriptionConfig(
                    model=settings.transcription_model,
                    language="en",
                ),
                input_audio_noise_reduction=RealtimeInputAudioNoiseReductionConfig(
                    type=settings.realtime_input_audio_noise_reduction
                ),
                turn_detection=RealtimeTurnDetectionConfig(
                    type=settings.realtime_turn_detection_type,
                    eagerness=settings.realtime_turn_detection_eagerness,
                    interrupt_response=settings.realtime_turn_detection_interrupt_response,
                    create_response=settings.realtime_turn_detection_create_response,
                ),
                tracing=RealtimeModelTracingConfig(
                    workflow_name="Resident One Voice",
                    group_id=self.group_id,
                ),
            ),
            playback_tracker=self.playback_tracker,
        )

    def _build_session_metadata(self, ask_request: AskRequest) -> dict[str, Any]:
        """Build the metadata dict for session creation and LangSmith."""
        start_time = time.time()
        start_time_iso = datetime.datetime.fromtimestamp(start_time, tz=datetime.UTC).isoformat()

        return {
            "environment": settings.environment,
            "property-id": self.ctx.ask_request.property_id,
            "resident-id": self.ctx.ask_request.product_info.knock_resident_id,
            "company-id": self.ctx.ask_request.product_info.uc_company_id.id
            if self.ctx.ask_request.product_info.uc_company_id
            else None,
            "product": self.ctx.ask_request.product,
            "property-name": self.ctx.ask_request.product_info.property_name,
            "start-time": start_time_iso,
            "call-sid": self.ctx.ask_request.product_info.call_sid,
            "pmc-id": self.ctx.ask_request.product_info.pmc_id,
            "pmc-name": self.ctx.ask_request.product_info.pmc_name,
            "openai-group-url": self.ctx.openai_group_url,
            "chat-session-id": self.ctx.ask_request.chat_session_id,
            "openai-trace-id": self.trace_id,
            "caller": self.ctx.ask_request.product_info.caller,
            "thread-id": ask_request.chat_session_id,
            "request-id": str(uuid.uuid4()),
        }

    async def _resolve_langsmith_url(self) -> None:
        """Resolve the LangSmith trace URL in the background.

        Runs in a fire-and-forget task so the sync HTTP call doesn't block
        the voice startup critical path. Sets ctx.langsmith_trace_url when done.
        """
        try:
            url = await asyncio.to_thread(get_langsmith_trace_url, self.root_run)
            self.ctx.langsmith_trace_url = url
            logger.info(f"Langsmith Trace: {url}")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Failed to resolve LangSmith trace URL", exc_info=True)

    async def _setup_realtime_session(self, starting_agent, metadata: dict) -> None:
        """Create a new realtime session. Does NOT enter it — call _enter_realtime_session() after."""
        self._session_metadata = metadata  # Stored for retry in _enter_realtime_session
        # Update the RealtimeModelTracingConfig metadata so OpenAI's server-side
        # tracing receives it (the trace() context manager metadata is local-only).
        # Filter out None values since OpenAI metadata values must be strings.
        try:
            tracing_metadata = {k: str(v) for k, v in metadata.items() if v is not None}
            self.model_config["initial_model_settings"]["tracing"]["metadata"] = tracing_metadata
        except (TypeError, KeyError):
            pass

        runner = RealtimeRunner(starting_agent)  # noqa
        # Note ulaw conversion for Twilio

        self._startup_span.mark("create_session_start")
        self.session = await runner.run(
            context=self.ctx,
            model_config=self.model_config,
        )
        self._startup_span.mark("create_session_end")

        # Wire up direct-inject so playback checks can prompt the model to speak
        self._call_state._send_message_fn = self._inject_message

    async def _enter_realtime_session(self, max_retries: int = 2):
        """Enter session and signal readiness, retrying on transient WebSocket failures.

        session.enter() starts internal WebSocket tasks that capture contextvars.
        Uses ``_pre_startup_ctx`` (captured before welcome_agent_init) so internal
        tasks don't inherit the startup span as parent.

        Retries handle transient OpenAI WebSocket errors (e.g., 1011 internal error)
        that can kill a call before it starts. On failure, the session is torn down
        and rebuilt before the next attempt.
        """
        clean_ctx = self._pre_startup_ctx or contextvars.copy_context()
        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                self._startup_span.mark("session_enter_start")
                enter_task = asyncio.create_task(self.session.enter(), context=clean_ctx)
                await enter_task
                self._startup_span.mark("session_enter_end")
                self._session_ready.set()
                self._schedule_next_filler()
                return
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    f"session.enter() failed (attempt {attempt}/{max_retries})",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                if attempt < max_retries:
                    # Tear down the broken session and rebuild before retrying
                    if self.session:
                        try:
                            await self.session.close()
                        except Exception as close_err:
                            logger.debug(f"Error closing failed session: {close_err}")
                        finally:
                            self.session = None
                    # Linear backoff: 0.5s, 1s, ...
                    await asyncio.sleep(0.5 * attempt)
                    # Rebuild session from the agent
                    starting_agent = self.agent.agent()
                    await self._setup_realtime_session(starting_agent, self._session_metadata)
        # All retries exhausted — raise the last exception so callers can handle it
        raise last_exc  # type: ignore[misc]

    def _start_realtime_session_loop(self):
        """Start the session loop with pre-startup context to avoid span leakage."""
        logger.info("Starting realtime session loop task")
        task_ctx = self._pre_startup_ctx
        self._realtime_session_task = asyncio.create_task(self._realtime_session_loop(), context=task_ctx)
        self._realtime_session_task.add_done_callback(_log_background_task_exception)

        # Create inactivity monitor here (after bind_contextvars) so it inherits structlog context
        if self._inactivity_monitor_task is None or self._inactivity_monitor_task.done():
            self._inactivity_monitor_task = asyncio.create_task(self._input_audio_inactivity_loop(), context=task_ctx)
            self._inactivity_monitor_task.add_done_callback(_log_background_task_exception)

    def _schedule_next_filler(self) -> None:
        """Schedule the next filler message based on configured timing."""
        if not self.call_active:
            self._next_filler_time = None
            return
        if not settings.send_filler_messages:
            self._next_filler_time = None
            return

        self._last_audio_time = time.time()

        mean = max(settings.filler_delay_mean_seconds, 0.0)
        std = max(settings.filler_delay_std_seconds, 0.0)

        delay = max(random.gauss(mean, std), 1)

        self._next_filler_time = self._last_audio_time + delay

    async def wait_until_done(self) -> None:
        """Wait until the session is done."""
        assert self._message_loop_task is not None
        try:
            await self._message_loop_task
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                # External cancellation - must propagate to avoid leaks
                raise
            # Internal cancellation during cleanup - expected
            logger.debug("Message loop task was cancelled")

    async def _realtime_session_loop(self) -> None:
        """Listen to events from the realtime session."""
        # Wait until the session is ready
        await self._session_ready.wait()

        assert self.session is not None, (
            "Realtime session is None - session must be initialized before starting the event loop"
        )
        event: RealtimeSessionEvent | None = None
        try:
            async for event in self.session:
                await self._handle_realtime_event(event)
        except ModelBehaviorError as e:
            logger.info(f"Model behavior error while handling event: {e}")
            await self._handle_realtime_error_event(event=event, error=e)
        except Exception as e:
            logger.info(f"Error in realtime session loop: {e}")
            try:
                await self._handle_realtime_error_event(event=event)
            except Exception as recovery_error:
                logger.warning(f"Recovery failed after session loop error: {recovery_error}")

    async def _twilio_message_loop(self) -> None:
        """Listen for messages from Twilio WebSocket and handle them."""
        # TODO(KNCK-39135): When voice load testing is enabled, suppress tracing here
        # with ls.tracing_context(enabled=not is_load_test) to avoid LangSmith cost spikes.
        with ls.trace(
            project_name=f"{settings.environment}_renter_ai_resident_voice",
            name=Product.RESIDENT_ONE_VOICE.value,
            run_type="chain",
        ) as run:
            try:
                self.root_run = run
                while self.call_active:
                    message_text = await self.twilio_websocket.receive_text()
                    message = orjson.loads(message_text.encode("utf-8"))
                    await self._handle_twilio_message(message)
            except asyncio.CancelledError:
                # CancelledError is BaseException — not caught by `except Exception`.
                # Catch explicitly to prevent it propagating through ls.trace()
                # and showing as a crash in LangSmith.
                logger.info("Twilio message loop cancelled during cleanup")
            except WebSocketDisconnect:
                logger.info("Twilio WebSocket disconnected")
                self._shutdown_reason = self._shutdown_reason or "websocket_disconnect"
            except (orjson.JSONDecodeError, ValueError) as e:
                logger.info(f"Failed to parse Twilio message as JSON: {e}")
            except Exception as e:
                logger.info(f"Error in Twilio message loop: {e}")
            finally:
                if self.call_active:
                    await self._cleanup_call()

    async def trace_messages_to_langsmith(self, history: list[Mapping[str, Any]]) -> None:
        if not self.root_run:
            return
        for message in history:
            item_id = message.get("item_id")
            role = message.get("role")
            if not self._should_trace_message(item_id, role):
                continue
            self.viewed_messages.add(item_id)  # claim before yielding to prevent duplicates
            await self._post_langsmith_child_run(message, item_id, role)

    def _fire_trace_task(self, history: list[Mapping[str, Any]]) -> None:
        """Launch tracing as a background task — must not block the audio event loop."""
        task = asyncio.create_task(self.trace_messages_to_langsmith(history))
        task.add_done_callback(_log_background_task_exception)
        self._pending_trace_tasks.add(task)
        task.add_done_callback(self._pending_trace_tasks.discard)

    def _should_trace_message(self, item_id: str | None, role: str | None) -> bool:
        """Check if a message should be traced (not yet viewed, not deferred)."""
        if item_id in self.viewed_messages:
            return False
        # Defer assistant messages until the Twilio mark confirms playback —
        # they'll be traced from _on_response_completed once end_time is set.
        if role == "assistant" and item_id not in self._message_end_times:
            return False
        return True

    async def _post_langsmith_child_run(self, message: Mapping[str, Any], item_id: str, role: str) -> None:
        """Create and post a single LangSmith child run for a traced message."""
        start_ts = self._message_start_times.get(item_id)
        end_ts = self._message_end_times.get(item_id)
        start_ts, end_ts = self._normalize_langsmith_times(start_ts, end_ts)
        if end_ts is None:
            end_ts = datetime.datetime.now(datetime.UTC)
        if start_ts is None:
            start_ts = end_ts

        name = "HumanMessage" if role == "user" else "AIMessage" if role == "assistant" else None
        if not name:
            return

        # Include the system prompt in AIMessage inputs so engineers can see
        # what instructions the model was given alongside the response.
        inputs: dict[str, str] = {}
        if name == "AIMessage" and getattr(self.ctx, "rendered_system_prompt", None):
            inputs["system_prompt"] = self.ctx.rendered_system_prompt

        # Pass start_time at creation so post() sends it immediately — LangSmith
        # does not allow updating start_time via patch() after the run is posted.
        is_filler = item_id in self._filler_item_ids
        extra = {"metadata": {"filler": is_filler}} if name == "AIMessage" else None
        child = self.root_run.create_child(
            name=name,
            run_type="llm",
            inputs=inputs,
            outputs={"message": message.get("content", "")},
            start_time=start_ts,
            end_time=end_ts,
            extra=extra,
        )
        # child.post() is a blocking HTTP call — offload to a thread
        # to avoid stalling the audio event loop
        await asyncio.to_thread(child.post)

    @staticmethod
    def _extract_raw_event_type(raw_event: Any) -> str | None:
        if raw_event is None:
            return None
        if isinstance(raw_event, dict):
            return raw_event.get("type")
        return getattr(raw_event, "type", None)

    @staticmethod
    def _normalize_langsmith_times(
        start_ts: datetime.datetime | None,
        end_ts: datetime.datetime | None,
    ) -> tuple[datetime.datetime | None, datetime.datetime | None]:
        """Ensure timestamps are present and non-decreasing for LangSmith durations."""
        if start_ts is None and end_ts is None:
            return None, None

        if end_ts is None:
            end_ts = datetime.datetime.now(datetime.UTC)
        if start_ts is None:
            start_ts = end_ts
        if start_ts and end_ts and start_ts > end_ts:
            start_ts = end_ts
        return start_ts, end_ts

    def _handle_raw_model_event(self, raw_event: Any) -> None:
        raw_type = self._extract_raw_event_type(raw_event)
        if raw_type == "transcript_delta":
            # SDK bug workaround: accumulate transcripts before session.py clears them on turn_ended.
            # See KNCK-38461 — SDK 0.6.9+ sends conversation.item.truncate on interrupts, which
            # triggers item_updated after _item_transcripts is already cleared.
            item_id = getattr(raw_event, "item_id", None)
            delta = getattr(raw_event, "delta", None)
            if item_id and delta:
                self._transcript_cache[item_id] = self._transcript_cache.get(item_id, "") + delta
        elif raw_type in {"input_audio_buffer.speech_started", "input_audio_buffer.speech_start"}:
            if not self._last_user_speaking_started_at:
                self._last_user_speaking_started_at = datetime.datetime.now(datetime.UTC)
            if not self._call_state.is_user_speaking:
                self._call_state.mark_user_speaking_started()
        elif raw_type in {"input_audio_buffer.speech_stopped", "input_audio_buffer.speech_stop"}:
            self._last_user_speaking_stopped_at = datetime.datetime.now(datetime.UTC)

    async def _handle_realtime_event(self, event: RealtimeSessionEvent) -> None:
        """Handle events from the realtime session."""
        try:
            if event.type == "audio":
                # Disable flag as agent will start speaking when audio arrives
                # This is a fallback for when history event does not receive 'completed' status
                self._call_state.is_user_speaking = False

                await self._handle_realtime_audio_event(event)

            elif event.type == "audio_interrupted":
                # Ignore interruptions during initial greeting
                if self._is_initial_greeting:
                    logger.info("Ignoring audio interruption during initial greeting")
                # Ignore interruptions during handoff playback — let the safety/transition
                # message and transfer tool call play to completion without being cancelled
                elif self._interrupt_suppression_active:
                    logger.info("Ignoring audio interruption during handoff playback")
                else:
                    logger.info("Sending audio interrupted to Twilio")
                    try:
                        await self.twilio_websocket.send_text(
                            orjson.dumps({"event": "clear", "streamSid": self._stream_sid}).decode("utf-8")
                        )
                    except (RuntimeError, ConnectionError, OSError, WebSocketDisconnect):
                        logger.info("Skipped clear event send - WebSocket already closed")
                    # Clear pacer queue on interruption
                    self._out_frame_q.clear()
                    self._out_partial.clear()
                    self._current_partial_event = None
                    self._first_ulaw_rx_ts = None
                    # Record end_times for interrupted items so they can be traced to LangSmith
                    # (marks will never arrive, so _on_response_completed won't fire)
                    if self._response_last_mark_ids:
                        now = datetime.datetime.now(datetime.UTC)
                        for item_id in self._response_last_mark_ids:
                            if item_id not in self._message_end_times:
                                self._message_end_times[item_id] = now
                        logger.debug(
                            f"Clearing {len(self._response_last_mark_ids)} pending response marks due to interruption"
                        )
                        self._response_last_mark_ids.clear()
                        # Use session._history (not self.history) — history_updated may not
                        # have fired yet, so self.history can be stale. session._history
                        # always has the items; transcript_cache recovers SDK-cleared text.
                        if self.session:
                            history = realtime_history_to_input_list(
                                self.session._history,
                                include_item_id=True,
                                transcript_cache=self._transcript_cache,
                            )
                            self._fire_trace_task(history)
                    # Mark agent as stopped speaking since the response was interrupted
                    if self._call_state.is_agent_speaking:
                        logger.debug("Marking agent as stopped speaking due to interruption")
                        self._call_state.mark_agent_speaking_stopped()

                    # Check if this interrupt was triggered by our own cancel operation
                    if self._expecting_cancel_interrupt:
                        logger.info("Cancel-triggered audio interrupt (not user speech)")
                        self._expecting_cancel_interrupt = False
                        # Don't mark user as speaking - we triggered this, not the user
                    else:
                        logger.info("User speaking - audio interrupted")
                        self._last_user_speaking_started_at = datetime.datetime.now(datetime.UTC)
                        self._call_state.mark_user_speaking_started()
                        self._consecutive_fillers_without_user_audio = 0
                        self._last_user_audio_time = time.time()
            elif event.type == "audio_end":
                logger.info("Audio end")
                # Flush any partial frame when audio ends
                fb = self._frame_bytes
                if 0 < len(self._out_partial) < fb and self._current_partial_event:
                    # pad with μ-law silence to full 160 bytes
                    pad = bytes([self._silence_byte]) * (fb - len(self._out_partial))
                    self._out_partial.extend(pad)
                    # Tag the padded frame with the current event metadata
                    self._out_frame_q.append((bytes(self._out_partial), self._current_partial_event))
                    self._out_partial.clear()
                    self._current_partial_event = None
                self._schedule_next_filler()
            elif event.type == "history_updated":
                if hasattr(self, "ctx"):
                    self.ctx.history = realtime_history_to_input_list(
                        event.history, transcript_cache=self._transcript_cache
                    )
                    self.history = realtime_history_to_input_list(
                        event.history, include_item_id=True, transcript_cache=self._transcript_cache
                    )
                for history_item in event.history:
                    if not isinstance(history_item, UserMessageItem) or history_item.role != "user":
                        continue
                    self._record_user_message_start_time(history_item.item_id)
                    if getattr(history_item, "status", None) == "completed":
                        self._on_user_message_completed(history_item.item_id)
            elif event.type == "guardrail_tripped":
                await self._handle_guardrail_tripped_event(event)
            elif event.type == "input_audio_timeout_triggered":
                await self._handle_input_audio_timeout_triggered_event(event)
            elif event.type == "raw_model_event":
                if type(event.data) is RealtimeModelExceptionEvent:
                    logger.info(f"Realtime model exception: {event.data}")
                    await self._handle_realtime_error_event(event=event)
                else:
                    self._handle_raw_model_event(event.data)
            elif event.type == "agent_end":
                self._fire_trace_task(self.history)
            elif event.type == "error":
                # The handler below either recovers or logs at higher severity if needed.
                logger.info(f"Realtime error event: {event}")
                await self._handle_realtime_error_event(event=event)
            else:
                pass

        except ModelBehaviorError as e:
            logger.info(f"Model behavior error in realtime session: {e}")
            await self._handle_realtime_error_event(event=event, error=e)

        except Exception as e:
            logger.info(f"Error in realtime session loop: {e}")
            try:
                await self._handle_realtime_error_event(event=event)
            except Exception as recovery_error:
                logger.warning(f"Recovery failed after event handling error: {recovery_error}")

    def _record_user_message_start_time(self, item_id: str) -> None:
        """Capture user message start_time on first appearance (from VAD speech_started)."""
        if item_id not in self._message_start_times:
            self._message_start_times[item_id] = self._last_user_speaking_started_at or datetime.datetime.now(
                datetime.UTC
            )

    def _record_user_message_end_time(self, item_id: str) -> None:
        """Capture user message end_time on completion (from VAD speech_stopped)."""
        if item_id in self._message_end_times:
            return
        self._message_end_times[item_id] = self._last_user_speaking_stopped_at or datetime.datetime.now(datetime.UTC)
        self._last_user_speaking_started_at = None
        self._last_user_speaking_stopped_at = None

    def _on_user_message_completed(self, item_id: str) -> None:
        """Handle user message reaching 'completed' status."""
        self._record_user_message_end_time(item_id)
        # Trace immediately so HumanMessage spans reflect actual speech duration
        # rather than waiting for agent_end / _on_response_completed.
        self._fire_trace_task(self.history)
        self._call_state.mark_user_speaking_stopped()
        self._call_state.mark_agent_processing_started()
        logger.debug("User stopped speaking (completed) - rescheduling filler")
        self._schedule_next_filler()

    async def _handle_realtime_error_event(
        self,
        event: RealtimeSessionEvent | None = None,
        error: ModelBehaviorError | None = None,
    ) -> None:
        """Handle runtime errors, optionally restarting the realtime session."""
        logger.info(f"Handling realtime error event: {event or error or 'Unknown error'}")

        # Handle "response_cancel_not_active" error - this is benign and should be ignored.
        # This occurs when we try to cancel a response that has already completed or was never started.
        # Common in race conditions where filler is pending but OpenAI hasn't started generating yet.
        if isinstance(event, RealtimeError) and "response_cancel_not_active" in str(event):
            logger.debug(
                "Ignoring response_cancel_not_active error - no active response to cancel",
                event_type=type(event).__name__,
            )
            return

        # Handle "audio content ... shorter than" error - this is benign and should be ignored.
        # This occurs when we send a response.cancel / truncate but the audio buffer is already
        # shorter than the truncation point (e.g., "Audio content of 100ms is already shorter than 231ms").
        # The response was effectively already done; rebuilding the session would be overkill.
        if isinstance(event, RealtimeError) and "shorter than" in str(event):
            logger.info(
                "Ignoring audio truncation error - audio buffer already shorter than truncation point",
                event_type=type(event).__name__,
                error_detail=str(event),
            )
            return

        # Handle "active response in progress" by forcing cancellation
        # This occurs when response.create is called while OpenAI is still processing a response.
        # When turn_detection.interrupt_response=True, the SDK skips sending response.cancel,
        # but in race conditions (e.g., guardrails, filler messages), explicit cancellation is needed.
        # See: https://github.com/openai/openai-agents-python/issues/1907
        if isinstance(event, RealtimeError) and "already has an active response in progress" in str(event):
            # Recoverable race condition; we cancel and retry below. Not an app error.
            logger.info(
                "Race condition detected: Active response in progress - forcing cancel and retrying",
                event_type=type(event).__name__,
                is_agent_speaking=self.is_agent_speaking,
                is_user_speaking=self.is_user_speaking,
            )

            # Log context for debugging
            if hasattr(self, "ctx") and self.ctx and hasattr(self.ctx, "history"):
                recent_history = self.ctx.history[-3:] if len(self.ctx.history) >= 3 else self.ctx.history
                logger.info(
                    "Active response conflict context - recent conversation history",
                    recent_history=recent_history,
                )

            try:
                if self.session and hasattr(self.session, "_model"):
                    # Step 1: Force cancel the conflicting response
                    # Set flag so the resulting audio_interrupted event is not mistaken for user speech
                    self._expecting_cancel_interrupt = True
                    await self.session._model.send_event(RealtimeModelSendInterrupt(force_response_cancel=True))
                    logger.info("Successfully sent forced response.cancel")

                    # Step 2: Wait for cancellation to take effect
                    cancellation_confirmed = False
                    for i in range(10):
                        await asyncio.sleep(0.05)
                        if not self.session._model._ongoing_response:
                            logger.info(f"Confirmed response cancellation after {(i + 1) * 50}ms")
                            cancellation_confirmed = True
                            break

                    # Step 3: Retry response.create only if cancellation was confirmed.
                    # If not confirmed, retrying would hit the same error and cause churn.
                    if cancellation_confirmed:
                        await self.session._model.send_event(
                            RealtimeModelSendRawMessage(
                                message={
                                    "type": "response.create",
                                    "other_data": {
                                        "response": {
                                            "output_modalities": ["audio"],
                                        },
                                    },
                                }
                            )
                        )
                        logger.info("Successfully retried response.create after cancellation")
                    else:
                        logger.warning("Cancellation not confirmed after 500ms, skipping response.create retry")
            except Exception as e:
                logger.warning(f"Failed to recover from active response conflict: {e}")
            finally:
                self._expecting_cancel_interrupt = False
            return

        try:
            await self._recover_realtime_session(event)
        except Exception as send_error:
            logger.info(f"Error recovering session: {send_error}")

    async def _handle_realtime_audio_event(self, event: RealtimeSessionEvent) -> None:
        """Handle audio events from the realtime session - queue for pacer."""
        ulaw_bytes = event.audio.data

        if ulaw_bytes:
            # Prevent unbounded growth of _mark_data if Twilio doesn't send mark events
            if len(self._mark_data) >= self._mark_data_max_size:
                # Remove oldest half of marks if we hit the limit
                oldest_marks = sorted(self._mark_data.keys(), key=int)[: self._mark_data_max_size // 2]
                for old_mark_id in oldest_marks:
                    del self._mark_data[old_mark_id]
                logger.warning(f"Cleared {len(oldest_marks)} stale marks from _mark_data to prevent memory leak")

            # Store mark data for playback tracking (marks sent by pacer after all chunks are sent)
            self._mark_counter += 1
            mark_id = str(self._mark_counter)
            self._mark_data[mark_id] = (
                event.audio.item_id,
                event.audio.content_index,
                len(ulaw_bytes),
            )

            # Track last mark for ALL items
            self._response_last_mark_ids[event.audio.item_id] = mark_id

            # Mark that agent is speaking when first audio arrives
            if not self._call_state.is_agent_speaking:
                is_filler = self._next_speech_is_filler
                self._next_speech_is_filler = False  # Reset flag after use
                if is_filler:
                    self._filler_item_ids.add(event.audio.item_id)
                logger.info(f"Agent started speaking (is_filler={is_filler})")
                self._message_start_times[event.audio.item_id] = datetime.datetime.now(datetime.UTC)
                self._call_state.mark_agent_speaking_started(is_filler=is_filler)

            # First audio: note time
            if self._first_ulaw_rx_ts is None:
                self._first_ulaw_rx_ts = time.monotonic()
                self._startup_span.mark("first_audio_received")

            # Track which event this audio belongs to
            event_metadata = (mark_id, event.audio.item_id, event.audio.content_index)

            # If we don't have a partial frame, set the current event for new frames
            # If we do have a partial frame, it belongs to the previous event
            # and new frames will belong to this event
            if self._current_partial_event is None or self._current_partial_event != event_metadata:
                self._current_partial_event = event_metadata

            # STRICT ALIGNMENT: accumulate then slice exact 160-byte frames
            # Tag each frame with the event metadata it belongs to
            self._out_partial.extend(ulaw_bytes)
            fb = self._frame_bytes

            while len(self._out_partial) >= fb:
                frame = bytes(self._out_partial[:fb])
                del self._out_partial[:fb]
                # Store frame with its event metadata
                # Use current_partial_event which tracks the event for frames being sliced
                self._out_frame_q.append((frame, self._current_partial_event))

                # After slicing a complete frame, if we have more data, it might be from a new event
                # But we'll handle that when the next event arrives - for now, keep the current event
                # until we've consumed all data from this event

            # If we've consumed all the partial data, clear the current event
            # This means all frames from the current event have been sliced
            if len(self._out_partial) == 0:
                self._current_partial_event = None
            # Otherwise, if we have a partial frame, it still belongs to the current event
            # and will be completed when more data arrives (from the same or different event)

            # Start pacer if not running
            if not self._pacer_running:
                self._pacer_running = True
                self._pacer_task = asyncio.create_task(self._pacer_loop(), name="twilio_ulaw_pacer")
                self._pacer_task.add_done_callback(_log_background_task_exception)

    def _record_initial_greeting_latency_if_ready(self) -> None:
        """One-shot stamp of `initial_greeting_latency_ms` on the root run.

        Safe to call on every pacer tick — the guard flag prevents duplicate
        writes once the value lands.
        """
        if self._initial_greeting_latency_recorded:
            return
        if record_initial_greeting_latency(self.root_run, self._startup_span) is not None:
            self._initial_greeting_latency_recorded = True

    async def _pacer_loop(self, skip_prebuffer: bool = False) -> None:
        """
        Strict real-time pacer with anchored timing and low-water jitter guard (optimized for low latency):
          - Exactly 1 μ-law frame (160 B) every 20 ms.
          - Scheduler anchored to a monotonic base (prevents drift).
          - Reduced prebuffer (6 frames/~120ms) and startup timeout (~120ms) for faster first response.
          - Smaller underrun grace window (3ms vs 8ms) for tighter latency.
          - Never drop/skip audio; silence only when truly empty.
        """
        fb = self._frame_bytes
        tick = self._tick_seconds
        pre_n = self._prebuffer_frames
        silence_frame = bytes([self._silence_byte]) * fb

        try:
            # Reduced prebuffer and startup timeout for lower latency
            if not skip_prebuffer:
                start_wait = time.monotonic()
                while self._pacer_running:
                    if (
                        len(self._out_frame_q) >= pre_n
                        or (time.monotonic() - start_wait) > self._pacer_startup_timeout_sec
                    ):
                        break
                    await asyncio.sleep(0.005)

            # ---- Anchored scheduler ----
            base = time.monotonic()
            tick_idx = 1  # first send at base + tick
            # current_event: tuple[str, str, int] | None = None  # Track current event being processed
            pending_mark_id: str | None = None  # Mark ID to send when event changes

            while self._pacer_running:
                next_deadline = base + tick * tick_idx
                now = time.monotonic()

                if now < next_deadline:
                    await asyncio.sleep(next_deadline - now)
                    now = time.monotonic()
                else:
                    # If we were late (event loop hiccup), just continue—do NOT burst.
                    pass

                # ---- Low-water jitter guard (reduced grace window for lower latency) ----
                # If we're about to underrun (0 or 1 frame in queue), give upstream a brief window
                # to deliver the next frame before we inject silence.
                if len(self._out_frame_q) <= 1:
                    grace_deadline = time.monotonic() + self._pacer_underrun_grace_sec
                    while len(self._out_frame_q) == 0 and time.monotonic() < grace_deadline and self._pacer_running:
                        # micro-poll without blocking the loop too long
                        await asyncio.sleep(0.001)

                # ---- Send exactly one frame per tick ----
                if self._out_frame_q:
                    frame, frame_event = self._out_frame_q.popleft()
                    mark_id, _, _ = frame_event
                    self._startup_span.mark("first_utterance_sent")
                    self._record_initial_greeting_latency_if_ready()

                    # Peek at next frame to check if it's from a different event
                    next_frame_event = None
                    if self._out_frame_q:
                        _, next_frame_event = self._out_frame_q[0]

                    if pending_mark_id is None:
                        pending_mark_id = mark_id

                    # If we ran out of frames or the current frame is not the same as the pending mark id, send the mark
                    if next_frame_event is None or (next_frame_event and next_frame_event[0] != mark_id):
                        await self._send_mark(pending_mark_id)
                        pending_mark_id = mark_id
                else:
                    frame = silence_frame  # truly empty → maintain cadence with silence
                    # send any pending mark if self._out_frame_q is empty
                    await self._send_mark(pending_mark_id)
                    pending_mark_id = None

                if not self._stream_sid:
                    # Stream not initialized yet, skip sending.
                    # Advance tick_idx so the anchored scheduler sleeps to the next deadline.
                    tick_idx += 1
                    continue

                # Check if call is still active before sending
                if not self.call_active:
                    break

                try:
                    payload = base64.b64encode(frame).decode("utf-8")
                    audio_delta = {"event": "media", "streamSid": self._stream_sid, "media": {"payload": payload}}

                    await self.twilio_websocket.send_text(orjson.dumps(audio_delta).decode("utf-8"))
                except (RuntimeError, ConnectionError, OSError, WebSocketDisconnect):
                    # Websocket is closed, exit the loop gracefully
                    logger.info("Stopping pacer loop, WebSocket closed. ")
                    break

                tick_idx += 1  # advance anchored schedule

            # Send mark for the last event if we have one pending - sanity check
            # Only attempt if websocket is still usable (not closed during loop exit)
            if pending_mark_id and self._stream_sid and self.call_active:
                try:
                    await self._send_mark(pending_mark_id)
                except (RuntimeError, ConnectionError, OSError, WebSocketDisconnect):
                    # WebSocket already closed, mark is not needed
                    logger.debug("Skipped final mark send - WebSocket already closed")

        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                # External cancellation - must propagate to avoid leaks
                raise
            logger.debug("Pacer loop cancelled")
        except (RuntimeError, ConnectionError, WebSocketDisconnect):
            # Handle websocket closure gracefully
            logger.info("Pacer loop stopped: WebSocket closed")
        except Exception as e:
            logger.exception(f"Pacer loop exception: {type(e).__name__}: {e}")
        finally:
            self._pacer_running = False
            self._first_ulaw_rx_ts = None

    async def _send_mark(self, mark_id: str) -> None:
        """Send a mark event to Twilio."""
        if not mark_id:
            return
        if not self._stream_sid or not self.call_active or not self._pacer_running:
            return

        # Check websocket state safely
        try:
            client_state = getattr(self.twilio_websocket, "client_state", None)
            if not client_state or getattr(client_state, "name", None) != "CONNECTED":
                return

            application_state = getattr(self.twilio_websocket, "application_state", None)
            if application_state and getattr(application_state, "name", None) != "CONNECTED":
                return
        except Exception:
            return

        try:
            await self.twilio_websocket.send_text(
                orjson.dumps({"event": "mark", "streamSid": self._stream_sid, "mark": {"name": mark_id}}).decode(
                    "utf-8"
                )
            )
        except (RuntimeError, ConnectionError, OSError, WebSocketDisconnect) as e:
            if isinstance(e, RuntimeError) and "close message has been sent" in str(e):
                logger.debug("Skipped mark send - WebSocket closing")
                return
            logger.info(f"Error sending mark event: {e}")
            # Don't re-raise WebSocketDisconnect - it's expected during cleanup
            if not isinstance(e, WebSocketDisconnect):
                raise

    @staticmethod
    def _format_guardrail_output(guardrail_result) -> str:
        """Format a guardrail result for logging, handling different output types."""
        info = guardrail_result.output.output_info
        reasoning = getattr(info, "reasoning", None)
        if reasoning is None and isinstance(info, Mapping):
            reasoning = info.get("reasoning", "")
        safe_response = getattr(info, "safe_response", None)
        if safe_response is None and isinstance(info, Mapping):
            safe_response = info.get("safe_response", "")
        return f"{reasoning or safe_response or str(info)}"

    async def _handle_guardrail_tripped_event(self, event) -> None:
        """Handle guardrail tripped events by formatting and sending appropriate messages."""
        guardrail_output_str = "\n".join([self._format_guardrail_output(o) for o in event.guardrail_results])
        logger.info(f"Guardrails tripped: {guardrail_output_str}")

        guardrail_tripped_message_str = GUARDRAIL_TRIPPED_MESSAGE.format(
            guardrail_message=guardrail_output_str,
            language_code=self._get_language_code(),
        )
        logger.info(f"Sending guardrail tripped message: {guardrail_tripped_message_str}")

        await asyncio.sleep(ASYNCIO_SLEEP_GUARDRAIL_TRIPPED_TIME)
        await self.session.send_message(guardrail_tripped_message_str)
        self._schedule_next_filler()

    async def _handle_input_audio_timeout_triggered_event(self, event) -> None:
        """Handle input audio timeout triggered events."""
        logger.info("Input audio timeout triggered")
        await self._send_input_audio_timeout_message()

    async def _inject_message(self, msg: str) -> None:
        """Send a playback-inject prompt, clearing any stale filler flag first.

        A filler sent just before the inject would otherwise leave
        _next_speech_is_filler=True, causing the inject's response to be
        mis-tagged as filler and triggering a spurious retry.
        """
        self._next_speech_is_filler = False
        await self.session.send_message(msg)

    async def _send_input_audio_timeout_message(self) -> None:
        """Send the input audio timeout prompt once until new user audio arrives."""
        if self._is_initial_greeting:
            logger.info("Not sending input audio timeout message: initial greeting in progress")
            return
        if self._interrupt_suppression_active:
            logger.info("Not sending filler: handoff in progress")
            return
        if not settings.send_filler_messages:
            logger.info("Not sending input audio timeout message: settings.send_filler_messages is false")
            return
        if not self.call_active:
            logger.debug("Not sending input audio timeout message: call is no longer active")
            return
        if not self._is_websocket_connected():
            logger.info("Not sending input audio timeout message: WebSocket is disconnected")
            return
        if not self.agent or not self.session:
            logger.info("Not sending input audio timeout message: self.agent or self.session is None")
            return
        if not self._session_ready.is_set():
            logger.info("Not sending input audio timeout message: self._session_ready is not set")
            return

        # Check all conditions that prevent sending fillers (user speaking, agent processing, etc.)
        if not self._call_state.can_send_filler():
            if self._call_state.is_user_speaking:
                logger.info("Not sending input audio timeout message: user is currently speaking")
            elif self._call_state.is_agent_speaking:
                logger.info("Not sending input audio timeout message: agent is currently speaking")
            return

        # Suppress filler during the grace window after the thinker finishes.
        # This closes the narrow race between thinker_running→False and the Responder
        # starting to speak, preventing a filler from playing before critical responses
        # (e.g. emergency safety content). Disable by setting thinker_response_grace_seconds=0.
        ctx = getattr(self, "ctx", None)
        thinker_finished_at = getattr(ctx, "thinker_finished_at", None)
        grace_seconds = getattr(settings, "thinker_response_grace_seconds", 0)
        if (
            isinstance(thinker_finished_at, int | float)
            and isinstance(grace_seconds, int | float)
            and time.monotonic() - thinker_finished_at < grace_seconds
        ):
            logger.info("Skipping filler: within thinker response grace period")
            return

        try:
            # Select filler message based on handoff state, thinker state, and escalation threshold.
            transfer_summary_flow_active = getattr(ctx, "transfer_summary_requested", False)
            destructive_handoff_in_progress = getattr(ctx, "handoff_in_progress", False)
            thinker_running = getattr(ctx, "thinker_running", False)
            language_code = self._get_language_code()

            # Suppress generic fillers while a destructive handoff tool is in flight.
            # Keep the transfer-summary path active so we can continue nudging toward
            # transfer_to_staff_voice until the handoff completes.
            if destructive_handoff_in_progress and not transfer_summary_flow_active:
                logger.info("Skipping filler: handoff in progress")
                return

            # Only count user-silence fillers toward dead line detection — agent
            # thinking fillers are not evidence the user has left.
            if not thinker_running:
                self._consecutive_fillers_without_user_audio += 1

            should_escalate = (
                settings.filler_escalation_enabled
                and self._consecutive_fillers_without_user_audio >= settings.filler_escalation_threshold
                and not thinker_running
            )

            if transfer_summary_flow_active:
                # During transfer handoff, always nudge toward transfer_to_staff_voice
                # to prevent thinker hijacking the handoff.
                message = FILLER_HANDOFF_MESSAGE.format(language_code=language_code)
            elif should_escalate:
                logger.warning(
                    "Filler escalation triggered",
                    consecutive_fillers=self._consecutive_fillers_without_user_audio,
                    thinker_running=thinker_running,
                )
                message = FILLER_ESCALATION_MESSAGE.format(language_code=language_code)
            elif thinker_running:
                message = FILLER_THINKER_ACTIVE_MESSAGE.format(language_code=language_code)
            else:
                message = FILLER_IDLE_MESSAGE.format(language_code=language_code)

            logger.info(
                f"Sending filler message ({self._consecutive_fillers_without_user_audio} consecutive)",
                transfer_summary_flow_active=transfer_summary_flow_active,
                destructive_handoff_in_progress=destructive_handoff_in_progress,
                escalated=should_escalate and not transfer_summary_flow_active,
                thinker_running=thinker_running,
            )
            # Mark that the next speech will be a filler message
            self._next_speech_is_filler = True
            await self.session.send_message(message)
            self._schedule_next_filler()
        except Exception as e:
            self._next_speech_is_filler = False  # Reset on error
            logger.info(f"Error sending input audio timeout message: {e}")

    async def _recover_realtime_session(
        self,
        event: RealtimeModelExceptionEvent | RealtimeSessionEvent | None = None,
    ) -> None:
        """Recover from a realtime session exception."""
        logger.info(f"Recovering from realtime session exception - event: {event}")
        history = self.ctx.history

        starting_agent = self.agent.agent()

        # Cancel the existing realtime session task if it exists
        if hasattr(self, "_realtime_session_task") and self._realtime_session_task:
            if self._realtime_session_task is asyncio.current_task():
                logger.info("Recovery called from within session loop - skipping self-cancel")
            else:
                logger.info("Cancelling existing realtime session task")
                self._realtime_session_task.cancel()
                try:
                    await self._realtime_session_task
                except asyncio.CancelledError:
                    # Check if this is external cancellation of recovery itself
                    task = asyncio.current_task()
                    if task is not None and task.cancelling() > 0:
                        raise
                    logger.info("Realtime session task cancelled successfully")

        self._session_ready.clear()

        # Close the old session before creating a new one to prevent resource leaks
        if self.session:
            try:
                logger.info("Closing old realtime session before recovery")
                await self.session.close()
            except Exception as e:
                logger.warning(f"Error closing old session during recovery: {e}")
            finally:
                self.session = None
        else:
            self.session = None

        metadata = {
            "environment": settings.environment,
            "property-id": self.ctx.ask_request.property_id,
            "resident-id": self.ctx.ask_request.product_info.knock_resident_id,
            "company-id": self.ctx.ask_request.product_info.uc_company_id.id
            if self.ctx.ask_request.product_info.uc_company_id
            else None,
            "product": self.ctx.ask_request.product,
            "property-name": self.ctx.ask_request.product_info.property_name,
            "call-sid": self.ctx.ask_request.product_info.call_sid,
            "pmc-id": self.ctx.ask_request.product_info.pmc_id,
            "pmc-name": self.ctx.ask_request.product_info.pmc_name,
            "openai-group-url": self.ctx.openai_group_url,
        }

        await self._setup_realtime_session(starting_agent, metadata)
        await self._enter_realtime_session()
        self._start_realtime_session_loop()

        recovery_message = RECOVERY_MESSAGE.format(
            history=history,
            language_code=self._get_language_code(),
        )
        logger.info(f"Sending recovery greeting: {recovery_message}")

        await self.session.send_message(recovery_message)

    async def _handle_twilio_message(self, message: dict[str, Any]) -> None:
        """Handle incoming messages from Twilio Media Stream."""
        try:
            event = message.get("event")

            if event == "connected":
                logger.info("Twilio media stream connected")
            elif event == "start":
                logger.info("Twilio start message received")
                # Twilio can stream inbound media before the welcome response is triggered.
                # Keep caller audio suppressed from call start until greeting completion.
                self._is_initial_greeting = True
                self._startup_span.mark("start_event_received")
                start_data = message.get("start", {})
                self._stream_sid, self._call_sid, self._payload = await self._process_start_payload(start_data)
                self._startup_span.mark("start_payload_processed")
                logger.info(f"Media stream started with SID: {self._stream_sid}")

                # Start pacer immediately with a stream of 0xFF silence
                # to keep Twilio's engine healthy and prevent audio splicing artifacts
                if not self._pacer_running:
                    self._pacer_running = True
                    self._pacer_task = asyncio.create_task(
                        self._pacer_loop(skip_prebuffer=True), name="twilio_ulaw_pacer"
                    )
                    self._pacer_task.add_done_callback(_log_background_task_exception)

                await self._agent_setup(payload=self._payload)
            elif event == "media":
                await self._handle_media_event(message)
            elif event == "mark":
                await self._handle_mark_event(message)
            elif event == "stop":
                # twilio calls can be ended by 1) end_call or 2) user hanging up
                # if we do not find the end_call tool as the last tool
                # it MUST be the user hanging up and we record call_hangup

                # IMMEDIATELY mark call as inactive to stop background tasks (filler, etc.)
                # This must happen BEFORE any async logging that could be interrupted
                self.call_active = False
                self._next_filler_time = None

                ctx = getattr(self, "ctx", None)
                ended_by_agent = getattr(ctx, "call_ended_by_agent", False) if ctx else False
                if not ended_by_agent:
                    post_trace_marker(
                        self.root_run,
                        "call_hangup",
                        message="Call ended by Twilio media stream stop event | user hung up",
                    )

                logger.info("Media stream stopped")

                await self._cleanup_call()
            else:
                logger.info(f"Unknown event: {event}")
        except Exception as e:
            logger.exception(f"Error handling Twilio message: {e}")

    async def _handle_media_event(self, message: dict[str, Any]) -> None:
        """Handle audio data from Twilio - buffer it before sending to OpenAI."""
        media = message.get("media", {})
        payload = media.get("payload", "")

        if payload:
            try:
                # Decode base64 audio from Twilio (µ-law format)
                ulaw_bytes = base64.b64decode(payload)

                # Add the original µ-law to buffer for OpenAI (they expect µ-law)
                self._audio_buffer.extend(ulaw_bytes)

                # Send buffered audio if we have enough data
                if len(self._audio_buffer) >= self.BUFFER_SIZE_BYTES:
                    await self._flush_audio_buffer()

            except Exception as e:
                logger.info(f"Error processing audio from Twilio: {e}")

        # Only reset the filler timer when VAD confirms the user is actually speaking —
        # raw media frames include silence/comfort-noise and would prevent fillers from ever firing.
        if self._call_state.is_user_speaking:
            self._schedule_next_filler()

    async def _handle_mark_event(self, message: dict[str, Any]) -> None:
        """Handle mark events from Twilio to update playback tracker."""
        try:
            mark_data = message.get("mark", {})
            mark_id = mark_data.get("name", "")

            # Look up stored data for this mark ID
            if mark_id in self._mark_data:
                item_id, item_content_index, byte_count = self._mark_data[mark_id]

                # Convert byte count back to bytes for playback tracker
                audio_bytes = b"\x00" * byte_count  # Placeholder bytes

                # Update playback tracker
                self.playback_tracker.on_play_bytes(item_id, item_content_index, audio_bytes)
                logger.debug(f"Playback tracker updated: {item_id}, index {item_content_index}, {byte_count} bytes")
                self._schedule_next_filler()

                if self._is_last_mark_for_response(item_id, mark_id):
                    del self._response_last_mark_ids[item_id]
                    await self._on_response_completed(item_id, mark_id)

                del self._mark_data[mark_id]

        except Exception as e:
            logger.info(f"Error handling mark event: {e}")

    def _is_last_mark_for_response(self, item_id: str, mark_id: str) -> bool:
        """Check if this mark is the final one for its response."""
        return item_id in self._response_last_mark_ids and self._response_last_mark_ids[item_id] == mark_id

    async def _on_response_completed(self, item_id: str, last_mark_id: str) -> None:
        """Callback when a response finishes playback (Twilio mark confirmed)."""
        self._message_end_times[item_id] = datetime.datetime.now(datetime.UTC)
        self._fire_trace_task(self.history)
        self._update_speaking_state_after_response()
        await self._handle_greeting_completion()

    def _update_speaking_state_after_response(self) -> None:
        """Mark agent as done speaking if no more pending responses."""
        if not self._response_last_mark_ids:
            logger.info("Agent finished speaking (no more pending responses)")
            self._call_state.mark_agent_speaking_stopped()

    async def _handle_greeting_completion(self) -> None:
        """Clear greeting flag, swap in full agent if needed, and discard buffered audio."""
        if not self._is_initial_greeting:
            return

        if hasattr(self, "ctx"):
            self.ctx.welcome_greeting_delivered = True

        # If the greeting agent fast-path is active, await the full agent before swapping
        if self._full_agent_task is not None:
            if not self._full_agent_task.done():
                logger.info("Greeting finished before full agent ready — waiting for agent init")
            try:
                await asyncio.wait_for(
                    self._full_agent_task,
                    timeout=settings.greeting_agent_init_timeout_seconds,
                )
            except TimeoutError:
                logger.error("Full agent init timed out — transferring to staff")
                self._full_agent_task.cancel()
                self._full_agent_task = None
                self._is_initial_greeting = False
                await self._transfer_call_on_init_failure()
                return
            except Exception:
                logger.exception("Full agent init failed — transferring to staff")
                self._full_agent_task = None
                self._is_initial_greeting = False
                await self._transfer_call_on_init_failure()
                return
            self._full_agent_task = None

        if self.session and self.agent:
            if self._parallel_init_span:
                self._parallel_init_span.mark("agent_swap_start")
            await self.session.update_agent(self.agent.agent())
            if self._parallel_init_span:
                self._parallel_init_span.mark("agent_swap_end")
                logger.info("Swapped in full agent after greeting")
            else:
                logger.info("Refreshed session instructions with welcome_greeting_delivered=True")

        if self._audio_buffer:
            logger.info(f"Discarding {len(self._audio_buffer)} bytes of user audio captured during greeting")
            self._audio_buffer.clear()

        self._is_initial_greeting = False
        logger.info("Initial greeting completed - allowing interruptions")

    async def _flush_audio_buffer(self) -> None:
        """Send buffered audio to OpenAI.

        Serialized via ``self._flush_lock`` so that the message loop and the periodic
        buffer flush loop cannot both enter the snapshot/send/clear sequence and
        double-send the same bytes. See KNCK-39464.
        """
        async with self._flush_lock:
            if not self._audio_buffer or not self.session:
                return

            # Don't send user audio to OpenAI during initial greeting
            if self._is_initial_greeting:
                return

            # Don't send user audio to OpenAI while a handoff message is playing —
            # prevents VAD from firing and queueing spurious responses.
            # Discard the buffer so suppressed audio doesn't replay later.
            if self._interrupt_suppression_active and self._call_state.is_agent_speaking:
                self._audio_buffer.clear()
                return

            try:
                # Snapshot and clear BEFORE awaiting send_audio. Clearing under the
                # lock ensures any concurrent flusher that was waiting on the lock
                # sees an empty buffer when it acquires and returns early.
                buffer_data = bytes(self._audio_buffer)
                self._audio_buffer.clear()
                if settings.twilio_input_audio_noise_reduction_enabled:
                    buffer_data = apply_noise_reduction(buffer_data, settings.openai_audio_format)
                await self.session.send_audio(buffer_data)
                self._last_buffer_send_time = time.time()

            except Exception as e:
                logger.info(f"Error sending buffered audio to OpenAI: {e}")

    async def _buffer_flush_loop(self) -> None:
        """Periodically flush audio buffer to prevent stale data."""
        try:
            while self.call_active:
                await asyncio.sleep(self.CHUNK_LENGTH_S)  # Check every 50 ms

                # If buffer has data and it's been too long since last send, flush it
                current_time = time.time()
                if self._audio_buffer and current_time - self._last_buffer_send_time > self.CHUNK_LENGTH_S * 2:
                    await self._flush_audio_buffer()

        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise
            logger.debug("Buffer flush loop cancelled")
        except Exception as e:
            logger.info(f"Error in buffer flush loop: {e}")

    async def _input_audio_inactivity_loop(self) -> None:
        """Monitor for extended silence (user or LLM) and prompt the agent."""
        try:
            while self.call_active:
                await asyncio.sleep(1.0)

                # Don't engage safety nets until the session is fully set up — WebSocket
                # client_state can briefly be non-CONNECTED during protocol upgrade.
                if not self._session_ready.is_set():
                    continue

                if self._is_session_expired():
                    logger.warning(
                        f"Session exceeded max duration ({settings.max_voice_session_duration_seconds}s) - terminating"
                    )
                    self._shutdown_reason = self._shutdown_reason or "session_timeout"
                    await self._cleanup_call()
                    break

                if not self._is_websocket_connected():
                    logger.info("WebSocket disconnected - triggering cleanup from inactivity loop")
                    self._shutdown_reason = self._shutdown_reason or "websocket_disconnect"
                    await self._cleanup_call()
                    break

                if self._is_dead_line():
                    logger.warning(
                        f"Dead line detected: {self._consecutive_fillers_without_user_audio} consecutive fillers "
                        f"without user audio (max: {settings.max_consecutive_fillers_without_user_audio}) - terminating"
                    )
                    self._shutdown_reason = self._shutdown_reason or "filler_deadline"
                    await self._cleanup_call()
                    break

                if not settings.send_filler_messages:
                    continue
                if not self.agent or not self.session:
                    continue

                if self._next_filler_time is None:
                    self._schedule_next_filler()
                    continue

                if self._call_state.is_user_speaking:
                    self._schedule_next_filler()
                    continue

                if time.time() >= self._next_filler_time:
                    logger.info("Detected extended silence")
                    await self._send_input_audio_timeout_message()
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                # External cancellation - must propagate to avoid leaks
                raise
            logger.debug("Input audio inactivity monitor cancelled")
        except Exception as e:
            logger.warning(f"Inactivity monitor crashed - safety nets disabled for this call: {e}", exc_info=True)

    def _is_websocket_connected(self) -> bool:
        """Check if the Twilio WebSocket is still connected."""
        try:
            if not hasattr(self.twilio_websocket, "client_state"):
                return False
            if not self.twilio_websocket.client_state:
                return False
            return self.twilio_websocket.client_state.name == "CONNECTED"
        except Exception:
            return False

    def _is_session_expired(self) -> bool:
        """Check if the session has exceeded the maximum duration."""
        if self._session_start_time is None:
            return False
        elapsed = time.time() - self._session_start_time
        return elapsed >= settings.max_voice_session_duration_seconds

    def _is_dead_line(self) -> bool:
        """Check if the line appears dead (no user engagement).

        A line is considered dead if we've sent too many consecutive filler
        messages without receiving any user audio. This indicates the user
        has likely hung up but the WebSocket hasn't properly closed.
        """
        return self._consecutive_fillers_without_user_audio >= settings.max_consecutive_fillers_without_user_audio

    async def _process_start_payload(self, start_payload: dict):
        """
        Process the start payload from Twilio.
        Logic adapted from here: https://github.com/knockrentals/renter-ai-agent/blob/alpha/routers/agent_api.py#L396

        Args:
            start_payload (dict): The start payload from Twilio.

        Returns:
            stream_sid (str): The stream SID from Twilio.
            call_sid (str): The call SID from Twilio.
            payload (dict): The decoded B64 JSON payload from Twilio.
        """
        stream_sid = start_payload.get("streamSid")
        call_sid = start_payload.get("callSid")

        customParameters = start_payload.get("customParameters")
        payload_str = customParameters.get("payload", None)

        # If payload_str is None we are testing
        if payload_str is None:
            payload_str = encode_object(self._load_test_payload())
            logger.warning("No payload provided, using default payload")

        payload = decode_object(payload_str)

        # token = customParameters.get("token")

        # TODO: add token validation from here: https://github.com/knockrentals/renter-ai-agent/blob/alpha/routers/agent_api.py#L405

        # override call_sid to ensure we have the correct call_sid during processing of the request, just in case and for testing purposes
        payload["call_sid"] = call_sid
        payload["product_info"]["call_sid"] = call_sid

        # Start recording if configured
        await self._start_recording(payload, call_sid)

        return stream_sid, call_sid, payload

    @staticmethod
    def _load_test_payload() -> dict[str, Any]:
        payload_test_dict: dict[str, Any] | None = None
        if settings.twilio_test_payload:
            try:
                payload_text = Path(settings.twilio_test_payload).read_text(encoding="utf-8")
                payload_test_dict = json.loads(payload_text)
                if not isinstance(payload_test_dict, dict):
                    raise ValueError("twilio_test_payload must be a JSON object")
                logger.info(f"Using test payload from {settings.twilio_test_payload}")
            except Exception as e:
                payload_test_dict = None
                logger.error(f"Failed to load test payload from {settings.twilio_test_payload}: {e}. Using default.")
        if payload_test_dict is None:
            payload_test_dict = copy.deepcopy(examples.ASK_REQUEST_RESIDENT_VOICE_KNCK)
        return payload_test_dict

    async def _start_recording(self, payload: dict, call_sid: str) -> None:
        """
        Start Twilio recording for the call if configured.
        Reference: https://github.com/knockrentals/renter-ai-agent/blob/alpha/routers/agent_api.py#L417

        Args:
            payload (dict): The decoded payload from Twilio.
            call_sid (str): The call SID from Twilio.
        """
        try:
            if payload.get("product_info", {}).get("should_record", False):
                api_key, api_secret, account_sid = get_twilio_credentials()
                twilio_client = TwilioClient(api_key, api_secret, account_sid)

                # Twilio REST client is synchronous; offload to thread to avoid blocking the event loop
                try:
                    self._recording_task = asyncio.create_task(
                        asyncio.to_thread(
                            twilio_client.calls(call_sid).recordings.create,
                            recording_status_callback=f"{settings.knock_internal_api_url}/v1/relay/voice/handlers/hangup-with-recording",
                            recording_channels="dual",
                        )
                    )
                    self._recording_task.add_done_callback(_log_background_task_exception)
                    logger.info(f"Recording started for call {call_sid}")
                except Exception:
                    # Best-effort: if scheduling fails, fallback to direct call (will block)
                    twilio_client.calls(call_sid).recordings.create(
                        recording_status_callback=f"{settings.knock_internal_api_url}/v1/relay/voice/handlers/hangup-with-recording",
                        recording_channels="dual",
                    )
                    logger.info(f"Recording started for call {call_sid}")
        except Exception as e:
            logger.error(f"Error starting Twilio recording for call {call_sid} - {e}")

    def _schedule_data_curation_logging(self) -> None:
        """Snapshot session history and fire off data curation logging as a background task."""
        if not self.session:
            return

        try:
            history_snapshot = list(getattr(self.session, "_history", []))
            context = self.session._context_wrapper.context
        except Exception as e:
            logger.warning(f"Error capturing data curation snapshot: {e}")
            return

        try:
            self._data_curation_task = asyncio.create_task(
                log_data_curation_event_for_realtime_history(
                    history_snapshot, context, transcript_cache=dict(self._transcript_cache)
                )
            )
            self._data_curation_task.add_done_callback(_log_background_task_exception)
        except Exception as e:
            logger.warning(f"Error scheduling data curation logging task: {e}")

    def _fill_missing_end_times(self) -> None:
        """Stamp items that never received a Twilio mark.
        Uses start_time (zero duration) — we don't know when playback actually
        stopped, so 0 is more honest than 'end of call'."""
        for item_id, start in self._message_start_times.items():
            if item_id not in self._message_end_times:
                self._message_end_times[item_id] = start

    async def _cleanup_call(self):
        """Clean up all running tasks and resources to prevent memory leaks."""
        if self._cleanup_called:
            return
        self._cleanup_called = True
        logger.info("Cleaning call")

        # End-of-session task event: PENDING + escalation when handoff routing
        # could not be confirmed; COMPLETED (with or without escalation) otherwise.
        # Scheduled before teardown so a failure in teardown doesn't swallow the
        # event. Guarded in case ctx was never created. Fire-and-forget — actual
        # publish runs in a background task drained at the end of cleanup.
        if _has_task_event_context(getattr(self, "ctx", None)):
            publish_task_event_fire_and_forget(
                kafka_application_context.task_event_producer,
                build_end_of_session_event(self.ctx),
                self.ctx.pending_activity_publishes,
            )
        if self._shutdown_reason:
            inputs = {}
            if self._consecutive_fillers_without_user_audio > 0:
                inputs["consecutive_fillers"] = self._consecutive_fillers_without_user_audio
                inputs["max_allowed"] = settings.max_consecutive_fillers_without_user_audio
            post_trace_marker(
                self.root_run,
                self._shutdown_reason,
                inputs=inputs,
                message=f"Call terminated: {self._shutdown_reason}",
            )
        # Wait for the deferred LangSmith URL resolution before curation needs it.
        # Short timeout — if it hasn't resolved by now it's been seconds; don't block cleanup.
        if self._langsmith_url_task and not self._langsmith_url_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._langsmith_url_task), timeout=2.0)
            except (TimeoutError, asyncio.CancelledError, Exception):
                logger.warning("LangSmith URL not resolved before cleanup — Kafka event will have null URL")
        self._schedule_data_curation_logging()  # Must run before _close_session nulls self.session
        self._deactivate()
        await self._cancel_background_tasks()
        await self._startup_span.finalize(self.root_run)
        if self._parallel_init_span:
            await self._parallel_init_span.finalize(self.root_run)
        await self._finalize_tracing()
        self._clear_audio_state()
        self._clear_tracing_state()
        await self._close_session()
        await self._close_agent()
        self._clear_context()
        await self._await_data_curation()

        # Drain any in-flight task-event publishes scheduled during the call
        # (IN_PROGRESS at start + end-of-session above). Awaited last so the
        # background tasks have the maximum window to complete before this
        # coroutine returns and the WebSocket teardown finishes.
        if _has_task_event_context(getattr(self, "ctx", None)):
            await drain_pending_publishes(self.ctx.pending_activity_publishes)

        logger.info("Call cleanup completed successfully")

    async def _finalize_tracing(self) -> None:
        """Fill missing end_times and trace all messages before state is cleared."""
        # Drain any in-flight fire-and-forget trace tasks before the final pass
        if self._pending_trace_tasks:
            await asyncio.gather(*self._pending_trace_tasks, return_exceptions=True)
            self._pending_trace_tasks.clear()
        self._fill_missing_end_times()
        # Snapshot session._history directly — always complete, unlike the event-driven
        # self.history which can miss post-thinker responses if history_updated fired
        # before transcript_delta events populated the item's transcript.
        history = (
            realtime_history_to_input_list(
                self.session._history, include_item_id=True, transcript_cache=self._transcript_cache
            )
            if self.session
            else self.history
        )
        await self.trace_messages_to_langsmith(history)

    def _deactivate(self) -> None:
        """Mark the call inactive and stop background scheduling."""
        self.call_active = False
        self._next_filler_time = None
        self._consecutive_fillers_without_user_audio = 0
        self._filler_item_ids.clear()
        self._call_state.reset()
        self._pacer_running = False

    def _clear_audio_state(self) -> None:
        """Clear audio queues, buffers, and playback tracking."""
        self._out_frame_q.clear()
        self._out_partial.clear()
        self._current_partial_event = None
        self._first_ulaw_rx_ts = None
        self._audio_buffer.clear()
        self._mark_data.clear()
        self._response_last_mark_ids.clear()

    def _clear_tracing_state(self) -> None:
        """Clear conversation history and tracing timestamps."""
        self.history.clear()
        self.viewed_messages.clear()
        self._message_start_times.clear()
        self._message_end_times.clear()
        self._transcript_cache.clear()
        self._last_user_speaking_started_at = None
        self._last_user_speaking_stopped_at = None

    async def _cancel_background_tasks(self) -> None:
        """Cancel and await all background tasks concurrently."""
        current_task = asyncio.current_task()
        tasks_to_cleanup = [
            ("pacer", self._pacer_task),
            ("realtime session", self._realtime_session_task),
            ("buffer flush", self._buffer_flush_task),
            ("message loop", self._message_loop_task),
            ("inactivity monitor", self._inactivity_monitor_task),
            ("recording", self._recording_task),
            ("langsmith url", self._langsmith_url_task),
            ("full agent init", self._full_agent_task),
        ]

        tasks_to_await: list[tuple[str, asyncio.Task]] = []
        for task_name, task in tasks_to_cleanup:
            if not task or task.done() or task is current_task:
                continue
            logger.info(f"Cancelling {task_name} task")
            task.cancel()
            tasks_to_await.append((task_name, task))

        if not tasks_to_await:
            return

        try:
            results = await asyncio.gather(*(task for _, task in tasks_to_await), return_exceptions=True)
        except asyncio.CancelledError:
            # Do NOT re-raise — complete as much cleanup as possible to prevent resource leaks
            results = []
            logger.debug("Task cleanup gather cancelled during cleanup")

        for (task_name, _task), result in zip(tasks_to_await, results):
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                logger.warning(f"Error awaiting {task_name} task: {result}")

    async def _close_session(self) -> None:
        """Close the RealtimeSession with a timeout.

        Uses asyncio.shield so session.close() completes even if our task
        is externally cancelled (e.g., by MCP pool health check cancel scope leak).
        """
        if not self.session:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self.session.close()), timeout=1.5)
        except TimeoutError:
            # Expected on hung-up calls during cleanup; not an application error.
            logger.info("Timed out closing realtime session")
        except asyncio.CancelledError:
            # Expected during external teardown.
            logger.info("Session close interrupted by external cancellation")
        except Exception as e:
            logger.warning(f"Error closing realtime session: {e}")
        finally:
            self.session = None

    async def _close_agent(self) -> None:
        """Close the agent context manager (MCP connections).

        Uses asyncio.shield so __aexit__ completes even if our task
        is externally cancelled, preventing MCP connection leaks.

        The 5s timeout gives the MCP SDK enough time to cancel its
        anyio task groups (_receive_loop, post_writer) during cleanup.
        """
        if not self.agent:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self.agent.__aexit__(None, None, None)), timeout=5.0)
            logger.info("Agent context manager cleaned up successfully")
        except TimeoutError:
            # Expected on hung-up calls during cleanup; not an application error.
            logger.info("Timed out closing agent")
        except asyncio.CancelledError:
            # Expected during external teardown.
            logger.info("Agent close interrupted by external cancellation")
        except Exception as e:
            logger.warning(f"Error closing agent: {e}")
        finally:
            self.agent = None

    async def _await_data_curation(self) -> None:
        """Wait for the data curation task to finish, cancel if it takes too long."""
        if not self._data_curation_task or self._data_curation_task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._data_curation_task), timeout=10.0)
        except TimeoutError:
            # Expected on hung-up calls during cleanup; the task is cancelled below.
            logger.info("Data curation task timed out — cancelling")
            self._data_curation_task.cancel()
            try:
                await self._data_curation_task
            except (asyncio.CancelledError, Exception):
                pass
        except asyncio.CancelledError:
            logger.debug("Data curation await cancelled during cleanup")
        except Exception as e:
            logger.warning(f"Error awaiting data curation task: {e}")

    def _clear_context(self) -> None:
        """Break circular references while preserving trace data."""
        if not hasattr(self, "ctx"):
            return
        self.ctx.history = []
        self.ctx.mcp_tool_calls = []
        # Break circular references to allow GC:
        #   handler → ctx._session_handler → handler  (set in _agent_setup line ~259)
        #   handler → ctx.call_state_manager → handler._call_state  (set in _agent_setup line ~255)
        self.ctx._session_handler = None
        self.ctx.call_state_manager = None
        self._call_state._send_message_fn = None
        # langsmith_run_tree is kept — needed for final RunTree.patch()

    async def _transfer_call(self, reason: str) -> None:
        """Transfer call to human agent via Twilio REST API."""
        if not self._call_sid:
            logger.error("Cannot transfer: call_sid not available", reason=reason)
            return

        try:
            api_key, api_secret, account_sid = get_twilio_credentials()
            twilio_client = TwilioClient(api_key, api_secret, account_sid)
            base_url = settings.knock_internal_api_url

            call = await asyncio.to_thread(
                twilio_client.calls(self._call_sid).update,
                twiml=_build_transfer_twiml(base_url),
                status_callback=f"{base_url}/v1/relay/voice/clay/callback",
            )
            logger.info("Transferred call", call_sid=self._call_sid, reason=reason, status=call.status)
        except Exception as transfer_error:
            logger.error("Failed to transfer call", call_sid=self._call_sid, reason=reason, error=str(transfer_error))

    async def _transfer_call_on_init_failure(self) -> None:
        """Transfer call to staff when background full-agent init fails."""
        logger.warning("Full agent init failed - transferring call to staff", call_sid=self._call_sid)
        await self._transfer_call("agent_init_failure")

    async def _transfer_call_on_validation_failure(self, error: Exception, payload: dict[str, Any]) -> None:
        """Transfer call to human agent when payload validation fails."""
        error_str = str(error)
        validation_reason = "missing_required_fields" if "Missing required fields" in error_str else "other"
        logger.warning(
            "AskRequest validation failed - transferring call",
            event_type="validation_failed",
            validation_reason=validation_reason,
            call_sid=self._call_sid,
            error=error_str,
            payload=payload,
            product=payload.get("product"),
            property_name=payload.get("product_info", {}).get("property_name"),
        )
        marker_inputs = build_validation_failure_marker_inputs(
            error_str=error_str,
            validation_reason=validation_reason,
            payload=payload,
            variant=getattr(self, "variant", "v1"),
        )
        post_trace_marker(
            self.root_run,
            "validation_failure",
            inputs=marker_inputs,
            message=f"Validation failed: {validation_reason}",
        )
        await self._transfer_call("validation_failure")


def encode_object(obj: dict):
    serialized_bytes = orjson.dumps(obj)  # orjson.dumps returns bytes
    encoded_bytes = base64.b64encode(serialized_bytes)
    encoded_string = encoded_bytes.decode("utf-8")
    return encoded_string


def decode_object(encoded_string: str):
    encoded_bytes = encoded_string.encode("utf-8")
    serialized_bytes = base64.b64decode(encoded_bytes)
    return orjson.loads(serialized_bytes)  # orjson.loads expects bytes


class TwilioWebSocketManager:
    def __init__(self):
        self.active_handlers: dict[str, TwilioHandler] = {}

    async def new_session(self, websocket: WebSocket) -> TwilioHandler:
        """Create and configure a new session."""
        handler = TwilioHandler(websocket)
        handler_id = str(id(handler))
        self.active_handlers[handler_id] = handler
        logger.info(
            "Creating twilio handler",
            handler_id=handler_id,
            active_handler_count=len(self.active_handlers),
        )
        return handler

    async def cleanup_handler(self, handler_id: str) -> None:
        """Remove a handler from active_handlers to prevent memory leak.

        Called from media_stream_endpoint's finally block to guarantee deregistration.
        Handler internal cleanup (_cleanup_call) is separate and runs first.

        Args:
            handler_id: str(id(handler)) key used during registration in new_session.
        """
        if handler_id in self.active_handlers:
            handler = self.active_handlers.pop(handler_id)
            logger.info(f"Removed handler {handler_id} from active_handlers (count: {len(self.active_handlers)})")
            del handler
        else:
            logger.debug(f"Handler {handler_id} not found in active_handlers")
