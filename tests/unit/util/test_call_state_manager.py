"""Tests for CallStateManager."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_leasing.settings import settings
from agent_leasing.util.call_state_manager import (
    PLAYBACK_INJECT_MESSAGE,
    CallStateManager,
    get_call_state_from_context,
)


class TestCallStateManagerBasicState:
    """Test basic state management methods."""

    def test_initial_state(self):
        """Test that CallStateManager initializes with correct default state."""
        manager = CallStateManager()

        assert manager.is_agent_speaking is False
        assert manager.is_agent_processing is False
        assert manager.is_user_speaking is False
        assert manager._processing_started_at is None

    def test_custom_processing_timeout(self):
        """Test CallStateManager with custom processing timeout."""
        manager = CallStateManager(processing_timeout_seconds=60.0)
        assert manager._processing_timeout == 60.0

    def test_mark_user_speaking_started(self):
        """Test marking user as speaking."""
        manager = CallStateManager()
        manager.mark_user_speaking_started()
        assert manager.is_user_speaking is True

    def test_mark_user_speaking_stopped(self):
        """Test marking user as stopped speaking."""
        manager = CallStateManager()
        manager.mark_user_speaking_started()
        manager.mark_user_speaking_stopped()
        assert manager.is_user_speaking is False

    def test_mark_agent_processing_started(self):
        """Test marking agent as processing."""
        manager = CallStateManager()
        manager.mark_agent_processing_started()

        assert manager.is_agent_processing is True
        assert manager._processing_started_at is not None

    def test_mark_agent_speaking_started(self):
        """Test marking agent as speaking clears processing state."""
        manager = CallStateManager()
        manager.mark_agent_processing_started()
        manager.mark_agent_speaking_started()

        assert manager.is_agent_speaking is True
        assert manager.is_agent_processing is False
        assert manager._processing_started_at is None

    def test_mark_agent_speaking_stopped(self):
        """Test marking agent as stopped speaking."""
        manager = CallStateManager()
        manager.mark_agent_speaking_started()
        manager.mark_agent_speaking_stopped()

        assert manager.is_agent_speaking is False

    def test_reset(self):
        """Test reset clears all state."""
        manager = CallStateManager()
        manager.mark_user_speaking_started()
        manager.mark_agent_speaking_started()
        manager.mark_agent_processing_started()

        manager.reset()

        assert manager.is_agent_speaking is False
        assert manager.is_agent_processing is False
        assert manager.is_user_speaking is False
        assert manager._processing_started_at is None


class TestCallStateManagerProcessingTimeout:
    """Test processing timeout functionality."""

    def test_is_processing_timed_out_not_processing(self):
        """Test timeout check when not processing."""
        manager = CallStateManager()
        assert manager.is_processing_timed_out() is False

    def test_is_processing_timed_out_within_timeout(self):
        """Test timeout check within timeout period."""
        manager = CallStateManager(processing_timeout_seconds=30.0)
        manager.mark_agent_processing_started()

        assert manager.is_processing_timed_out() is False

    def test_is_processing_timed_out_exceeded(self):
        """Test timeout check when exceeded."""
        manager = CallStateManager(processing_timeout_seconds=0.0)
        manager.mark_agent_processing_started()

        # With 0 second timeout, should immediately be timed out
        assert manager.is_processing_timed_out() is True


class TestCallStateManagerCanSendFiller:
    """Test can_send_filler logic."""

    def test_can_send_filler_idle_state(self):
        """Test filler allowed when idle."""
        manager = CallStateManager()
        assert manager.can_send_filler() is True

    def test_can_send_filler_agent_speaking(self):
        """Test filler not allowed when agent speaking."""
        manager = CallStateManager()
        manager.mark_agent_speaking_started()
        assert manager.can_send_filler() is False

    def test_can_send_filler_user_speaking(self):
        """Test filler not allowed when user speaking."""
        manager = CallStateManager()
        manager.mark_user_speaking_started()
        assert manager.can_send_filler() is False

    def test_can_send_filler_agent_processing(self):
        """Test filler is allowed when agent is processing."""
        manager = CallStateManager(processing_timeout_seconds=30.0)
        manager.mark_agent_processing_started()
        assert manager.can_send_filler() is True

    def test_can_send_filler_agent_processing_timed_out(self):
        """Test filler allowed when agent processing exceeded timeout."""
        manager = CallStateManager(processing_timeout_seconds=0.0)
        manager.mark_agent_processing_started()
        assert manager.can_send_filler() is True


class TestCallStateManagerAsyncWaits:
    """Test async waiting methods."""

    @pytest.mark.asyncio
    async def test_wait_for_agent_speaking_started_already_speaking(self):
        """Test wait returns immediately if already speaking."""
        manager = CallStateManager()
        manager.mark_agent_speaking_started()

        result = await manager.wait_for_agent_speaking_started(timeout_seconds=1.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_agent_speaking_started_timeout(self):
        """Test wait times out if agent doesn't start speaking."""
        manager = CallStateManager()

        start = time.monotonic()
        result = await manager.wait_for_agent_speaking_started(timeout_seconds=0.1)
        elapsed = time.monotonic() - start

        assert result is False
        assert elapsed >= 0.1
        assert elapsed < 0.3  # Some buffer for test execution

    @pytest.mark.asyncio
    async def test_wait_for_agent_speaking_started_becomes_true(self):
        """Test wait succeeds when agent starts speaking during wait."""
        manager = CallStateManager()

        async def start_speaking_later():
            await asyncio.sleep(0.05)
            manager.mark_agent_speaking_started()

        task = asyncio.create_task(start_speaking_later())
        result = await manager.wait_for_agent_speaking_started(timeout_seconds=1.0)
        await task

        assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_agent_speaking_stopped_already_stopped(self):
        """Test wait returns immediately if not speaking."""
        manager = CallStateManager()

        result = await manager.wait_for_agent_speaking_stopped(timeout_seconds=1.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_agent_speaking_stopped_timeout(self):
        """Test wait times out if agent doesn't stop speaking."""
        manager = CallStateManager()
        manager.mark_agent_speaking_started()

        start = time.monotonic()
        result = await manager.wait_for_agent_speaking_stopped(timeout_seconds=0.1)
        elapsed = time.monotonic() - start

        assert result is False
        assert elapsed >= 0.1

    @pytest.mark.asyncio
    async def test_wait_for_agent_speaking_stopped_becomes_true(self):
        """Test wait succeeds when agent stops speaking during wait."""
        manager = CallStateManager()
        manager.mark_agent_speaking_started()

        async def stop_speaking_later():
            await asyncio.sleep(0.05)
            manager.mark_agent_speaking_stopped()

        task = asyncio.create_task(stop_speaking_later())
        result = await manager.wait_for_agent_speaking_stopped(timeout_seconds=1.0)
        await task

        assert result is True


class TestCallStateManagerPlaybackWait:
    """Test wait_for_agent_playback and wait_for_message_playback methods."""

    @pytest.mark.asyncio
    async def test_wait_for_agent_playback_success(self):
        """Test wait_for_agent_playback when speech completes."""
        manager = CallStateManager()

        async def simulate_playback():
            # Allow the wait to start polling first
            await asyncio.sleep(0.01)
            manager.mark_agent_speaking_started()
            await asyncio.sleep(0.05)
            manager.mark_agent_speaking_stopped()

        async def do_wait():
            return await manager.wait_for_agent_playback(
                start_timeout_seconds=1.0,
                end_timeout_seconds=1.0,
                settle_delay_seconds=0.01,
                poll_interval_seconds=0.01,
            )

        # Run both concurrently
        results = await asyncio.gather(do_wait(), simulate_playback())
        started, completed = results[0]

        assert started is True
        assert completed is True

    @pytest.mark.asyncio
    async def test_wait_for_agent_playback_never_starts(self):
        """Test wait_for_agent_playback when speech never starts."""
        manager = CallStateManager()

        started, completed = await manager.wait_for_agent_playback(
            start_timeout_seconds=0.1,
            end_timeout_seconds=0.1,
        )

        assert started is False
        assert completed is False

    @pytest.mark.asyncio
    async def test_wait_for_message_playback_success(self):
        """Test wait_for_message_playback with successful playback."""
        manager = CallStateManager()

        async def simulate_playback():
            await asyncio.sleep(0.01)
            manager.mark_agent_speaking_started()
            await asyncio.sleep(0.05)
            manager.mark_agent_speaking_stopped()

        async def wait_for_playback():
            return await manager.wait_for_message_playback(
                "test",
                start_timeout_seconds=1.0,
                end_timeout_seconds=1.0,
                settle_delay_seconds=0.01,
            )

        # Run both concurrently
        results = await asyncio.gather(wait_for_playback(), simulate_playback())
        result = results[0]

        assert result.success is True
        assert result.started is True
        assert result.completed is True

    @pytest.mark.asyncio
    async def test_wait_for_message_playback_uses_defaults(self):
        """Test that wait_for_message_playback uses default timeouts."""
        manager = CallStateManager()
        # Pre-set the speaking state so wait returns immediately
        manager.mark_agent_speaking_started()

        result = await manager.wait_for_message_playback("transfer")

        assert result.success is True
        assert result.started is True


class TestCallStateManagerInjectPlayback:
    """Test wait_for_message_playback with send_message_fn (direct inject path)."""

    @pytest.mark.asyncio
    async def test_inject_triggers_speech(self):
        """send_message_fn set, speech starts after inject → success."""
        manager = CallStateManager()
        inject_calls: list[str] = []

        async def mock_send_message(msg: str):
            inject_calls.append(msg)

            # Schedule speech to start AFTER send_message returns,
            # so wait_for_agent_speaking_started can detect it
            async def _speak():
                await asyncio.sleep(0.01)
                manager.mark_agent_speaking_started()
                await asyncio.sleep(0.05)
                manager.mark_agent_speaking_stopped()

            asyncio.create_task(_speak())

        manager._send_message_fn = mock_send_message

        result = await manager.wait_for_message_playback(
            "transfer",
            tool_name="transfer_to_staff_voice",
            start_timeout_seconds=1.0,
            end_timeout_seconds=1.0,
            settle_delay_seconds=0.01,
        )

        assert result.success is True
        assert result.started is True
        assert result.completed is True
        assert len(inject_calls) == 1
        assert inject_calls[0] == PLAYBACK_INJECT_MESSAGE.format(message_type="transfer")

    @pytest.mark.asyncio
    async def test_inject_escape_hatch_after_max_attempts(self):
        """send_message_fn set, speech never starts → escape hatch after max attempts."""
        send_fn = AsyncMock()
        manager = CallStateManager()
        manager._send_message_fn = send_fn

        result = await manager.wait_for_message_playback(
            "goodbye",
            tool_name="end_call",
            start_timeout_seconds=0.05,
            end_timeout_seconds=0.05,
        )

        assert result.success is True
        assert result.started is False
        assert result.completed is False
        assert send_fn.call_count == 2

    @pytest.mark.asyncio
    async def test_inject_second_attempt_triggers_speech(self):
        """First inject doesn't trigger speech, second does."""
        manager = CallStateManager()
        inject_count = 0

        async def mock_send_message(msg: str):
            nonlocal inject_count
            inject_count += 1
            if inject_count >= 2:

                async def _speak():
                    await asyncio.sleep(0.01)
                    manager.mark_agent_speaking_started()
                    await asyncio.sleep(0.05)
                    manager.mark_agent_speaking_stopped()

                asyncio.create_task(_speak())

        manager._send_message_fn = mock_send_message

        result = await manager.wait_for_message_playback(
            "transfer",
            start_timeout_seconds=0.15,
            end_timeout_seconds=1.0,
            settle_delay_seconds=0.01,
        )

        assert result.success is True
        assert result.started is True
        assert result.completed is True
        assert inject_count == 2

    @pytest.mark.asyncio
    async def test_goodbye_recent_playback_skips_inject(self):
        """Recent non-filler speech should suppress redundant goodbye injects."""
        manager = CallStateManager()
        send_fn = AsyncMock()
        manager._send_message_fn = send_fn

        manager.mark_agent_speaking_started(is_filler=False)
        manager.mark_agent_speaking_stopped()

        result = await manager.wait_for_message_playback(
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
    async def test_non_goodbye_recent_playback_still_injects(self):
        """Only goodbye uses the recent-playback dedupe path."""
        manager = CallStateManager()
        send_fn = AsyncMock()
        manager._send_message_fn = send_fn

        manager.mark_agent_speaking_started(is_filler=False)
        manager.mark_agent_speaking_stopped()

        result = await manager.wait_for_message_playback(
            "transfer",
            tool_name="transfer_to_staff_voice",
            start_timeout_seconds=0.05,
            end_timeout_seconds=0.05,
        )

        assert result.success is True
        assert result.started is False
        assert result.completed is False
        assert send_fn.await_count == 2

    @pytest.mark.asyncio
    async def test_goodbye_dedupe_window_is_configurable(self, monkeypatch):
        """A tiny configured window should disable dedupe for delayed goodbye checks."""
        manager = CallStateManager()
        send_fn = AsyncMock()
        manager._send_message_fn = send_fn

        manager.mark_agent_speaking_started(is_filler=False)
        manager.mark_agent_speaking_stopped()
        monkeypatch.setattr(settings, "goodbye_playback_dedupe_window_seconds", 0.0)

        result = await manager.wait_for_message_playback(
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
        """Redundant stop callbacks should not create/refresh non-filler playback recency."""
        manager = CallStateManager()
        send_fn = AsyncMock()
        manager._send_message_fn = send_fn
        monkeypatch.setattr(settings, "goodbye_playback_dedupe_window_seconds", 0.05)

        manager.mark_agent_speaking_started(is_filler=False)
        manager.mark_agent_speaking_stopped()
        await asyncio.sleep(0.08)
        # Simulate redundant completion callback while already stopped.
        manager.mark_agent_speaking_stopped()

        result = await manager.wait_for_message_playback(
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
    async def test_inject_resets_attempt_counter(self):
        """Successful inject resets the attempt counter for next call."""
        manager = CallStateManager()

        async def mock_send_message(msg: str):
            async def _speak():
                await asyncio.sleep(0.01)
                manager.mark_agent_speaking_started()
                await asyncio.sleep(0.05)
                manager.mark_agent_speaking_stopped()

            asyncio.create_task(_speak())

        manager._send_message_fn = mock_send_message

        result = await manager.wait_for_message_playback(
            "goodbye",
            start_timeout_seconds=1.0,
            end_timeout_seconds=1.0,
            settle_delay_seconds=0.01,
        )

        assert result.success is True
        assert manager._playback_attempts.get("goodbye", 0) == 0

    @pytest.mark.asyncio
    async def test_inject_no_callback_proceeds_gracefully(self):
        """No send_message_fn set, speech never starts → proceeds without crashing."""
        manager = CallStateManager()
        # _send_message_fn is None by default

        result = await manager.wait_for_message_playback(
            "goodbye",
            tool_name="end_call",
            start_timeout_seconds=0.05,
            end_timeout_seconds=0.05,
        )

        assert result.success is True
        assert result.started is False
        assert result.completed is False

    @pytest.mark.asyncio
    async def test_inject_ignores_filler_audio(self):
        """Filler audio during inject doesn't count as the real message."""
        manager = CallStateManager()
        inject_count = 0

        async def mock_send_message(msg: str):
            nonlocal inject_count
            inject_count += 1

            if inject_count == 1:
                # First inject triggers filler, not real speech
                async def _filler():
                    await asyncio.sleep(0.01)
                    manager.mark_agent_speaking_started(is_filler=True)
                    await asyncio.sleep(0.03)
                    manager.mark_agent_speaking_stopped()

                asyncio.create_task(_filler())
            else:
                # Second inject triggers real speech
                async def _speak():
                    await asyncio.sleep(0.01)
                    manager.mark_agent_speaking_started(is_filler=False)
                    await asyncio.sleep(0.05)
                    manager.mark_agent_speaking_stopped()

                asyncio.create_task(_speak())

        manager._send_message_fn = mock_send_message

        result = await manager.wait_for_message_playback(
            "transfer",
            start_timeout_seconds=0.5,
            end_timeout_seconds=1.0,
            settle_delay_seconds=0.01,
        )

        assert result.success is True
        assert result.started is True
        assert result.completed is True
        # Filler was skipped, real speech detected on second inject
        assert inject_count == 2


class TestGetCallStateFromContext:
    """Test get_call_state_from_context helper."""

    def test_get_call_state_from_context_present(self):
        """Test getting call state when present."""
        manager = CallStateManager()
        mock_ctx = MagicMock()
        mock_ctx.context.call_state_manager = manager

        result = get_call_state_from_context(mock_ctx)
        assert result is manager

    def test_get_call_state_from_context_missing(self):
        """Test getting call state when not present."""
        mock_ctx = MagicMock(spec=["context"])
        mock_ctx.context = MagicMock(spec=[])  # No call_state_manager attribute

        result = get_call_state_from_context(mock_ctx)
        assert result is None
