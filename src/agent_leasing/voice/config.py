"""VoiceConfig — typed configuration for the voice package.

Reads ``VOICE_*`` settings from the global ``Config`` and exposes them as
a frozen dataclass. Components in the voice package depend on ``VoiceConfig``
instead of reaching into ``settings`` directly, making them independently
testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from agent_leasing.settings import Config


@dataclass(frozen=True, slots=True)
class VoiceConfig:
    """Immutable snapshot of voice-related settings for one call.

    This is **not** a settings source.  Environment variables are read by
    pydantic in ``settings.py`` (the ``VOICE_*`` fields on the ``Config``
    class).  The factory function ``voice_config_from_settings(settings)``
    copies those values into this frozen dataclass, which components then
    use for the lifetime of the call.

    The flow is::

        ENV / .env  →  pydantic Config (settings.py)
                            →  voice_config_from_settings()
                                    →  VoiceConfig (this dataclass)

    Defaults here are fallbacks for tests that construct ``VoiceConfig()``
    directly without going through the factory.
    """

    # --- Feature flag (read from settings at handler creation) ---
    fillers_enabled: bool = True

    # --- Filler timing ---
    filler_delay_mean_seconds: float = 8.0
    filler_delay_std_seconds: float = 1.5
    filler_handling_strategy: Literal["cancel", "wait", "hybrid"] = "hybrid"
    filler_wait_timeout_seconds: float = 1.5
    filler_escalation_enabled: bool = True
    filler_escalation_threshold: int = 2

    # --- Audio pacer (must match twilio_handler.py tuned values) ---
    pacer_tick_seconds: float = 0.020  # 20 ms per frame (160 bytes @ 8kHz mu-law)
    pacer_prebuffer_frames: int = 6  # ~120ms prebuffer
    pacer_startup_timeout_seconds: float = 0.120  # 120ms startup timeout
    pacer_underrun_grace_seconds: float = 0.003  # 3ms underrun grace

    # --- Audio buffer ---
    buffer_chunk_seconds: float = 0.05  # 50 ms
    buffer_sample_rate: int = 8000  # Twilio g711_ulaw

    # --- Session ---
    realtime_model: str = "gpt-realtime-2"
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] = "low"
    audio_format: Literal["g711_ulaw"] = "g711_ulaw"
    transcription_model: str = "gpt-realtime-whisper"
    voice: str = "marin"
    voice_speed: float = 1.1
    turn_detection_type: str = "semantic_vad"
    turn_detection_eagerness: str = "auto"
    turn_detection_interrupt_response: bool = True
    turn_detection_create_response: bool = True
    input_audio_noise_reduction: Literal["near_field", "far_field"] = "near_field"

    # --- Noise reduction (Twilio-side) ---
    noise_reduction_enabled: bool = False

    # --- Session lifecycle ---
    max_session_duration_seconds: float = 3600.0
    max_consecutive_fillers_without_user_audio: int = 5

    # --- Playback detection ---
    playback_start_timeout_seconds: float = 1.0
    playback_end_timeout_seconds: float = 10.0
    playback_settle_delay_seconds: float = 0.2
    max_playback_attempts: int = 2

    # --- Thinker ---
    preamble_speech_detection_enabled: bool = False
    thinker_concurrency_guard_enabled: bool = True
    thinker_response_grace_seconds: float = 3.0

    # --- Greeting agent (parallel startup) ---
    greeting_agent_enabled: bool = False
    greeting_agent_init_timeout_seconds: float = 30.0

    # --- Environment (for metadata / tracing) ---
    environment: str = "dev"

    # --- Session connection (non-secret URLs; credentials read from settings directly) ---
    openai_base_wss_url: str = ""
    knock_internal_api_url: str = "https://alpha-api.knocktest.com"


def voice_config_from_settings(cfg: Config) -> VoiceConfig:
    """Build a ``VoiceConfig`` from the global ``Config`` singleton.

    Reads directly from the ``VOICE_*`` settings, which have their own
    defaults.  No fallback to legacy settings — the voice package is
    self-contained.
    """
    return VoiceConfig(
        fillers_enabled=cfg.voice_fillers_enabled,
        filler_delay_mean_seconds=cfg.voice_filler_delay_mean_seconds,
        filler_delay_std_seconds=cfg.voice_filler_delay_std_seconds,
        filler_handling_strategy=cfg.voice_filler_handling_strategy,
        filler_wait_timeout_seconds=cfg.voice_filler_wait_timeout_seconds,
        filler_escalation_enabled=cfg.voice_filler_escalation_enabled,
        filler_escalation_threshold=cfg.voice_filler_escalation_threshold,
        pacer_tick_seconds=cfg.voice_pacer_tick_seconds,
        pacer_prebuffer_frames=cfg.voice_pacer_prebuffer_frames,
        realtime_model=cfg.realtime_model,
        reasoning_effort=cfg.realtime_reasoning_effort,
        audio_format=cfg.openai_audio_format,
        transcription_model=cfg.transcription_model,
        voice=cfg.realtime_voice,
        voice_speed=cfg.realtime_voice_speed,
        turn_detection_type=cfg.realtime_turn_detection_type,
        turn_detection_eagerness=cfg.realtime_turn_detection_eagerness,
        turn_detection_interrupt_response=cfg.realtime_turn_detection_interrupt_response,
        turn_detection_create_response=cfg.realtime_turn_detection_create_response,
        input_audio_noise_reduction=cfg.realtime_input_audio_noise_reduction,
        noise_reduction_enabled=cfg.voice_noise_reduction_enabled,
        max_session_duration_seconds=cfg.voice_max_session_duration_seconds,
        max_consecutive_fillers_without_user_audio=cfg.voice_max_consecutive_fillers_without_user_audio,
        playback_start_timeout_seconds=cfg.voice_playback_start_timeout_seconds,
        playback_end_timeout_seconds=cfg.voice_playback_end_timeout_seconds,
        playback_settle_delay_seconds=cfg.voice_playback_settle_delay_seconds,
        max_playback_attempts=cfg.voice_max_playback_attempts,
        preamble_speech_detection_enabled=cfg.voice_preamble_speech_detection_enabled,
        thinker_concurrency_guard_enabled=cfg.voice_thinker_concurrency_guard_enabled,
        thinker_response_grace_seconds=cfg.voice_thinker_response_grace_seconds,
        greeting_agent_enabled=cfg.greeting_agent_enabled,
        greeting_agent_init_timeout_seconds=cfg.greeting_agent_init_timeout_seconds,
        environment=cfg.environment,
        openai_base_wss_url=cfg.openai_base_wss_url,
        knock_internal_api_url=cfg.knock_internal_api_url,
    )
