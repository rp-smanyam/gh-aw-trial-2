import asyncio
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_leasing.api.model import AskRequest, Persona
from agent_leasing.util.helpers import resolve_greeting_placeholders

logger = structlog.getLogger()


class HandoffResult(BaseModel):
    """Outcome of a handoff/transfer tool invocation.

    Set by transfer_to_staff_voice and emergency_service_transfer_* tools in
    both success and failure paths. Read at call cleanup to choose between
    COMPLETED (no escalation / with escalation) or PENDING (with escalation)
    when publishing task events.

    routing_confirmed semantics:
      True  — Twilio / dispatch API accepted the routing instruction.
      False — routing attempted but API threw an exception or returned an
              error status; downstream consumer cannot assume the caller
              was successfully transferred.
    """

    tool: str
    reason: str
    routing_confirmed: bool
    summary: str | None = None
    twilio_call_status: str | None = None

    @field_validator("twilio_call_status", mode="before")
    @classmethod
    def _coerce_call_status(cls, v):
        # Twilio returns status as a string in prod, but test mocks often pass
        # Mock objects. Coerce non-string non-None values to str so construction
        # never fails on a non-critical metadata field.
        if v is None or isinstance(v, str):
            return v
        return str(v)


class SessionScope(BaseModel):
    """This context is passed around within Agents SDK for convenience."""

    # Fields excluded from the Redis SessionScope cache (text-channel turn-to-turn state).
    #
    # Convention:
    #   * Use this set for fields that should NOT round-trip through Redis but ARE
    #     valid arguments to other `model_dump()` callers (logs, debug, snapshots).
    #   * Use `Field(exclude=True)` ONLY when a field must be excluded from every
    #     `model_dump()` call site (e.g. `pending_activity_publishes` holds
    #     `asyncio.Task` objects that aren't JSON-serializable in any context).
    _CACHE_EXCLUDE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "ask_request",
            "logging_metadata",
            "history",
            "langsmith_project_name",
            "langsmith_trace_url",
            "openai_trace_url",
            "rendered_system_prompt",
            "track_voice_thinker_runs",
            "voice_thinker_runs",
            # Stale-on-restore: default_factory must run fresh on each from_cache().
            "current_time",
            # Per-turn trace headers; rebuilt every turn before use, never read across turns.
            "langsmith_run_tree",
            # Mid-turn transient flags. Restoring True from a crashed mid-turn write
            # would block interrupt suppression / fillers on the next session.
            "handoff_in_progress",
            "office_closed_warning_given",
            "transfer_summary_requested",
            "thinker_running",
            "thinker_finished_at",
            "call_management_in_progress",
        }
    )

    # Use timezone-aware UTC so office-hours checks remain deterministic.
    current_time: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # This is the JSON request body sent to /v1/agent/ask
    # Note: This field is excluded from cache serialization (see to_cache/from_cache)
    ask_request: AskRequest | None = None

    previous_response_id: str | None = None
    openai_conversation_id: str | None = None
    thread_id: str | None = None

    langsmith_run_tree: dict | None = None

    # Transient: stores the fully rendered system prompt so it can be
    # attached to the AIMessage LangSmith span after Runner.run() completes.
    rendered_system_prompt: str | None = None

    langsmith_project_name: str | None = None

    langsmith_trace_url: str | None = None

    openai_trace_id: str | None = None
    openai_trace_url: str | None = None
    openai_group_id: str | None = None
    openai_group_url: str | None = None

    history: list[dict] | None = []

    # Set to True after the initial voice greeting completes playback.
    # Used to gate the Welcome Workflow and language detection in VOICE_RESPONDER.md.
    welcome_greeting_delivered: bool = False

    logging_metadata: dict = {}

    handoff: bool = False
    handoff_message: str | None = None

    # Record a security bypass in context so we don't make 2 LLM calls in one turn to determine this
    security_bypass: bool = False

    # Property marketing summary from LDP. Populated only when
    # settings.property_marketing_info_tool_enabled is False (legacy prompt-injection path).
    property_data: str | None = ""

    @property
    def property_id(self):
        return self.ask_request.property_id

    @property
    def prospect_id(self):
        return self.ask_request.prospect_id

    @property
    def custom_greeting(self) -> str | None:
        """Custom greeting from Knowledge Base, if configured for this property.

        KB greetings can embed [first_name]/[last_name]/[property_name] tokens; resolve
        them here since the prompt instructs the model to say the greeting verbatim.
        """
        if self.ask_request and self.ask_request.product_info:
            product_info = self.ask_request.product_info
            return resolve_greeting_placeholders(
                product_info.custom_greeting,
                first_name=product_info.uc_first_name,
                last_name=product_info.uc_last_name,
                property_name=product_info.property_name,
            )
        return None

    @property
    def has_openai_server_history(self) -> bool:
        """Whether OpenAI server-side state already has prior turns.

        Gates first-turn-only logic (Insight News, prefetch) and thinker
        history dedup.  Uses previous_response_id as the signal in both modes
        because it is only set after the first Runner.run completes — unlike
        openai_conversation_id which is created before the first call.
        """
        return bool(self.previous_response_id)

    # These values would normally come from a more complex data structure like AskRequest,
    # passed in through an endpoint
    has_sms_consent: bool = False

    # Tool outputs can be saved in the context and later accessed. For example, they
    # can be injected into the system prompt.
    packages: Any = None
    service_requests: Any = None
    signed_up_community_events: Any = None

    # The disabled modules and tools (None means not yet fetched)
    disabled_modules: list[str] | None = None
    disabled_tools: list[str] | None = None

    # Track MCP tool calls for data curation logging
    mcp_tool_calls: list[dict] = Field(default_factory=list)

    # TODO(KNCK-39556 PR 2): wire `drain_pending_publishes(...)` into
    # non-voice request-complete and voice call-end hooks.
    pending_activity_publishes: set[asyncio.Task] = Field(default_factory=set, exclude=True)

    # Once-per-conversation gate for the FRUSTRATED_USER activity. Persisted
    # via the SessionScope cache so a repeat-frustration turn after restart
    # also stays suppressed.
    frustrated_user_emitted: bool = False

    # Voice-only: per-thinker invocation trace of inner tool calls.
    # Used by tests to reconstruct the chronological tool timeline under
    # the responder/thinker architecture.  Disabled by default so
    # production doesn't pay the serialization cost.
    track_voice_thinker_runs: bool = False
    voice_thinker_runs: list[dict[str, Any]] = Field(default_factory=list)

    # Tracks whether the agent intentionally ended or redirected the call (e.g., transfer tools).
    # Used to distinguish a user hangup from a tool-driven call stop.
    call_ended_by_agent: bool = False

    # Populated by handoff tools (transfer_to_staff_voice, emergency_service_transfer_*)
    # in both success AND failure paths. Read at cleanup to publish the right
    # task-event status:
    #   None                                → COMPLETED, no escalation.
    #   routing_confirmed=True              → COMPLETED, with escalation.
    #   routing_confirmed=False             → PENDING,   with escalation.
    handoff_result: HandoffResult | None = None

    # Stable per Redis-cached session lifetime. Persists across messages
    # within a session via SessionScope cache; regenerates when the cache
    # entry expires and a fresh SessionScope is constructed. Used by the
    # task-event payload builder so SMS/EMAIL task.ids split correctly across
    # a person's multiple sessions (their chat_session_id is upstream's
    # stream_id, which is person-level rather than session-level).
    session_marker: str = Field(default_factory=lambda: uuid4().hex)

    # Voice: True while a call management tool (end_call, transfer_to_staff_voice,
    # emergency_service_transfer_basic) is actively executing its destructive Twilio /
    # Knock API work. Used to reject concurrent invocations that would race each other
    # and fire duplicate transfer/hangup calls (see KNCK-39358).
    call_management_in_progress: bool = False

    # True after the first phone validation failure in ESR advanced — forces escalation on the second attempt.
    esr_phone_retry_attempted: bool = False

    # True while the agent is handing the caller off to a human (ESR or transfer_to_staff).
    # Triggers interrupt_response suppression so the safety/transition message and tool call
    # play without being cancelled by user audio.
    handoff_in_progress: bool = False

    # Voice: True after the transfer tool asks for a summary and before the transfer completes.
    # Read by twilio_handler to send handoff-aware fillers instead of thinker nudges.
    transfer_summary_requested: bool = False

    # Voice: one-time closed-hours transfer warning gate for the active call.
    # Prevents repeating the office-closed warning after it has already been spoken once.
    office_closed_warning_given: bool = False

    # Voice: True while the thinker tool is actively processing a request.
    # Read by twilio_handler to decide whether to escalate filler messages.
    thinker_running: bool = False
    # Monotonic timestamp set when the thinker finishes. Used by twilio_handler
    # to suppress filler during the grace window before the Responder starts speaking.
    thinker_finished_at: float | None = None

    # SMS consent tracking
    sms_consent_status: str | None = None
    sms_consent_recorded: bool = False
    sms_needs_consent_prompt: bool = False  # True when agent should ask for consent (first time only)
    pending_sms_query: str | None = (
        None  # Original query stored when consent was originally not granted, processed after START
    )
    voice_sms_consent_confirmed: bool = (
        False  # True after user confirms SMS consent AND opt-in verified this session (VOICE only)
    )

    # Identity verification status (set by verify_resident_identity tool)
    # Keyed by channel (e.g. "SMS", "EMAIL") so each channel verifies independently.
    identity_verified: dict[str, bool] = Field(default_factory=dict)
    identity_verified_with_birth_year: dict[str, bool] = Field(default_factory=dict)
    verification_attempts: dict[str, int] = Field(default_factory=dict)

    # Language tracking for multilingual support
    language_code: str = "en"

    # ── Channel-aware verification helpers ──────────────────────────

    def is_identity_verified(self, channel: str) -> bool:
        """Return whether identity has been verified for *channel*."""
        return self.identity_verified.get(channel, False)

    def is_identity_verified_with_birth_year(self, channel: str) -> bool:
        """Return whether identity + birth year has been verified for *channel*."""
        return self.identity_verified_with_birth_year.get(channel, False)

    def get_verification_attempts(self, channel: str) -> int:
        """Return the number of failed verification attempts for *channel*."""
        return self.verification_attempts.get(channel, 0)

    def set_identity_verified(self, channel: str, value: bool = True) -> None:
        """Mark identity as verified (or not) for *channel*."""
        self.identity_verified[channel] = value

    def set_identity_verified_with_birth_year(self, channel: str, value: bool = True) -> None:
        """Mark identity + birth year as verified (or not) for *channel*."""
        self.identity_verified_with_birth_year[channel] = value

    def increment_verification_attempts(self, channel: str) -> None:
        """Increment the failed-attempt counter for *channel*."""
        self.verification_attempts[channel] = self.get_verification_attempts(channel) + 1

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    @property
    def persona(self) -> Persona:
        """Determine the persona based on the product type. Delegates to AskRequest.persona."""
        if self.ask_request is None:
            logger.warning("No ask_request available, defaulting to Persona.PROSPECT")
            return Persona.PROSPECT
        return self.ask_request.persona

    def reset(self):
        """Reset the context to its initial state."""
        self.security_bypass = False

    def to_cache(self) -> dict:
        """Serialize for cache storage, excluding transient fields."""
        return self.model_dump(exclude=self._CACHE_EXCLUDE_FIELDS)

    @classmethod
    def from_cache(cls, data: dict) -> "SessionScope":
        """Reconstruct from cached data.

        Handles migration from the old flat-boolean verification fields
        to the new channel-keyed dicts.  Old sessions simply lose their
        verification state (the user re-verifies), which is safe.
        """
        for field in ("identity_verified", "identity_verified_with_birth_year"):
            if isinstance(data.get(field), bool):
                data[field] = {}
        if isinstance(data.get("verification_attempts"), int):
            data["verification_attempts"] = {}
        return cls(**data)
