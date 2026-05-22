import json
import os
from typing import Any, Literal

import structlog
from agents import ModelSettings
from pydantic import Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DOTENV = os.path.join(os.path.dirname(__file__), "../../.env")
PHONE_NUMBER_MAX_FLOOR = (
    20_000_000_000  # Phone number safe floor - prevent numeric clamping from breaking phone numbers
)
logger = structlog.getLogger(__name__)


class Config(BaseSettings):
    """
    Stores environment variables, setting reasonable defaults where possible.

    To override, set these in a .env file in the root directory or pass
    them in. Case is managed, so `ASK_ENDPOINT` sets `ask_endpoint`.

    OpenAI Configuration:
    - openai_base_url: Custom base URL for OpenAI HTTP API requests (e.g., for data residency).
      When set, the tracing endpoint is automatically configured to {base_url}/traces/ingest
      to keep traces in the same region as API calls.
    - openai_base_wss_url: Custom WebSocket URL for OpenAI realtime/voice connections.
      Used by voice_ui_handler and twilio_handler for WebSocket-based voice interactions.
    """

    app_name: str = "agent-leasing"
    environment: str = "dev"
    port: int = 8000

    ask_endpoint: str = "http://127.0.0.1:8000/v1/agent/ask"
    model: str = "gpt-5.1"
    model_reasoning_effort: str = "none"
    model_verbosity: str = "low"

    # Resident One Settings
    resident_one_model: str = "gpt-5.4"
    resident_one_model_reasoning_effort: str = "low"
    resident_one_model_verbosity: str = "low"
    resident_one_model_max_tokens: int = 4096
    # If 0, the version passed in through a payload will always be honored
    # 0 will result in INSTRUCTIONS.md getting used
    resident_one_prompt_version: int = 0
    # Controls the maximum number of turns during the agent loop execution
    resident_one_max_turns: int = 10

    realtime_model: str = "gpt-realtime-2"
    # Reasoning effort for the realtime voice model. The Literal mirrors the
    # SDK's RealtimeReasoningConfig.effort, so pydantic rejects invalid values
    # (e.g. REALTIME_REASONING_EFFORT=loww) at startup rather than letting them
    # flow to OpenAI and fail at session-create. The Realtime 2.0 prompting
    # guide recommends "low" for customer-support workloads.
    realtime_reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] = "low"
    model_temperature: float = 1.0
    model_service_tier: str = "priority"
    guardrail_model: str = "gpt-5.4-nano"

    # Security Guardrail - Moderation API call timeout (seconds). Caps the OpenAI
    # moderations.create() call so a slow upstream can't stall the whole turn.
    security_guardrail_moderation_timeout_seconds: float = 5.0
    # Security Guardrail - When the moderation API errors or times out, fail open
    # (let content through with a warning) instead of propagating the exception,
    # which would cause the resident to receive the canned fallback response
    # instead of the agent's real reply. Kill switch — flip to False to restore
    # pre-#1599 behavior (propagate the exception).
    security_guardrail_fail_open_on_moderation_error: bool = True

    # Security Guardrail - Per-category thresholds for moderation scores (0.0 to 1.0)
    # Lower values are more strict, higher values are more permissive
    security_guardrail_thresholds: dict[str, float] = {
        # Sexual content - lowered to catch sexual requests disguised in property-management context
        "sexual": 0.15,
        "sexual_minors": 0.1,  # Zero tolerance
        # Violence and harm
        "violence": 0.35,
        "violence/graphic": 0.35,
        # Self-harm
        "self-harm": 0.25,
        "self-harm/intent": 0.25,
        "self-harm/instructions": 0.25,
        # Hate and harassment
        "hate": 0.25,
        "hate/threatening": 0.25,
        "harassment": 0.35,
        "harassment/threatening": 0.25,
        # Illegal activities
        "illicit": 0.30,
        "illicit_violent": 0.30,
    }

    # Guardrail Configuration - Control which guardrails are enabled
    # Valid input guardrail names: security, pii, prompt_injection, prisma_airs
    # Valid output guardrail names: security, pii, fair_housing, competitor_blocking, prisma_airs, unauthorized_promises, legal_advice
    enabled_input_guardrails: list[str] = [
        "security",
        "pii",
        "prompt_injection",
        "prisma_airs",
    ]
    enabled_output_guardrails: list[str] = [
        "security",
        "pii",
        "fair_housing",
        "competitor_blocking",
        "prisma_airs",
        "unauthorized_promises",
        "legal_advice",
    ]

    mcp_max_output_length: int = 100000  # a standard service request status will be #1.5k
    # MCP Tool Input Guardrails - Limits for tool arguments

    mcp_max_input_length: int = 1_000

    mcp_min_numeric_value: int = 0
    mcp_max_numeric_value: int = 1_000_000_000

    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_base_wss_url: str = ""

    # Per-call timeout for OpenAI HTTP API requests. Sits above the healthy
    # LLM-call distribution on alpha (max ~10s) but below the nginx 60s
    # upstream-read ceiling, so pathological hangs (we observed a 56s
    # outlier during an MCP slowdown) are still caught. See data/KNCK-39536.md.
    openai_request_timeout_seconds: float = 30.0

    # OpenAI SDK retry count. Kept at 1 (SDK default is 2) so worst-case
    # wall time per logical call is timeout * 2 = 60s — at the nginx wall
    # but no worse. Retries here cover transient connection errors that
    # tests rely on; the per-conversation race against /responses (which
    # `max_retries=0` would partially suppress) is mostly cosmetic since
    # the orphan tool-call from the aborted call commits server-side
    # regardless of retry behavior. See data/KNCK-39690.md.
    openai_max_retries: int = 1

    @property
    def openai_wss_full_endpoint(self):
        base = self.openai_base_wss_url or "wss://api.openai.com/v1/realtime"
        return f"{base}?model={self.realtime_model}"

    @field_validator("openai_base_url")
    @classmethod
    def validate_http_url(cls, v: str) -> str:
        """Validate that openai_base_url is a valid HTTP/HTTPS URL if provided."""
        if not v:  # Empty string is allowed (means "not set")
            return v

        # Use Pydantic's HttpUrl for validation
        try:
            # HttpUrl will validate and raise ValueError if invalid
            HttpUrl(v)
        except Exception as e:
            raise ValueError(f"Invalid openai_base_url: '{v}'. Must be a valid HTTP or HTTPS URL. Error: {e}")

        return v

    @field_validator("openai_base_wss_url")
    @classmethod
    def validate_websocket_url(cls, v: str) -> str:
        """Validate that openai_base_wss_url is a valid WebSocket URL if provided."""
        if not v:  # Empty string is allowed (means "not set")
            return v

        # Validate WebSocket URL format
        if not (v.startswith("ws://") or v.startswith("wss://")):
            raise ValueError(f"Invalid openai_base_wss_url: '{v}'. Must start with 'ws://' or 'wss://'")

        # Basic structure validation
        if len(v.split("://", 1)) < 2 or not v.split("://", 1)[1]:
            raise ValueError(f"Invalid openai_base_wss_url: '{v}'. Must include a valid domain after the protocol")

        return v

    # Prisma AIRS API settings for content moderation
    prisma_airs_api_key: str = ""
    prisma_airs_api_url: str = ""
    prisma_airs_profile_name: str = ""
    prisma_airs_blocking_mode: bool = False

    log_level: str = "INFO"
    # Default to JSON logs (production-first approach)
    # Set LOG_JSON_FORMAT=false for local development
    log_json_format: bool = False
    langsmith_tracing: bool = False
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_api_key: str = ""

    # Facilities MCP Server - Used for maintenance and service requests
    facilities_mcp_server: str = "http://127.0.0.1:8042/"
    facilities_mcp_auth_enabled: bool = True
    facilities_mcp_auth_client_secret: str = "SECRET"
    facilities_mcp_auth_token_endpoint: str = "https://www-sat.realpage.com/login/identity/connect/token"
    facilities_mcp_auth_client_id: str = "ai-agent-facilities"
    facilities_mcp_auth_scopes: str = "facilitiescommonapi facilitiesinspectionsapi facilitiesservicerequestsapi"

    # Facilities Thinker API
    facilities_thinker_api_host: str = "http://localhost:1080"
    facilities_thinker_api_enabled: bool = False
    facilities_thinker_self_service_enabled: bool = False
    # When True (and facilities_thinker_api_enabled=True), SR prefetch uses MCP
    # instead of the direct facilities thinker API
    sr_prefetch_via_mcp: bool = False

    # Knock MCP Server - Used for SMS and resident-related operations
    knock_mcp_server: str = "http://127.0.0.1:8042/"
    knock_mcp_auth_enabled: bool = True
    knock_mcp_auth_client_secret: str = "SECRET"
    knock_mcp_auth_token_endpoint: str = "https://www-qa.realpage.com/login/identity/connect/token"
    knock_mcp_auth_client_id: str = "mcp-knock"
    knock_mcp_auth_scopes: str = "mcp-knock"

    # Loft MCP Server - Used for packages and community features
    loft_mcp_server: str = "http://127.0.0.1:8042/"
    loft_mcp_auth_enabled: bool = True
    loft_mcp_auth_client_secret: str = "SECRET"
    loft_mcp_auth_token_endpoint: str = "https://www-dev.realpage.com/login/identity/connect/token"
    loft_mcp_auth_client_id: str = "LuminaVa"
    loft_mcp_auth_scopes: str = "onesite-lr-mcp onesite-ldp-mcp"

    # OneSite MCP Server - Used for property management and ledger operations
    onesite_mcp_server: str = "http://127.0.0.1:8042/"
    onesite_mcp_auth_enabled: bool = True
    onesite_mcp_auth_client_secret: str = "SECRET"
    onesite_mcp_auth_token_endpoint: str = "https://www-sat.realpage.com/login/identity/connect/token"
    onesite_mcp_auth_client_id: str = "LuminaVA"
    onesite_mcp_auth_scopes: str = "onesite-lr-mcp onesite-ldp-mcp"
    # Feature flag for new mcp-onesite rent format (current_balance vs total_balance_due)
    # Set to False to use legacy format with modify_get_rent_information post-processor
    onesite_new_rent_format: bool = True

    ldp_auth_enabled: bool = False
    ldp_login_client_id: str = ""
    ldp_login_client_secret: str = "SECRET"
    ldp_login_token_endpoint: str = ""
    ldp_rp_api_url: str = "http://localhost:1080"

    ldp_modules_all_enabled: bool = False

    # When True, property marketing info is surfaced via the get_property_marketing_info
    # function tool rather than being injected into the system prompt.
    property_marketing_info_tool_enabled: bool = True

    books_host: str = "http://localhost:1080"
    books_auth_enabled: bool = True
    books_auth_endpoint: str = "https://www-qa.realpage.com/login/identity/connect/token"
    books_auth_client_id: str = "renter-ai"
    books_auth_client_secret: str = "SECRET"
    books_auth_scopes: str = "bluebookapi unifiedsettingsapi"

    emergency_dispatch_url: str = "http://localhost:1080/inboundIVR/api/voice/ResAICreateEngineDispatch/PropertyID"
    emergency_service_transfer_advanced_enabled: bool = True
    # Kill switch for caller-interrupt suppression during voice handoffs.
    # Covers both emergency (ESR) and non-emergency (transfer_to_staff) paths.
    interrupt_suppression_enabled: bool = True

    # When True, use the OpenAI Conversations API (conversation_id) instead of
    # previous_response_id for conversation continuity.  This makes the
    # conversation_id shareable with external services (e.g. Facilities API).
    use_conversations_api: bool = False

    # RPCC emergency transfer — SIP endpoint for Genesys Cloud.
    # Must be set explicitly per environment (the prod trunk is a real number).
    rpcc_sip_endpoint: str = ""
    emergency_service_transfer_rpcc_enabled: bool = True

    # Handoff inactivity settings (SMS/EMAIL)
    # AI resumes after this amount of inactivity following a handoff (e.g., "1m", "3d")
    handoff_inactivity_ttl: str = "3d"

    # KNCK-39169: use _deliver_stopped flag instead of _tasks.clear() in anyio patch.
    # Default on (new behavior). Kill switch: set ANYIO_PATCH_PRESERVE_TASKS_ENABLED=false
    # to revert to the old _tasks.clear() behavior if the new approach causes issues.
    anyio_patch_preserve_tasks_enabled: bool = True

    # Startup latency instrumentation — logs timing for auth, MCP handshake, LDP, etc.
    startup_latency_logging_enabled: bool = False

    # Parallel voice startup: greeting agent speaks immediately while full agent
    # (LDP + MCP + prefetch) initialises in the background.
    greeting_agent_enabled: bool = False

    otel_enabled: bool = False
    otel_exporter_otlp_protocol: str = "http/protobuf"
    # Per-export deadline for OTLP exporters (logs/metrics/traces). Default 30s
    # gives Elastic APM headroom for slow round-trips; library default of 10s
    # was producing DEADLINE_EXCEEDED noise (#1551, #1553, #1554).
    otel_exporter_timeout_seconds: int = Field(default=30, ge=1)
    agentic_evals_endpoint: str = ""
    agentic_evals_token: str = ""
    elastic_endpoint: str = ""
    elastic_token: str = ""

    model_config = SettingsConfigDict(env_file=DOTENV, env_file_encoding="utf-8", extra="ignore")
    identity_secret_token: str = "secret"

    # Max identity verification attempts before forcing staff transfer.
    # Default 2 (one retry). Set to 1 to disable retry (old behavior).
    max_identity_verification_attempts: int = 2

    # When False, all channels skip identity verification (like CHAT).
    identity_verification_enabled: bool = True

    # When True, verify_resident_identity uses the candidate-generation
    # implementation (#1491) instead of the cascading-fallback implementation.
    # Default False for safe rollout. Kill switch — flip off to revert.
    use_candidate_generation_verifier: bool = False

    # Optional welcome message sections. Default is a minimal welcome (greeting + closing
    # question only). Populate this list to enable additional sections:
    #   "services" — capabilities line ("I can help with ...")
    #   "insights" — active service requests, packages, and community events mentions
    # Setting "insights" also enables the first-turn insight prefetch.
    welcome_message_sections: list[Literal["services", "insights"]] = []

    # Knock
    # from https://github.com/knockrentals/renter-ai-agent/projects/renter_ai/resident/configs.yaml
    knock_internal_api_url: str = "https://alpha-api.knocktest.com"

    # Twilio
    twilio_auth_token: str = "secret"
    knock_twilio_account_sid: str = "secret"
    knock_twilio_api_key: str = "secret"
    knock_twilio_api_secret: str = "secret"

    # Realtime Voice Settings
    realtime_voice: str = "marin"
    realtime_voice_speed: float = 1.1
    realtime_turn_detection_type: str = "semantic_vad"
    realtime_turn_detection_eagerness: str = "low"
    realtime_turn_detection_interrupt_response: bool = True
    realtime_turn_detection_create_response: bool = True
    realtime_input_audio_noise_reduction: Literal["near_field", "far_field"] = "near_field"
    openai_audio_format: Literal["g711_ulaw"] = "g711_ulaw"
    transcription_model: str = "gpt-realtime-whisper"

    twilio_input_audio_noise_reduction_enabled: bool = False
    send_filler_messages: bool = True
    # filler_delay_mean_seconds < 8 risks race conditions
    filler_delay_mean_seconds: float = 8.0  # First filler after ~ns silence
    filler_delay_std_seconds: float = 1.5  # Tight variance for predictable 4.5-7.5s range

    max_voice_session_duration_seconds: float = 3600.0  # 1 hour max
    max_consecutive_fillers_without_user_audio: int = 5  # Dead line detection

    # Filler Handling Strategy - How to handle active filler messages when thinker responds
    # "cancel": Immediately cancel any active filler (fastest, may cut off mid-sentence)
    # "wait": Wait for filler to complete naturally (smoothest, may add latency)
    # "hybrid": Wait up to timeout, then cancel if still active (recommended balance)
    filler_handling_strategy: Literal["cancel", "wait", "hybrid"] = "hybrid"
    filler_wait_timeout_seconds: float = 1.5  # Max wait time before forcing cancel (hybrid/wait only)

    # Filler Escalation - nudge responder to call thinker when stuck in filler loop
    filler_escalation_enabled: bool = True  # Kill switch for escalation behavior
    filler_escalation_threshold: int = 2  # Escalate after N fillers with no thinker running

    # Playback Detection Settings
    playback_start_timeout_seconds: float = 1.0  # Max wait for speech to start
    playback_end_timeout_seconds: float = 10.0  # Max wait for speech to complete
    playback_settle_delay_seconds: float = 0.2  # Settle delay after speech completes
    max_playback_attempts: int = 2  # Max attempts before escape hatch
    # Suppress duplicate end-call farewell injects when non-filler speech just ended.
    goodbye_playback_dedupe_window_seconds: float = 3.0
    preamble_speech_detection_enabled: bool = False  # Require preamble speech before tool calls
    thinker_concurrency_guard_enabled: bool = True  # Reject concurrent thinker invocations
    # Reject concurrent invocations of end_call / transfer_to_staff_voice /
    # emergency_service_transfer_basic (KNCK-39358). All three modify the same
    # Twilio call state, so a second in-flight invocation is always a bug.
    call_management_concurrency_guard_enabled: bool = True
    thinker_response_grace_seconds: float = 3.0  # Suppress filler for N seconds after thinker finishes (0 to disable)

    # Realtime logging settings
    realtime_language_classification_max_concurrency: int = 8

    # Redis
    redis_enabled: bool = False
    redis_host: str = "redis://0.0.0.0"
    redis_port: int = 6379

    # Kafka
    kafka_reporting_enabled: bool = False
    data_curation_schema_id: int | None = None
    data_curation_schema: dict = {}
    data_curation_schema_file: str = "data_curation_schema.json"
    kafka_reporting_data_bootstrap_servers: str | None = None
    kafka_reporting_data_topic: str | None = None
    kafka_reporting_data_api_key: str | None = None
    kafka_reporting_data_api_secret: str | None = None
    kafka_reporting_data_schema_registry_url: str | None = None
    kafka_reporting_data_schema_api_key: str | None = None
    kafka_reporting_data_schema_api_secret: str | None = None

    # task-activity-event producer (KNCK-39556, Morning Brief feed).
    # Reuses the `kafka_reporting_data_*` cluster creds (same Confluent Cloud
    # cluster as data-curation); only the topic name is separate.
    task_activity_event_publishing_enabled: bool = True
    task_activity_publish_timeout_seconds: float = 0.5
    kafka_task_activity_topic: str | None = None

    # Voice-only end-of-call FRUSTRATED_USER classifier. Off-switch + timeout
    # are independent of the publishing flag because the classifier is what
    # actually costs LLM tokens — even with publishing disabled we don't want
    # to keep paying for it during a rollback.
    frustration_classifier_enabled: bool = True
    frustration_classifier_timeout_seconds: float = 5.0

    # task-event producer — conversation lifecycle status (IN_PROGRESS / PENDING / COMPLETED).
    # Reuses the same `kafka_reporting_data_*` cluster creds as the sibling
    # task-activity-event producer; separate topic and schema subject.
    task_event_publishing_enabled: bool = False
    task_event_publish_timeout_seconds: float = 0.5
    kafka_task_event_topic: str | None = None

    # Kafka producer poll interval (seconds) - controls background thread polling frequency
    # Lower (0.1-0.5s): Higher CPU, faster callbacks (for high-throughput streaming)
    # Higher (1-5s): Lower CPU, slower callbacks (for low-frequency telemetry)
    # Default 1.0s provides 10x CPU reduction with negligible latency impact
    kafka_producer_poll_interval_seconds: float = 1.0

    # Heartbeats sent for client for streaming (milliseconds)
    streaming_heartbeat_interval: float = 4.0

    caching_enabled: bool = True

    filler_phrases: bool = False

    # Cache expiration periods
    expire_default: str = "10m"
    expire_sms: str = "6h"
    expire_chat: str = "10m"
    expire_voice: str = "10m"
    expire_email: str = "6h"

    # LDP cache TTLs — shared single source of truth (Lambda reads from Secrets Manager)
    ldp_cache_ttl: str = "2h"
    ldp_cache_early_ttl: str = "1h30m"

    # Calls are initiated from Knock to Twilio, but we can test directly from Twilio by
    # simulating the payload that Knock would ordinarily send.
    # The default simulated payload is examples.ASK_REQUEST_RESIDENT_VOICE_KNCK.
    # Override it by pointing TWILIO_TEST_PAYLOAD to a JSON file containing the full payload.
    twilio_test_payload: str | None = None

    # The default simulated payload for the chatbot is examples.ASK_REQUEST_RESIDENT_CHAT_LL.
    # Override it by pointing CHATBOT_TEST_PAYLOAD to a JSON file containing the full payload.
    chatbot_test_payload: str | None = None
    # Toggle which sample ask_request payload flavor to use when no explicit path is provided.
    # e.g., "alpha" -> example_ask_request_ll.alpha.json, "beta" -> example_ask_request_ll.beta.json
    example_payload_flavor: str | None = None

    # Flag to enable the NiceGUI chatbot UI
    chatbot_enabled: bool = True

    # --- Voice refactor (KNCK-39531) ---
    # When True, /media-stream/websocket uses the new voice package
    # instead of twilio_handler.py.  Default False for safe rollout.
    use_voice_refactor: bool = False

    # VOICE_*-prefixed settings for the new voice package.
    # These have their own defaults so the legacy settings can be cleanly
    # removed when twilio_handler.py is decommissioned.
    #
    # --- Tuning heuristics ---
    #
    # Filler timing chain (these interact):
    #   delay_mean must be > delay_std * 2 to avoid fillers firing < 1s after audio.
    #   grace_seconds should be < delay_mean so the grace window expires before
    #   the next filler is due.  If grace >= delay_mean, fillers after thinker
    #   responses are suppressed indefinitely.
    #
    # Escalation vs dead line:
    #   escalation_threshold < max_consecutive_fillers_without_user_audio, always.
    #   Escalation (nudge model to call thinker) fires first; dead line (hang up)
    #   fires later.  If threshold >= max_consecutive, escalation never fires
    #   before the call terminates.
    #
    # Pacer timing:
    #   tick_seconds * prebuffer_frames = startup latency before first audio plays.
    #   Current: 0.020 * 6 = 120ms.  Increasing prebuffer_frames smooths jitter
    #   but adds latency.  tick_seconds must match the audio frame duration
    #   (20ms for 160 bytes at 8kHz mu-law) — do not change independently.
    #
    # Playback detection:
    #   start_timeout + end_timeout bounds how long a tool waits for the model
    #   to speak before proceeding.  If the model is slow (e.g. long thinker
    #   response), increase end_timeout.  settle_delay adds a brief pause after
    #   speech completes before a tool proceeds — too short causes the tool to
    #   act while the last word is still playing.
    #
    # Filler wait vs cancel:
    #   filler_wait_timeout_seconds only matters when strategy is "hybrid" or
    #   "wait".  It caps how long the thinker waits for a filler to finish before
    #   force-canceling.  Must be < delay_mean to avoid the next filler firing
    #   while waiting.

    # Filler messages — on/off, timing, and escalation behavior
    voice_fillers_enabled: bool = True
    voice_filler_delay_mean_seconds: float = 8.0  # first filler after ~8s silence
    voice_filler_delay_std_seconds: float = 1.5  # tight variance for predictable 4.5-7.5s range
    voice_filler_handling_strategy: Literal["cancel", "wait", "hybrid"] = "hybrid"
    voice_filler_wait_timeout_seconds: float = 1.5  # max wait before forcing cancel (hybrid/wait)
    voice_filler_escalation_enabled: bool = True  # nudge responder to call thinker when stuck
    voice_filler_escalation_threshold: int = 2  # escalate after N fillers with no thinker running

    # Audio pacer — frame timing for outbound audio to Twilio
    voice_pacer_tick_seconds: float = 0.020  # 20ms per frame (160 bytes @ 8kHz mu-law)
    voice_pacer_prebuffer_frames: int = 6  # ~120ms prebuffer before first send

    # Session lifecycle
    voice_max_session_duration_seconds: float = 3600.0  # 1 hour max
    voice_max_consecutive_fillers_without_user_audio: int = 5  # dead line detection

    # Playback detection — timeouts for confirming agent speech started/stopped
    voice_playback_start_timeout_seconds: float = 1.0
    voice_playback_end_timeout_seconds: float = 10.0
    voice_playback_settle_delay_seconds: float = 0.2
    voice_max_playback_attempts: int = 2
    # Same as goodbye_playback_dedupe_window_seconds, but for the voice package.
    voice_goodbye_playback_dedupe_window_seconds: float = 3.0

    # Thinker behavior
    voice_preamble_speech_detection_enabled: bool = False
    voice_thinker_concurrency_guard_enabled: bool = True
    voice_thinker_response_grace_seconds: float = 3.0  # suppress filler for N seconds after thinker finishes

    # Input audio noise reduction (Twilio-side, before sending to OpenAI)
    voice_noise_reduction_enabled: bool = False

    # Max time to wait for the background full-agent init before giving up
    # and transferring the call to staff. Trips only when the greeting has
    # finished but the full agent is still initialising.
    greeting_agent_init_timeout_seconds: float = 30.0

    def cache_expiration(self, channel: str | None) -> str:
        if not channel:
            return self.expire_default

        return {
            "sms": self.expire_sms,
            "email": self.expire_email,
            "chat": self.expire_chat,
            "voice": self.expire_voice,
        }.get(channel, self.expire_default)

    @property
    def dev_host(self):
        return f"{settings.environment}-{self.app_name}-voice.knocktest.com"

    @property
    def prod_host(self):
        return f"{self.app_name}-voice.knockcrm.com"

    def is_kafka_reporting_configured(self) -> bool:
        if not self.kafka_reporting_enabled:
            return False

        return all(
            [
                self.data_curation_schema_id,
                self.data_curation_schema,
                self.kafka_reporting_data_bootstrap_servers,
                self.kafka_reporting_data_topic,
                self.kafka_reporting_data_api_key,
                self.kafka_reporting_data_api_secret,
                self.kafka_reporting_data_schema_registry_url,
                self.kafka_reporting_data_schema_api_key,
                self.kafka_reporting_data_schema_api_secret,
            ]
        )

    def model_post_init(self, ctx):
        # Ensure numeric clamp ceiling cannot drop below phone-number-safe floor
        if self.mcp_max_numeric_value < PHONE_NUMBER_MAX_FLOOR:
            logger.warning(
                "mcp_max_numeric_value (%s) below phone-safe floor (%s); raising to floor",
                self.mcp_max_numeric_value,
                PHONE_NUMBER_MAX_FLOOR,
            )
            self.mcp_max_numeric_value = PHONE_NUMBER_MAX_FLOOR

        # Validate guardrail configuration
        valid_input_guardrails = {
            "security",
            "pii",
            "prompt_injection",
            "prisma_airs",
        }
        valid_output_guardrails = {
            "security",
            "pii",
            "fair_housing",
            "competitor_blocking",
            "prisma_airs",
            "unauthorized_promises",
            "legal_advice",
        }

        # Validate input guardrails
        invalid_input = set(self.enabled_input_guardrails) - valid_input_guardrails
        if invalid_input:
            raise ValueError(
                f"Invalid input guardrail names: {invalid_input}. Valid options are: {valid_input_guardrails}"
            )

        # Validate output guardrails
        invalid_output = set(self.enabled_output_guardrails) - valid_output_guardrails
        if invalid_output:
            raise ValueError(
                f"Invalid output guardrail names: {invalid_output}. Valid options are: {valid_output_guardrails}"
            )

        # Load Kafka schema with error handling
        schema_path = os.path.join(os.path.dirname(__file__), "kafka", self.data_curation_schema_file)

        try:
            with open(schema_path, encoding="utf-8") as file:
                self.data_curation_schema = json.load(file)
        except (OSError, ValueError, FileNotFoundError) as e:
            raise ValueError(f"Could not read Kafka schema file {schema_path}: {e}")


