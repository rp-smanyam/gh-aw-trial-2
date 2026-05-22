"""Tests for FillerManager — scheduling, selection, delivery, dead-line detection."""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_leasing.voice.config import VoiceConfig
from agent_leasing.voice.coordination.call_state import VoiceCallState
from agent_leasing.voice.filler.manager import FillerManager


def _make_filler(
    *,
    fillers_enabled: bool = True,
    escalation_threshold: int = 2,
    max_consecutive: int = 5,
) -> tuple[FillerManager, VoiceCallState, AsyncMock]:
    config = VoiceConfig(
        fillers_enabled=fillers_enabled,
        filler_escalation_enabled=True,
        filler_escalation_threshold=escalation_threshold,
        max_consecutive_fillers_without_user_audio=max_consecutive,
    )
    call_state = VoiceCallState()
    session = AsyncMock()
    # is_response_active is a sync method on SessionManager; default it to
    # False so the filler delivery path isn't gated by it in tests that
    # don't explicitly target the active-response check.
    session.is_response_active = MagicMock(return_value=False)
    fm = FillerManager(config, session, call_state)
    return fm, call_state, session


class TestFillerScheduling:
    def test_schedule_sets_next_filler_time(self):
        fm, _, _ = _make_filler()
        fm.schedule()
        assert fm._next_filler_time is not None
        assert fm._next_filler_time > time.time()

    def test_schedule_disabled(self):
        fm, _, _ = _make_filler(fillers_enabled=False)
        fm.schedule()
        assert fm._next_filler_time is None

    def test_cancel_schedule_clears_timer(self):
        fm, _, _ = _make_filler()
        fm.schedule()
        assert fm._next_filler_time is not None
        fm.cancel_schedule()
        assert fm._next_filler_time is None


class TestFillerDelivery:
    @pytest.mark.asyncio
    async def test_send_if_due_sends_when_time_elapsed(self):
        fm, _, session = _make_filler()
        fm._next_filler_time = time.time() - 1  # Already past due
        sent = await fm.send_if_due(language_code="en")
        assert sent is True
        session.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_if_due_skips_when_not_due(self):
        fm, _, session = _make_filler()
        fm._next_filler_time = time.time() + 100  # Far in the future
        sent = await fm.send_if_due(language_code="en")
        assert sent is False
        session.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_if_due_skips_when_disabled(self):
        fm, _, session = _make_filler(fillers_enabled=False)
        fm._next_filler_time = time.time() - 1
        sent = await fm.send_if_due(language_code="en")
        assert sent is False

    @pytest.mark.asyncio
    async def test_send_if_due_skips_when_agent_speaking(self):
        fm, cs, session = _make_filler()
        cs.mark_agent_speaking_started()
        fm._next_filler_time = time.time() - 1
        sent = await fm.send_if_due(language_code="en")
        assert sent is False

    @pytest.mark.asyncio
    async def test_send_sets_next_speech_is_filler(self):
        fm, _, _ = _make_filler()
        fm._next_filler_time = time.time() - 1
        assert fm.next_speech_is_filler is False
        await fm.send_if_due(language_code="en")
        assert fm.next_speech_is_filler is True

    @pytest.mark.asyncio
    async def test_send_if_due_skips_when_response_active(self):
        """Filler must not fire while the SDK has an in-flight response —
        overlapping ``response.create`` calls trigger the
        ``"already has an active response in progress"`` RealtimeError that
        #1525 recovers from.  Skipping here prevents the trigger entirely.
        """
        fm, _, session = _make_filler()
        session.is_response_active.return_value = True
        prior_time = time.time() - 1
        fm._next_filler_time = prior_time

        sent = await fm.send_if_due(language_code="en")

        assert sent is False
        session.send_message.assert_not_called()
        # Reschedules so it tries again later instead of dropping the filler.
        assert fm._next_filler_time is not None
        assert fm._next_filler_time > prior_time

    @pytest.mark.asyncio
    async def test_send_if_due_skips_when_handoff_in_progress(self):
        """Generic filler must stay suppressed while a destructive handoff tool is running."""
        fm, _, session = _make_filler()
        fm._next_filler_time = time.time() - 1

        sent = await fm.send_if_due(language_code="en", destructive_handoff_in_progress=True)

        assert sent is False
        session.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_if_due_allows_transfer_summary_filler_during_handoff(self):
        """Transfer-summary flow is the only filler exception during destructive handoff."""
        fm, _, session = _make_filler()
        fm._next_filler_time = time.time() - 1

        sent = await fm.send_if_due(
            language_code="en",
            transfer_summary_flow_active=True,
            destructive_handoff_in_progress=True,
        )

        assert sent is True
        session.send_message.assert_called_once()


