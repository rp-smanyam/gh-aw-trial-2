"""Tests for VoiceCallState — async Event-based state machine."""

import asyncio
import datetime
from unittest.mock import AsyncMock

import pytest

from agent_leasing.settings import settings
from agent_leasing.voice.coordination.call_state import VoiceCallState


class TestVoiceCallStateBasic:
    def test_initial_state(self):
        cs = VoiceCallState()
        assert cs.is_agent_speaking is False
        assert cs.is_agent_processing is False
        assert cs.is_user_speaking is False
        assert cs.is_filler_playing is False
        assert cs.last_user_speaking_started_at is None
        assert cs.last_user_speaking_stopped_at is None

    def test_can_send_filler_initial(self):
        cs = VoiceCallState()
        assert cs.can_send_filler() is True

    def test_cannot_send_filler_when_agent_speaking(self):
        cs = VoiceCallState()
        cs.mark_agent_speaking_started()
        assert cs.can_send_filler() is False

    def test_cannot_send_filler_when_user_speaking(self):
        cs = VoiceCallState()
        cs.mark_user_speaking_started()
        assert cs.can_send_filler() is False


class TestVoiceCallStateTransitions:
    def test_agent_speaking_clears_processing(self):
        cs = VoiceCallState()
        cs.mark_agent_processing_started()
        assert cs.is_agent_processing is True
        cs.mark_agent_speaking_started()
        assert cs.is_agent_processing is False

    def test_agent_speaking_non_filler_clears_user_speaking(self):
        cs = VoiceCallState()
        cs.mark_user_speaking_started()
        cs.mark_agent_speaking_started(is_filler=False)
        assert cs.is_user_speaking is False

    def test_agent_speaking_filler_preserves_user_speaking(self):
        cs = VoiceCallState()
        cs.mark_user_speaking_started()
        cs.mark_agent_speaking_started(is_filler=True)
        assert cs.is_user_speaking is True
        assert cs.is_filler_playing is True

    def test_on_interrupt_clears_speaking(self):
        cs = VoiceCallState()
        cs.mark_agent_speaking_started()
        cs.on_interrupt()
        assert cs.is_agent_speaking is False
        assert cs.is_filler_playing is False


class TestVoiceCallStateTimestamps:
    def test_user_speaking_started_records_timestamp(self):
        cs = VoiceCallState()
        cs.mark_user_speaking_started()
        assert cs.last_user_speaking_started_at is not None
        assert isinstance(cs.last_user_speaking_started_at, datetime.datetime)

    def test_user_speaking_stopped_records_timestamp(self):
        cs = VoiceCallState()
        cs.mark_user_speaking_stopped()
        assert cs.last_user_speaking_stopped_at is not None

    def test_consume_timestamps_returns_and_clears(self):
        cs = VoiceCallState()
        cs.mark_user_speaking_started()
        cs.mark_user_speaking_stopped()
        started, stopped = cs.consume_user_speaking_timestamps()
        assert started is not None
        assert stopped is not None
        assert cs.last_user_speaking_started_at is None
        assert cs.last_user_speaking_stopped_at is None

    def test_consume_timestamps_when_empty(self):
        cs = VoiceCallState()
        started, stopped = cs.consume_user_speaking_timestamps()
        assert started is None
        assert stopped is None


