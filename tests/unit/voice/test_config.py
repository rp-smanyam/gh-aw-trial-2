"""Tests for VoiceConfig — settings mapping."""

from agent_leasing.voice.config import VoiceConfig, voice_config_from_settings


class TestVoiceConfigDefaults:
    def test_default_values(self):
        cfg = VoiceConfig()
        assert cfg.fillers_enabled is True
        assert cfg.filler_delay_mean_seconds == 8.0
        assert cfg.filler_delay_std_seconds == 1.5
        assert cfg.voice == "marin"
        assert cfg.audio_format == "g711_ulaw"
        assert cfg.turn_detection_type == "semantic_vad"
        assert cfg.max_session_duration_seconds == 3600.0
        assert cfg.pacer_tick_seconds == 0.020
        assert cfg.pacer_prebuffer_frames == 6
        assert cfg.realtime_model == "gpt-realtime-2"
        assert cfg.reasoning_effort == "low"

    def test_frozen(self):
        cfg = VoiceConfig()
        try:
            cfg.voice = "other"
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestVoiceConfigFromSettings:
    def test_reads_voice_settings_directly(self):
        """Factory reads from VOICE_* settings, not legacy settings."""
        from agent_leasing.settings import settings

        cfg = voice_config_from_settings(settings)
        assert cfg.fillers_enabled == settings.voice_fillers_enabled
        assert cfg.filler_delay_mean_seconds == settings.voice_filler_delay_mean_seconds
        assert cfg.noise_reduction_enabled == settings.voice_noise_reduction_enabled
        assert cfg.max_session_duration_seconds == settings.voice_max_session_duration_seconds
        assert cfg.greeting_agent_enabled == settings.greeting_agent_enabled
        assert cfg.greeting_agent_init_timeout_seconds == settings.greeting_agent_init_timeout_seconds
        assert cfg.environment == settings.environment
        assert cfg.realtime_model == settings.realtime_model
        assert cfg.reasoning_effort == settings.realtime_reasoning_effort

    def test_env_var_override(self):
        """When a VOICE_* env var is set, it flows through to VoiceConfig."""
        from unittest.mock import MagicMock

        mock_cfg = MagicMock()
        mock_cfg.voice_fillers_enabled = False
        mock_cfg.voice_filler_delay_mean_seconds = 5.0
        mock_cfg.voice_filler_delay_std_seconds = 1.0
        mock_cfg.voice_filler_handling_strategy = "cancel"
        mock_cfg.voice_filler_wait_timeout_seconds = 2.0
        mock_cfg.voice_filler_escalation_enabled = False
        mock_cfg.voice_filler_escalation_threshold = 3
        mock_cfg.voice_pacer_tick_seconds = 0.040
        mock_cfg.voice_pacer_prebuffer_frames = 12
        mock_cfg.voice_max_session_duration_seconds = 1800.0
        mock_cfg.voice_max_consecutive_fillers_without_user_audio = 3
        mock_cfg.voice_playback_start_timeout_seconds = 2.0
        mock_cfg.voice_playback_end_timeout_seconds = 15.0
        mock_cfg.voice_playback_settle_delay_seconds = 0.5
        mock_cfg.voice_max_playback_attempts = 3
        mock_cfg.voice_preamble_speech_detection_enabled = True
        mock_cfg.voice_thinker_concurrency_guard_enabled = False
        mock_cfg.voice_thinker_response_grace_seconds = 5.0
        mock_cfg.voice_noise_reduction_enabled = True
        mock_cfg.greeting_agent_enabled = True
        mock_cfg.greeting_agent_init_timeout_seconds = 15.0
        mock_cfg.realtime_model = "gpt-realtime-2"
        mock_cfg.realtime_reasoning_effort = "low"
        mock_cfg.openai_audio_format = "g711_ulaw"
        mock_cfg.transcription_model = "gpt-realtime-whisper"
        mock_cfg.realtime_voice = "marin"
        mock_cfg.realtime_voice_speed = 1.1
        mock_cfg.realtime_turn_detection_type = "semantic_vad"
        mock_cfg.realtime_turn_detection_eagerness = "auto"
        mock_cfg.realtime_turn_detection_interrupt_response = True
        mock_cfg.realtime_turn_detection_create_response = True
        mock_cfg.realtime_input_audio_noise_reduction = "near_field"
        mock_cfg.environment = "test"
        mock_cfg.openai_base_wss_url = ""
        mock_cfg.knock_internal_api_url = "https://test.com"

        cfg = voice_config_from_settings(mock_cfg)
        assert cfg.fillers_enabled is False
        assert cfg.filler_delay_mean_seconds == 5.0
        assert cfg.filler_handling_strategy == "cancel"
        assert cfg.pacer_tick_seconds == 0.040
        assert cfg.noise_reduction_enabled is True
        assert cfg.max_session_duration_seconds == 1800.0
        assert cfg.greeting_agent_enabled is True
        assert cfg.greeting_agent_init_timeout_seconds == 15.0