class TestFillerMessageSelection:
    @pytest.mark.asyncio
    async def test_thinker_active_message(self):
        fm, _, session = _make_filler()
        fm._next_filler_time = time.time() - 1
        await fm.send_if_due(language_code="es", thinker_running=True)
        msg = session.send_message.call_args[0][0]
        assert "still working" in msg.lower()

    @pytest.mark.asyncio
    async def test_idle_message(self):
        fm, _, session = _make_filler()
        fm._next_filler_time = time.time() - 1
        await fm.send_if_due(language_code="en", thinker_running=False)
        msg = session.send_message.call_args[0][0]
        assert "still here" in msg.lower()

    @pytest.mark.asyncio
    async def test_escalation_message(self):
        fm, _, session = _make_filler(escalation_threshold=1)
        # First filler — should be idle, counter goes to 1
        fm._next_filler_time = time.time() - 1
        await fm.send_if_due(language_code="en")
        # Second filler — counter=1 >= threshold=1, should escalate
        fm._next_filler_time = time.time() - 1
        await fm.send_if_due(language_code="en")
        msg = session.send_message.call_args[0][0]
        assert "CRITICAL" in msg

    @pytest.mark.asyncio
    async def test_handoff_message(self):
        fm, _, session = _make_filler()
        fm._next_filler_time = time.time() - 1
        await fm.send_if_due(language_code="en", transfer_summary_flow_active=True)
        msg = session.send_message.call_args[0][0]
        assert "transfer" in msg.lower()


class TestFillerDeadLine:
    def test_is_dead_line(self):
        fm, _, _ = _make_filler(max_consecutive=3)
        assert fm.is_dead_line() is False
        fm._consecutive_fillers_without_user_audio = 3
        assert fm.is_dead_line() is True

    @pytest.mark.asyncio
    async def test_consecutive_counter_increments_on_idle_filler(self):
        fm, _, _ = _make_filler()
        fm._next_filler_time = time.time() - 1
        await fm.send_if_due(language_code="en", thinker_running=False)
        assert fm._consecutive_fillers_without_user_audio == 1

    @pytest.mark.asyncio
    async def test_consecutive_counter_not_incremented_on_thinker_filler(self):
        fm, _, _ = _make_filler()
        fm._next_filler_time = time.time() - 1
        await fm.send_if_due(language_code="en", thinker_running=True)
        assert fm._consecutive_fillers_without_user_audio == 0


class TestFillerOnInterrupt:
    def test_on_interrupt_resets_counter(self):
        fm, _, _ = _make_filler()
        fm._consecutive_fillers_without_user_audio = 5
        fm.on_interrupt()
        assert fm._consecutive_fillers_without_user_audio == 0
        assert fm._next_filler_time is None
        assert fm.next_speech_is_filler is False


class TestFillerDeadLineSequences:
    """End-to-end counter sequences that drive is_dead_line()."""

    @pytest.mark.asyncio
    async def test_five_idle_fillers_trigger_dead_line(self):
        fm, _, _ = _make_filler(max_consecutive=5)
        for _ in range(5):
            fm._next_filler_time = time.time() - 1
            await fm.send_if_due(language_code="en", thinker_running=False)
        assert fm._consecutive_fillers_without_user_audio == 5
        assert fm.is_dead_line() is True

    @pytest.mark.asyncio
    async def test_four_idle_fillers_do_not_trigger_dead_line(self):
        fm, _, _ = _make_filler(max_consecutive=5)
        for _ in range(4):
            fm._next_filler_time = time.time() - 1
            await fm.send_if_due(language_code="en", thinker_running=False)
        assert fm._consecutive_fillers_without_user_audio == 4
        assert fm.is_dead_line() is False

    @pytest.mark.asyncio
    async def test_on_interrupt_between_fillers_resets_counter(self):
        fm, _, _ = _make_filler(max_consecutive=5)
        fm._next_filler_time = time.time() - 1
        await fm.send_if_due(language_code="en", thinker_running=False)
        assert fm._consecutive_fillers_without_user_audio == 1
        fm.on_interrupt()
        fm._next_filler_time = time.time() - 1
        await fm.send_if_due(language_code="en", thinker_running=False)
        assert fm._consecutive_fillers_without_user_audio == 1

    @pytest.mark.asyncio
    async def test_thinker_filler_does_not_reset_counter(self):
        """A thinker filler in the middle does not reset the idle-filler count."""
        fm, _, _ = _make_filler(max_consecutive=5)
        for _ in range(4):
            fm._next_filler_time = time.time() - 1
            await fm.send_if_due(language_code="en", thinker_running=False)
        assert fm._consecutive_fillers_without_user_audio == 4
        # A thinker filler — should not increment, should not reset
        fm._next_filler_time = time.time() - 1
        await fm.send_if_due(language_code="en", thinker_running=True)
        assert fm._consecutive_fillers_without_user_audio == 4
        # One more idle filler reaches dead-line
        fm._next_filler_time = time.time() - 1
        await fm.send_if_due(language_code="en", thinker_running=False)
        assert fm._consecutive_fillers_without_user_audio == 5
        assert fm.is_dead_line() is True


class TestFillerItemTracking:
    def test_mark_filler_item(self):
        fm, _, _ = _make_filler()
        fm.mark_filler_item("item_123")
        assert "item_123" in fm.filler_item_ids

    def test_reset_clears_filler_items(self):
        fm, _, _ = _make_filler()
        fm.mark_filler_item("item_123")
        fm.reset()
        assert len(fm.filler_item_ids) == 0