class TestVoiceCallStateWaiters:
    @pytest.mark.asyncio
    async def test_wait_for_speaking_started_immediate(self):
        cs = VoiceCallState()
        cs.mark_agent_speaking_started()
        result = await cs.wait_for_agent_speaking_started(timeout=0.1)
        assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_speaking_started_timeout(self):
        cs = VoiceCallState()
        result = await cs.wait_for_agent_speaking_started(timeout=0.05)
        assert result is False

    @pytest.mark.asyncio
    async def test_wait_for_speaking_stopped_immediate(self):
        cs = VoiceCallState()
        result = await cs.wait_for_agent_speaking_stopped(timeout=0.1)
        assert result is True  # Not speaking, so already stopped

    @pytest.mark.asyncio
    async def test_wait_for_filler_stopped_immediate(self):
        cs = VoiceCallState()
        result = await cs.wait_for_filler_stopped(timeout=0.1)
        assert result is True  # No filler playing

    @pytest.mark.asyncio
    async def test_wait_for_speaking_started_event_driven(self):
        cs = VoiceCallState()

        async def start_speaking():
            await asyncio.sleep(0.05)
            cs.mark_agent_speaking_started()

        asyncio.create_task(start_speaking())
        result = await cs.wait_for_agent_speaking_started(timeout=1.0)
        assert result is True


class TestVoiceCallStateReset:
    def test_reset_clears_all(self):
        cs = VoiceCallState()
        cs.mark_agent_speaking_started(is_filler=True)
        cs.mark_user_speaking_started()
        cs.mark_agent_processing_started()
        cs.reset()
        assert cs.is_agent_speaking is False
        assert cs.is_agent_processing is False
        assert cs.is_user_speaking is False
        assert cs.is_filler_playing is False
        assert cs.last_user_speaking_started_at is None


class TestVoiceCallStatePlaybackDedup:
    @pytest.mark.asyncio
    async def test_goodbye_recent_playback_skips_inject(self):
        cs = VoiceCallState()
        send_fn = AsyncMock()
        cs._send_message_fn = send_fn

        cs.mark_agent_speaking_started(is_filler=False)
        cs.mark_agent_speaking_stopped()

        result = await cs.wait_for_message_playback(
            "goodbye",
            tool_name="end_call",
            start_timeout_seconds=0.05,
            end_timeout_seconds=0.05,
        )

        assert result.success is True
        assert result.started is True
        assert result.completed is True
        send_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_goodbye_without_recent_playback_still_injects(self):
        cs = VoiceCallState()
        send_fn = AsyncMock()
        cs._send_message_fn = send_fn

        result = await cs.wait_for_message_playback(
            "goodbye",
            tool_name="end_call",
            start_timeout_seconds=0.05,
            end_timeout_seconds=0.05,
        )

        assert result.success is True
        assert result.started is False
        assert result.completed is False
        assert send_fn.await_count == 2

    @pytest.mark.asyncio
    async def test_goodbye_dedupe_window_is_configurable(self, monkeypatch):
        cs = VoiceCallState()
        send_fn = AsyncMock()
        cs._send_message_fn = send_fn

        cs.mark_agent_speaking_started(is_filler=False)
        cs.mark_agent_speaking_stopped()
        monkeypatch.setattr(settings, "voice_goodbye_playback_dedupe_window_seconds", 0.0)

        result = await cs.wait_for_message_playback(
            "goodbye",
            tool_name="end_call",
            start_timeout_seconds=0.05,
            end_timeout_seconds=0.05,
        )

        assert result.success is True
        assert result.started is False
        assert result.completed is False
        assert send_fn.await_count == 2

    @pytest.mark.asyncio
    async def test_duplicate_stop_does_not_refresh_recent_playback_timestamp(self, monkeypatch):
        cs = VoiceCallState()
        send_fn = AsyncMock()
        cs._send_message_fn = send_fn
        monkeypatch.setattr(settings, "voice_goodbye_playback_dedupe_window_seconds", 0.05)

        cs.mark_agent_speaking_started(is_filler=False)
        cs.mark_agent_speaking_stopped()
        await asyncio.sleep(0.08)
        # Simulate redundant completion callback while already stopped.
        cs.mark_agent_speaking_stopped()

        result = await cs.wait_for_message_playback(
            "goodbye",
            tool_name="end_call",
            start_timeout_seconds=0.05,
            end_timeout_seconds=0.05,
        )

        assert result.success is True
        assert result.started is False
        assert result.completed is False
        assert send_fn.await_count == 2
