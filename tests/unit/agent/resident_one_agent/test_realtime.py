"""Tests for resident_one_agent realtime module."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agents import RunContextWrapper
from agents.tool import ToolContext
from mcp.types import CallToolResult, TextContent
from openai.types.responses import ResponseFunctionToolCall

from agent_leasing.agent.resident_one_agent.realtime import (
    THINKER_TOOL_NAME,
    _cancel_active_filler,
    _handle_filler_before_thinker_response,
    _wait_for_filler_completion,
    build_parallel_greeting_agent,
    create_thinker_tool,
)
from agent_leasing.models.context import SessionScope


def create_mock_handler(
    is_agent_speaking: bool = False,
    has_session: bool = True,
    has_model: bool = True,
    has_schedule_next_filler: bool = True,
    next_speech_is_filler: bool = False,
):
    """Create a mock handler with configurable attributes."""
    handler = MagicMock()
    handler.is_agent_speaking = is_agent_speaking
    handler._expecting_cancel_interrupt = False
    handler._next_speech_is_filler = next_speech_is_filler

    # Mock _call_state with async wait methods
    handler._call_state = MagicMock()
    handler._call_state.wait_for_agent_speaking_stopped = AsyncMock(return_value=True)

    if has_session:
        handler.session = MagicMock()
        if has_model:
            handler.session._model = MagicMock()
            handler.session._model.send_event = AsyncMock()
        else:
            del handler.session._model
    else:
        handler.session = None

    if has_schedule_next_filler:
        handler._schedule_next_filler = MagicMock()
    else:
        del handler._schedule_next_filler

    return handler


def create_mock_logger():
    """Create a mock logger."""
    return MagicMock()


class TestCancelActiveFiller:
    """Tests for _cancel_active_filler function."""

    @pytest.mark.asyncio
    async def test_cancel_sets_expecting_interrupt_flag(self):
        """Test that cancel sets the expecting interrupt flag before sending event."""
        handler = create_mock_handler()
        logger = create_mock_logger()

        await _cancel_active_filler(handler, logger)

        # Flag should be reset to False after completion
        assert handler._expecting_cancel_interrupt is False
        handler.session._model.send_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_resets_flag_on_exception(self):
        """Test that the expecting interrupt flag is reset even on exception."""
        handler = create_mock_handler()
        handler.session._model.send_event = AsyncMock(side_effect=Exception("Send failed"))
        logger = create_mock_logger()

        await _cancel_active_filler(handler, logger)

        # Flag should still be reset to False
        assert handler._expecting_cancel_interrupt is False
        logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_cancel_logs_success(self):
        """Test that successful cancellation is logged."""
        handler = create_mock_handler()
        logger = create_mock_logger()

        await _cancel_active_filler(handler, logger)

        logger.info.assert_called_with("Cancelled active filler")

    @pytest.mark.asyncio
    async def test_cancel_logs_failure(self):
        """Test that failed cancellation is logged as debug."""
        handler = create_mock_handler()
        handler.session._model.send_event = AsyncMock(side_effect=Exception("Network error"))
        logger = create_mock_logger()

        await _cancel_active_filler(handler, logger)

        # Should log debug message with error
        assert any("cancel" in str(call).lower() for call in logger.debug.call_args_list)


class TestWaitForFillerCompletion:
    """Tests for _wait_for_filler_completion function."""

    @pytest.mark.asyncio
    async def test_returns_true_immediately_if_not_speaking(self):
        """Test that function returns True immediately if agent is not speaking."""
        handler = create_mock_handler(is_agent_speaking=False)
        logger = create_mock_logger()

        result = await _wait_for_filler_completion(handler, logger, timeout=5.0)

        assert result is True
        logger.debug.assert_called_with("Agent not speaking, no need to wait")

    @pytest.mark.asyncio
    async def test_returns_true_when_filler_completes(self):
        """Test that function returns True when filler completes within timeout."""
        handler = create_mock_handler(is_agent_speaking=True)
        handler._call_state.wait_for_agent_speaking_stopped = AsyncMock(return_value=True)
        logger = create_mock_logger()

        result = await _wait_for_filler_completion(handler, logger, timeout=5.0)

        assert result is True
        handler._call_state.wait_for_agent_speaking_stopped.assert_called_once_with(timeout_seconds=5.0)

    @pytest.mark.asyncio
    async def test_returns_false_on_timeout(self):
        """Test that function returns False when timeout is reached."""
        handler = create_mock_handler(is_agent_speaking=True)
        handler._call_state.wait_for_agent_speaking_stopped = AsyncMock(return_value=False)
        logger = create_mock_logger()

        result = await _wait_for_filler_completion(handler, logger, timeout=1.0)

        assert result is False
        handler._call_state.wait_for_agent_speaking_stopped.assert_called_once_with(timeout_seconds=1.0)
        assert any("Timeout" in str(call) for call in logger.info.call_args_list)


class TestHandleFillerBeforeThinkerResponse:
    """Tests for _handle_filler_before_thinker_response function."""

    @pytest.mark.asyncio
    async def test_skips_if_no_session(self):
        """Test that function skips handling if no session available."""
        handler = create_mock_handler(has_session=False)
        logger = create_mock_logger()

        await _handle_filler_before_thinker_response(handler, logger)

        logger.debug.assert_called_with("No session or model available, skipping filler handling")

    @pytest.mark.asyncio
    async def test_skips_if_no_model(self):
        """Test that function skips handling if no model available."""
        handler = create_mock_handler(has_model=False)
        logger = create_mock_logger()

        await _handle_filler_before_thinker_response(handler, logger)

        logger.debug.assert_called_with("No session or model available, skipping filler handling")

    @pytest.mark.asyncio
    async def test_reschedules_filler_timer_at_start_and_end(self):
        """Test that filler timer is rescheduled at start and end."""
        handler = create_mock_handler()
        logger = create_mock_logger()

        with patch("agent_leasing.settings.settings.filler_handling_strategy", "cancel"):
            await _handle_filler_before_thinker_response(handler, logger)

        # Should be called twice - at start and end
        assert handler._schedule_next_filler.call_count == 2

    @pytest.mark.asyncio
    async def test_skips_reschedule_if_handler_lacks_method(self):
        """Test that reschedule is skipped if handler doesn't have the method."""
        handler = create_mock_handler(has_schedule_next_filler=False)
        logger = create_mock_logger()

        with patch("agent_leasing.settings.settings.filler_handling_strategy", "cancel"):
            # Should not raise
            await _handle_filler_before_thinker_response(handler, logger)

    # Cancel strategy tests

    @pytest.mark.asyncio
    async def test_cancel_strategy_with_active_filler(self):
        """Test cancel strategy when agent is actively speaking."""
        handler = create_mock_handler(is_agent_speaking=True)
        logger = create_mock_logger()

        with patch("agent_leasing.settings.settings.filler_handling_strategy", "cancel"):
            await _handle_filler_before_thinker_response(handler, logger)

        # Should have called send_event to cancel
        handler.session._model.send_event.assert_called()
        assert any("canceling immediately" in str(call) for call in logger.info.call_args_list)

    @pytest.mark.asyncio
    async def test_cancel_strategy_with_pending_filler(self):
        """Test cancel strategy when filler is pending but not playing."""
        handler = create_mock_handler(is_agent_speaking=False, next_speech_is_filler=True)
        logger = create_mock_logger()

        with patch("agent_leasing.settings.settings.filler_handling_strategy", "cancel"):
            await _handle_filler_before_thinker_response(handler, logger)

        # Should have reset the pending filler flag
        assert handler._next_speech_is_filler is False
        handler.session._model.send_event.assert_called()

    @pytest.mark.asyncio
    async def test_cancel_strategy_with_no_filler(self):
        """Test cancel strategy sends unconditional cancel even when no filler is detected."""
        handler = create_mock_handler(is_agent_speaking=False, next_speech_is_filler=False)
        logger = create_mock_logger()

        with patch("agent_leasing.settings.settings.filler_handling_strategy", "cancel"):
            await _handle_filler_before_thinker_response(handler, logger)

        # Should still send cancel to clear any VAD auto-triggered response
        handler.session._model.send_event.assert_called()
        assert any("unconditional cancel" in str(call).lower() for call in logger.info.call_args_list)

    # Wait strategy tests

    @pytest.mark.asyncio
    async def test_wait_strategy_filler_completes_naturally(self):
        """Test wait strategy sends unconditional cancel even when filler already completed."""
        handler = create_mock_handler(is_agent_speaking=False)  # Already done speaking
        logger = create_mock_logger()

        with patch("agent_leasing.settings.settings.filler_handling_strategy", "wait"):
            await _handle_filler_before_thinker_response(handler, logger)

        # Should still send cancel to clear any VAD auto-triggered response
        handler.session._model.send_event.assert_called()

    @pytest.mark.asyncio
    async def test_wait_strategy_timeout_then_cancel(self):
        """Test wait strategy cancels filler after timeout."""
        handler = create_mock_handler(is_agent_speaking=True)
        logger = create_mock_logger()

        # Simulate timeout (agent keeps speaking)
        with (
            patch("agent_leasing.settings.settings.filler_handling_strategy", "wait"),
            patch(
                "agent_leasing.agent.resident_one_agent.realtime._wait_for_filler_completion",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            await _handle_filler_before_thinker_response(handler, logger)

        # Should have called cancel
        handler.session._model.send_event.assert_called()

    @pytest.mark.asyncio
    async def test_wait_strategy_with_pending_filler_after_wait(self):
        """Test wait strategy handles pending filler after wait completes."""
        handler = create_mock_handler(is_agent_speaking=False, next_speech_is_filler=True)
        logger = create_mock_logger()

        with (
            patch("agent_leasing.settings.settings.filler_handling_strategy", "wait"),
            patch(
                "agent_leasing.agent.resident_one_agent.realtime._wait_for_filler_completion",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            await _handle_filler_before_thinker_response(handler, logger)

        # Should have reset pending flag and cancelled
        assert handler._next_speech_is_filler is False
        handler.session._model.send_event.assert_called()

    # Hybrid strategy tests

    @pytest.mark.asyncio
    async def test_hybrid_strategy_timeout_then_cancel(self):
        """Test hybrid strategy cancels after timeout."""
        handler = create_mock_handler(is_agent_speaking=True)
        logger = create_mock_logger()

        with (
            patch("agent_leasing.settings.settings.filler_handling_strategy", "hybrid"),
            patch("agent_leasing.settings.settings.filler_wait_timeout_seconds", 2.0),
            patch(
                "agent_leasing.agent.resident_one_agent.realtime._wait_for_filler_completion",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            await _handle_filler_before_thinker_response(handler, logger)

        handler.session._model.send_event.assert_called()
        assert any("forcing cancel" in str(call).lower() for call in logger.info.call_args_list)

    @pytest.mark.asyncio
    async def test_hybrid_strategy_no_filler(self):
        """Test hybrid strategy sends unconditional cancel even when no filler detected."""
        handler = create_mock_handler(is_agent_speaking=False, next_speech_is_filler=False)
        logger = create_mock_logger()

        with (
            patch("agent_leasing.settings.settings.filler_handling_strategy", "hybrid"),
            patch(
                "agent_leasing.agent.resident_one_agent.realtime._wait_for_filler_completion",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            await _handle_filler_before_thinker_response(handler, logger)

        # Should still send cancel to clear any VAD auto-triggered response
        handler.session._model.send_event.assert_called()

    # Unknown strategy tests

    @pytest.mark.asyncio
    async def test_unknown_strategy_falls_back_to_hybrid(self):
        """Test that unknown strategy falls back to hybrid behavior."""
        handler = create_mock_handler(is_agent_speaking=True)
        logger = create_mock_logger()

        with (
            patch("agent_leasing.settings.settings.filler_handling_strategy", "unknown_strategy"),
            patch("agent_leasing.settings.settings.filler_wait_timeout_seconds", 2.0),
            patch(
                "agent_leasing.agent.resident_one_agent.realtime._wait_for_filler_completion",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            await _handle_filler_before_thinker_response(handler, logger)

        # Should log warning about unknown strategy
        assert any("Unknown filler handling strategy" in str(call) for call in logger.warning.call_args_list)
        # Should still cancel the filler
        handler.session._model.send_event.assert_called()

    @pytest.mark.asyncio
    async def test_unknown_strategy_with_pending_filler(self):
        """Test that unknown strategy handles pending filler."""
        handler = create_mock_handler(is_agent_speaking=False, next_speech_is_filler=True)
        logger = create_mock_logger()

        with (
            patch("agent_leasing.settings.settings.filler_handling_strategy", "invalid"),
            patch(
                "agent_leasing.agent.resident_one_agent.realtime._wait_for_filler_completion",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            await _handle_filler_before_thinker_response(handler, logger)

        assert handler._next_speech_is_filler is False
        handler.session._model.send_event.assert_called()


def _create_thinker_context(history=None, previous_response_id=None, openai_conversation_id=None):
    """Create a mock SessionScope context for thinker tool tests."""
    context = MagicMock()
    context.thinker_running = False
    context.history = history or []
    context.previous_response_id = previous_response_id
    context.openai_conversation_id = openai_conversation_id
    context.ask_request = MagicMock()
    context.ask_request.product = "test"
    context.ask_request.property_id = "123"
    context.ask_request.product_info.property_name = "Test Property"
    context.openai_group_id = "group-1"
    context.openai_group_url = None
    context.call_state_manager = None
    return context


def _thinker_patches(mock_output, ensure_conversation_id_return=None):
    """Return patches needed to run the thinker tool without real infrastructure."""
    mock_trace_cm = AsyncMock()
    mock_trace_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    mock_trace_cm.__aexit__ = AsyncMock(return_value=False)

    return (
        patch("agent_leasing.agent.resident_one_agent.realtime.trace", return_value=mock_trace_cm),
        patch(
            "agent_leasing.agent.resident_one_agent.realtime.run_agent_with_orphan_recovery",
            new_callable=AsyncMock,
            return_value=mock_output,
        ),
        patch(
            "agent_leasing.agent.resident_one_agent.realtime.ItemHelpers.text_message_outputs",
            return_value="test response",
        ),
        patch("agent_leasing.agent.resident_one_agent.realtime.add_metadata_into_context"),
        patch(
            "agent_leasing.agent.resident_one_agent.realtime._handle_filler_before_thinker_response",
            new_callable=AsyncMock,
        ),
        patch("agent_leasing.settings.settings.preamble_speech_detection_enabled", False),
        patch(
            "agent_leasing.agent.resident_one_agent.realtime.ensure_conversation_id",
            new_callable=AsyncMock,
            return_value=ensure_conversation_id_return,
        ),
    )


class TestThinkerToolHistoryDedup:
    """Tests for thinker tool history deduplication logic."""

    @pytest.mark.asyncio
    async def test_includes_history_when_no_previous_response_id(self):
        """First thinker call (no response chain) should prepend history."""
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        context = _create_thinker_context(history=history, previous_response_id=None)
        tool = create_thinker_tool(context, MagicMock())

        mock_output = MagicMock()
        mock_output.last_response_id = "resp-1"
        mock_output.new_items = []

        patches = _thinker_patches(mock_output)
        with patches[0], patches[1] as mock_run, patches[2], patches[3], patches[4], patches[5], patches[6]:
            tool_ctx = ToolContext(
                context=context,
                tool_name=THINKER_TOOL_NAME,
                tool_call_id="call-1",
                tool_arguments="{}",
            )
            await tool.on_invoke_tool(tool_ctx, '{"input": "What is my rent?"}')

        input_items = mock_run.call_args.kwargs["input"]
        assert len(input_items) == 3
        assert input_items[0] == history[0]
        assert input_items[1] == history[1]
        # Input should include the raw transcript annotation from history
        assert input_items[2]["role"] == "user"
        assert "What is my rent?" in input_items[2]["content"]
        assert '[Latest user transcript: "hello"]' in input_items[2]["content"]

    @pytest.mark.asyncio
    async def test_skips_history_when_previous_response_id_set(self):
        """Subsequent thinker calls with response chain should skip history."""
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        context = _create_thinker_context(history=history, previous_response_id="resp-prev-123")
        tool = create_thinker_tool(context, MagicMock())

        mock_output = MagicMock()
        mock_output.last_response_id = "resp-2"
        mock_output.new_items = []

        patches = _thinker_patches(mock_output)
        with patches[0], patches[1] as mock_run, patches[2], patches[3], patches[4], patches[5], patches[6]:
            tool_ctx = ToolContext(
                context=context,
                tool_name=THINKER_TOOL_NAME,
                tool_call_id="call-2",
                tool_arguments="{}",
            )
            await tool.on_invoke_tool(tool_ctx, '{"input": "What is my rent?"}')

        input_items = mock_run.call_args.kwargs["input"]
        assert len(input_items) == 1
        # Input should include the raw transcript annotation from history
        assert input_items[0]["role"] == "user"
        assert "What is my rent?" in input_items[0]["content"]
        assert '[Latest user transcript: "hello"]' in input_items[0]["content"]


class TestThinkerToolTranscriptInjection:
    """Tests for raw transcript injection into thinker input."""

    @pytest.mark.asyncio
    async def test_appends_latest_user_transcript_to_input(self):
        """Latest user transcript from history should be appended to thinker input."""
        history = [
            {"role": "user", "content": "Euro 3"},
            {"role": "assistant", "content": "I heard unit 303"},
        ]
        context = _create_thinker_context(history=history, previous_response_id=None)
        tool = create_thinker_tool(context, MagicMock())

        mock_output = MagicMock()
        mock_output.last_response_id = "resp-1"
        mock_output.new_items = []

        patches = _thinker_patches(mock_output)
        with patches[0], patches[1] as mock_run, patches[2], patches[3], patches[4], patches[5], patches[6]:
            tool_ctx = ToolContext(
                context=context,
                tool_name=THINKER_TOOL_NAME,
                tool_call_id="call-1",
                tool_arguments="{}",
            )
            await tool.on_invoke_tool(tool_ctx, '{"input": "Unit 303"}')

        input_items = mock_run.call_args.kwargs["input"]
        # The user content in the last input item should contain the transcript annotation
        user_input = input_items[-1]["content"]
        assert '[Latest user transcript: "Euro 3"]' in user_input

    @pytest.mark.asyncio
    async def test_finds_last_user_message_skipping_assistant(self):
        """Should find the latest user message even if last item is assistant."""
        history = [
            {"role": "user", "content": "my unit is 3"},
            {"role": "assistant", "content": "Got it, unit 303"},
        ]
        context = _create_thinker_context(history=history, previous_response_id=None)
        tool = create_thinker_tool(context, MagicMock())

        mock_output = MagicMock()
        mock_output.last_response_id = "resp-1"
        mock_output.new_items = []

        patches = _thinker_patches(mock_output)
        with patches[0], patches[1] as mock_run, patches[2], patches[3], patches[4], patches[5], patches[6]:
            tool_ctx = ToolContext(
                context=context,
                tool_name=THINKER_TOOL_NAME,
                tool_call_id="call-1",
                tool_arguments="{}",
            )
            await tool.on_invoke_tool(tool_ctx, '{"input": "Unit 303"}')

        user_input = mock_run.call_args.kwargs["input"][-1]["content"]
        assert '[Latest user transcript: "my unit is 3"]' in user_input

    @pytest.mark.asyncio
    async def test_no_transcript_when_history_empty(self):
        """No transcript annotation when history is empty."""
        context = _create_thinker_context(history=[], previous_response_id=None)
        tool = create_thinker_tool(context, MagicMock())

        mock_output = MagicMock()
        mock_output.last_response_id = "resp-1"
        mock_output.new_items = []

        patches = _thinker_patches(mock_output)
        with patches[0], patches[1] as mock_run, patches[2], patches[3], patches[4], patches[5], patches[6]:
            tool_ctx = ToolContext(
                context=context,
                tool_name=THINKER_TOOL_NAME,
                tool_call_id="call-1",
                tool_arguments="{}",
            )
            await tool.on_invoke_tool(tool_ctx, '{"input": "Unit 303"}')

        user_input = mock_run.call_args.kwargs["input"][-1]["content"]
        assert "[Latest user transcript:" not in user_input
        assert user_input == "Unit 303"

    @pytest.mark.asyncio
    async def test_no_transcript_when_no_user_messages(self):
        """No transcript annotation when history has only assistant messages."""
        history = [
            {"role": "assistant", "content": "Welcome!"},
        ]
        context = _create_thinker_context(history=history, previous_response_id=None)
        tool = create_thinker_tool(context, MagicMock())

        mock_output = MagicMock()
        mock_output.last_response_id = "resp-1"
        mock_output.new_items = []

        patches = _thinker_patches(mock_output)
        with patches[0], patches[1] as mock_run, patches[2], patches[3], patches[4], patches[5], patches[6]:
            tool_ctx = ToolContext(
                context=context,
                tool_name=THINKER_TOOL_NAME,
                tool_call_id="call-1",
                tool_arguments="{}",
            )
            await tool.on_invoke_tool(tool_ctx, '{"input": "Unit 303"}')

        user_input = mock_run.call_args.kwargs["input"][-1]["content"]
        assert "[Latest user transcript:" not in user_input

    @pytest.mark.asyncio
    async def test_transcript_appended_with_previous_response_id(self):
        """Transcript should still be appended even when previous_response_id is set (the key fix)."""
        history = [
            {"role": "user", "content": "Euro 3"},
            {"role": "assistant", "content": "I heard 303"},
        ]
        context = _create_thinker_context(history=history, previous_response_id="resp-prev-123")
        tool = create_thinker_tool(context, MagicMock())

        mock_output = MagicMock()
        mock_output.last_response_id = "resp-2"
        mock_output.new_items = []

        patches = _thinker_patches(mock_output)
        with patches[0], patches[1] as mock_run, patches[2], patches[3], patches[4], patches[5], patches[6]:
            tool_ctx = ToolContext(
                context=context,
                tool_name=THINKER_TOOL_NAME,
                tool_call_id="call-2",
                tool_arguments="{}",
            )
            await tool.on_invoke_tool(tool_ctx, '{"input": "Unit 303"}')

        input_items = mock_run.call_args.kwargs["input"]
        # History should NOT be prepended (previous_response_id provides continuity)
        assert len(input_items) == 1
        # But transcript should still be appended to the input string
        assert '[Latest user transcript: "Euro 3"]' in input_items[0]["content"]


class TestThinkerToolLanguageBehavior:
    """Tests that the thinker does NOT change language on voice.

    The responder is the sole authority for language via set_conversation_language.
    The thinker must never modify language_code on voice — it follows whatever the
    responder set.
    """

    def _make_context_and_run_context(self, resident_context_voice_knck):
        context = resident_context_voice_knck
        context.language_code = "en"
        context.history = []
        context.previous_response_id = None
        tool_call = ResponseFunctionToolCall(
            arguments="{}",
            call_id="test-call-id",
            name=THINKER_TOOL_NAME,
            type="function_call",
        )
        mock_run_context = ToolContext.from_agent_context(
            RunContextWrapper(context=context),
            tool_call_id="test-call-id",
            tool_call=tool_call,
        )
        return context, mock_run_context

    def _make_mock_output(self):
        mock_output = MagicMock()
        mock_output.last_response_id = "test-response-id"
        mock_output.new_items = []
        return mock_output

    @pytest.mark.asyncio
    async def test_thinker_does_not_set_language_even_when_default(self, resident_context_voice_knck):
        """Thinker must not change language_code even when it's still the default 'en'.
        Only the responder can set language on voice via set_conversation_language."""
        context, mock_run_context = self._make_context_and_run_context(resident_context_voice_knck)

        mock_thinker = MagicMock()
        mock_thinker.agent.return_value = MagicMock()
        tool = create_thinker_tool(context, mock_thinker)

        thinker_json = json.dumps({"response": "Hola, ¿en qué puedo ayudarte?", "language_code": "es"})

        with (
            patch(
                "agent_leasing.agent.resident_one_agent.realtime.run_agent_with_orphan_recovery",
                new_callable=AsyncMock,
            ) as mock_runner_run,
            patch(
                "agent_leasing.agent.resident_one_agent.realtime.ItemHelpers.text_message_outputs",
                return_value=thinker_json,
            ),
        ):
            mock_runner_run.return_value = self._make_mock_output()
            await tool.on_invoke_tool(mock_run_context, '{"input": "Hola"}')

        # Thinker must NOT change language — responder is sole authority
        assert mock_run_context.context.language_code == "en"

    @pytest.mark.asyncio
    async def test_thinker_does_not_override_responder(self, resident_context_voice_knck):
        """When responder has already called set_conversation_language,
        thinker's language_code must NOT override it."""
        context, mock_run_context = self._make_context_and_run_context(resident_context_voice_knck)
        # Simulate responder having already set language to French
        mock_run_context.context.language_code = "fr"

        mock_thinker = MagicMock()
        mock_thinker.agent.return_value = MagicMock()
        tool = create_thinker_tool(context, mock_thinker)

        thinker_json = json.dumps({"response": "Hola!", "language_code": "es"})

        with (
            patch(
                "agent_leasing.agent.resident_one_agent.realtime.run_agent_with_orphan_recovery",
                new_callable=AsyncMock,
            ) as mock_runner_run,
            patch(
                "agent_leasing.agent.resident_one_agent.realtime.ItemHelpers.text_message_outputs",
                return_value=thinker_json,
            ),
        ):
            mock_runner_run.return_value = self._make_mock_output()
            await tool.on_invoke_tool(mock_run_context, '{"input": "Bonjour"}')

        # Responder's language must be preserved
        assert mock_run_context.context.language_code == "fr"

    @pytest.mark.asyncio
    async def test_thinker_fallback_skips_english(self, resident_context_voice_knck):
        """Thinker returning 'en' when context is already 'en' is a no-op."""
        context, mock_run_context = self._make_context_and_run_context(resident_context_voice_knck)

        mock_thinker = MagicMock()
        mock_thinker.agent.return_value = MagicMock()
        tool = create_thinker_tool(context, mock_thinker)

        thinker_json = json.dumps({"response": "Your rent is $1500.", "language_code": "en"})

        with (
            patch(
                "agent_leasing.agent.resident_one_agent.realtime.run_agent_with_orphan_recovery",
                new_callable=AsyncMock,
            ) as mock_runner_run,
            patch(
                "agent_leasing.agent.resident_one_agent.realtime.ItemHelpers.text_message_outputs",
                return_value=thinker_json,
            ),
        ):
            mock_runner_run.return_value = self._make_mock_output()
            await tool.on_invoke_tool(mock_run_context, '{"input": "What is my rent?"}')

        assert mock_run_context.context.language_code == "en"

    @pytest.mark.asyncio
    async def test_thinker_does_not_set_language_locked_flag(self, resident_context_voice_knck):
        """Thinker must not set _language_locked — that mechanism is removed."""
        context, mock_run_context = self._make_context_and_run_context(resident_context_voice_knck)

        mock_thinker = MagicMock()
        mock_thinker.agent.return_value = MagicMock()
        tool = create_thinker_tool(context, mock_thinker)

        thinker_json = json.dumps({"response": "Bonjour!", "language_code": "fr"})

        with (
            patch(
                "agent_leasing.agent.resident_one_agent.realtime.run_agent_with_orphan_recovery",
                new_callable=AsyncMock,
            ) as mock_runner_run,
            patch(
                "agent_leasing.agent.resident_one_agent.realtime.ItemHelpers.text_message_outputs",
                return_value=thinker_json,
            ),
        ):
            mock_runner_run.return_value = self._make_mock_output()
            await tool.on_invoke_tool(mock_run_context, '{"input": "Bonjour"}')

        assert not hasattr(mock_run_context.context, "_language_locked")

    @pytest.mark.asyncio
    async def test_thinker_extracts_response_from_json(self, resident_context_voice_knck):
        """Thinker should still extract the response field from JSON output."""
        context, mock_run_context = self._make_context_and_run_context(resident_context_voice_knck)

        mock_thinker = MagicMock()
        mock_thinker.agent.return_value = MagicMock()
        tool = create_thinker_tool(context, mock_thinker)

        thinker_json = json.dumps({"response": "Your rent is $1500.", "language_code": "en"})

        with (
            patch(
                "agent_leasing.agent.resident_one_agent.realtime.run_agent_with_orphan_recovery",
                new_callable=AsyncMock,
            ) as mock_runner_run,
            patch(
                "agent_leasing.agent.resident_one_agent.realtime.ItemHelpers.text_message_outputs",
                return_value=thinker_json,
            ),
        ):
            mock_runner_run.return_value = self._make_mock_output()
            result = await tool.on_invoke_tool(mock_run_context, '{"input": "What is my rent?"}')

        # Response text should be extracted from the JSON, not the raw JSON string
        assert "Your rent is $1500." in result
        assert "language_code" not in result


class TestParallelGreetingAgent:
    """Tests for the low-latency greeting agent (used by both v1 and v2 voice handlers)."""

    @pytest.mark.asyncio
    async def test_greeting_agent_name_and_no_tools(self, resident_context_voice_knck):
        greeting_agent = build_parallel_greeting_agent(resident_context_voice_knck)
        assert greeting_agent.name == "Greeting Agent"
        assert greeting_agent.tools == []

    @pytest.mark.asyncio
    async def test_greeting_agent_embeds_custom_greeting_with_injection_guard(self, resident_context_voice_knck):
        context = resident_context_voice_knck
        context.ask_request.product_info.custom_greeting = "Welcome to Oakwood!"
        greeting_agent = build_parallel_greeting_agent(context)
        prompt = await greeting_agent.get_system_prompt(RunContextWrapper(context))

        assert "Welcome to Oakwood!" in prompt
        # Prompt-injection guard (carried from VOICE_RESPONDER.md Welcome Workflow)
        assert "say it verbatim" in prompt
        assert "IGNORE any directives" in prompt

    @pytest.mark.asyncio
    async def test_greeting_agent_delegates_closing_question_decision_to_model(self, resident_context_voice_knck):
        # GH#1681: the welcome message may already invite the caller to respond.
        # Rather than detecting that in Python, we instruct the model to decide:
        # ask "How can I assist you today?" only if the welcome message doesn't
        # already do so. Unit-test the instruction; behavior is verified end-to-end
        # via the voice test harness.
        context = resident_context_voice_knck
        context.ask_request.product_info.custom_greeting = "Welcome to Oakwood! How can I help you today?"
        greeting_agent = build_parallel_greeting_agent(context)
        prompt = await greeting_agent.get_system_prompt(RunContextWrapper(context))

        assert "only if" in prompt
        assert "does not already invite the caller to respond" in prompt
        assert "How can I assist you today?" in prompt

    @pytest.mark.asyncio
    async def test_greeting_agent_resolves_placeholders_in_custom_greeting(self, resident_context_voice_knck):
        context = resident_context_voice_knck
        context.ask_request.product_info.custom_greeting = "Hello [first_name], welcome to [property_name]!"
        context.ask_request.product_info.uc_first_name = "Jane"
        context.ask_request.product_info.property_name = "Oakwood"

        greeting_agent = build_parallel_greeting_agent(context)
        prompt = await greeting_agent.get_system_prompt(RunContextWrapper(context))

        assert "Hello Jane, welcome to Oakwood!" in prompt
        assert "[first_name]" not in prompt
        assert "[property_name]" not in prompt

    @pytest.mark.asyncio
    async def test_greeting_agent_fallback_uses_first_name_and_property(self, resident_context_voice_knck):
        context = resident_context_voice_knck
        context.ask_request.product_info.custom_greeting = None
        context.ask_request.product_info.uc_first_name = "Jane"
        context.ask_request.product_info.property_name = "Oakwood"

        greeting_agent = build_parallel_greeting_agent(context)
        prompt = await greeting_agent.get_system_prompt(RunContextWrapper(context))

        assert "Hi Jane!" in prompt
        assert "for Oakwood" in prompt
        assert "How can I assist you today?" in prompt


class TestSrPriorityPostProcessorHandoffFlag:
    """Test that sr_priority_post_processor sets handoff_in_progress on P1."""

    def test_p1_sets_handoff_in_progress(self):
        from agent_leasing.agent.tools.mcp_post_processors import sr_priority_post_processor

        data = {"service_request_id": "123", "agent_response": "Done.", "priority_number": "1", "priority_name": "Emg"}
        result = CallToolResult(content=[TextContent(text=json.dumps(data), type="text")], structuredContent=None)
        context = SessionScope()
        assert context.handoff_in_progress is False
        sr_priority_post_processor(result, context=context)
        assert context.handoff_in_progress is True

    def test_non_p1_does_not_set_handoff_in_progress(self):
        from agent_leasing.agent.tools.mcp_post_processors import sr_priority_post_processor

        data = {"service_request_id": "123", "agent_response": "Done.", "priority_number": "3", "priority_name": "Std"}
        result = CallToolResult(content=[TextContent(text=json.dumps(data), type="text")], structuredContent=None)
        context = SessionScope()
        sr_priority_post_processor(result, context=context)
        assert context.handoff_in_progress is False


class TestThinkerToolConversationId:
    """Tests for conversation_id handling in the voice thinker tool."""

    @pytest.mark.asyncio
    async def test_passes_conversation_id_when_enabled(self):
        """When conversations API is enabled, thinker passes conversation_id to Runner.run."""
        context = _create_thinker_context(history=[], previous_response_id=None)
        tool = create_thinker_tool(context, MagicMock())

        mock_output = MagicMock()
        mock_output.last_response_id = "resp-1"
        mock_output.new_items = []

        patches = _thinker_patches(mock_output, ensure_conversation_id_return="conv_voice_123")
        with patches[0], patches[1] as mock_run, patches[2], patches[3], patches[4], patches[5], patches[6]:
            tool_ctx = ToolContext(
                context=context,
                tool_name=THINKER_TOOL_NAME,
                tool_call_id="call-1",
                tool_arguments="{}",
            )
            await tool.on_invoke_tool(tool_ctx, '{"input": "Can I keep rabbits?"}')

        assert mock_run.call_args.kwargs["conversation_id"] == "conv_voice_123"
        # previous_response_id should be None when conversation_id is set
        assert mock_run.call_args.kwargs["previous_response_id"] is None

    @pytest.mark.asyncio
    async def test_passes_previous_response_id_when_disabled(self):
        """When conversations API is disabled, thinker passes previous_response_id."""
        context = _create_thinker_context(history=[], previous_response_id="resp-prev-456")
        tool = create_thinker_tool(context, MagicMock())

        mock_output = MagicMock()
        mock_output.last_response_id = "resp-2"
        mock_output.new_items = []

        patches = _thinker_patches(mock_output, ensure_conversation_id_return=None)
        with patches[0], patches[1] as mock_run, patches[2], patches[3], patches[4], patches[5], patches[6]:
            tool_ctx = ToolContext(
                context=context,
                tool_name=THINKER_TOOL_NAME,
                tool_call_id="call-2",
                tool_arguments="{}",
            )
            await tool.on_invoke_tool(tool_ctx, '{"input": "What is my rent?"}')

        assert mock_run.call_args.kwargs["previous_response_id"] == "resp-prev-456"
        assert mock_run.call_args.kwargs["conversation_id"] is None

    @pytest.mark.asyncio
    async def test_captures_last_response_id_with_conversation_api(self):
        """previous_response_id is still captured from output even when using conversations."""
        context = _create_thinker_context(history=[], previous_response_id=None)
        tool = create_thinker_tool(context, MagicMock())

        mock_output = MagicMock()
        mock_output.last_response_id = "resp-new-789"
        mock_output.new_items = []

        patches = _thinker_patches(mock_output, ensure_conversation_id_return="conv_voice_abc")
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            tool_ctx = ToolContext(
                context=context,
                tool_name=THINKER_TOOL_NAME,
                tool_call_id="call-3",
                tool_arguments="{}",
            )
            await tool.on_invoke_tool(tool_ctx, '{"input": "Check my packages"}')

        # previous_response_id should be captured for history gating
        context.previous_response_id = "resp-new-789"

    @pytest.mark.asyncio
    async def test_multi_turn_voice_conversation(self):
        """Multi-turn: first call includes history, second call skips it."""
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

        # --- First thinker call: no previous_response_id -> history included ---
        context = _create_thinker_context(history=history, previous_response_id=None, openai_conversation_id=None)
        tool = create_thinker_tool(context, MagicMock())

        mock_output_1 = MagicMock()
        mock_output_1.last_response_id = "resp-turn-1"
        mock_output_1.new_items = []

        patches = _thinker_patches(mock_output_1, ensure_conversation_id_return="conv_multi")
        with patches[0], patches[1] as mock_run, patches[2], patches[3], patches[4], patches[5], patches[6]:
            tool_ctx = ToolContext(
                context=context,
                tool_name=THINKER_TOOL_NAME,
                tool_call_id="call-t1",
                tool_arguments="{}",
            )
            await tool.on_invoke_tool(tool_ctx, '{"input": "Can I keep rabbits?"}')

        input_items_1 = mock_run.call_args.kwargs["input"]
        # History (2 items) + current input (1 item) = 3
        assert len(input_items_1) == 3

        # --- Second thinker call: previous_response_id is now set -> history skipped ---
        context.previous_response_id = "resp-turn-1"
        context.thinker_running = False  # Reset for next call
        tool2 = create_thinker_tool(context, MagicMock())

        mock_output_2 = MagicMock()
        mock_output_2.last_response_id = "resp-turn-2"
        mock_output_2.new_items = []

        patches2 = _thinker_patches(mock_output_2, ensure_conversation_id_return="conv_multi")
        with patches2[0], patches2[1] as mock_run2, patches2[2], patches2[3], patches2[4], patches2[5], patches2[6]:
            tool_ctx2 = ToolContext(
                context=context,
                tool_name=THINKER_TOOL_NAME,
                tool_call_id="call-t2",
                tool_arguments="{}",
            )
            await tool2.on_invoke_tool(tool_ctx2, '{"input": "What about cats?"}')

        input_items_2 = mock_run2.call_args.kwargs["input"]
        # History skipped, only current input
        assert len(input_items_2) == 1
        assert mock_run2.call_args.kwargs["conversation_id"] == "conv_multi"