_REASONING_MODEL_PREFIXES = ("gpt-5",)


def build_model_settings(
    *,
    model: str,
    effort: str | None,
    verbosity: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    service_tier: str | None = None,
    extra_args: dict[str, Any] | None = None,
    override_model: str | None = None,
) -> ModelSettings:
    """Create model settings, including reasoning details when supported."""

    settings_kwargs: dict[str, Any] = {}

    if effort and model.startswith(_REASONING_MODEL_PREFIXES):
        settings_kwargs["reasoning"] = {"effort": effort}

    if verbosity is not None:
        settings_kwargs["verbosity"] = verbosity

    if temperature is not None:
        settings_kwargs["temperature"] = temperature

    if max_tokens is not None:
        settings_kwargs["max_tokens"] = max_tokens

    merged_extra_args: dict[str, Any] = dict(extra_args or {})
    if service_tier:
        merged_extra_args.setdefault("service_tier", service_tier)
    if merged_extra_args:
        settings_kwargs["extra_args"] = merged_extra_args

    if override_model:
        settings_kwargs["model"] = override_model

    return ModelSettings(**settings_kwargs)


settings = Config()

# Fallback for Cursor Background Agents where OPENAI_API_KEY cannot
# be set directly. If OPENAI_API_KEY is not set but OAI_API_KEY is, copy it over.
if not os.getenv("OPENAI_API_KEY") and (oai_key := os.getenv("OAI_API_KEY")):
    os.environ["OPENAI_API_KEY"] = oai_key
