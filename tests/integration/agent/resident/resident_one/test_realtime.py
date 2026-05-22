"""Tests for resident_one_agent realtime module."""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agents import RunContextWrapper
from agents.realtime import RealtimeAgent
from agents.tool_context import ToolContext
from openai.types.responses import ResponseFunctionToolCall

from agent_leasing.agent.resident_one_agent.realtime import (
    THINKER_TOOL_DESCRIPTION,
    THINKER_TOOL_NAME,
    ResidentRealtimeResponderAgent,
    create_thinker_tool,
)
from agent_leasing.settings import settings


@contextmanager
def _responder_agent_patches():
    """Shared patches for ResidentRealtimeResponderAgent tests.

    Mocks external dependencies so the agent can be initialized
    without MCP servers, LDP calls, or data prefetching.
    """
    with (
        patch(
            "agent_leasing.agent.resident_one_agent.agent.get_disabled_modules_with_pte",
            new_callable=AsyncMock,
            return_value=([], False),
        ),
        patch(
            "agent_leasing.agent.resident_one_agent.agent.get_mcp_servers",
            return_value={},
        ),
        patch("agent_leasing.agent.resident_one_agent.agent.custom_span") as mock_span,
        patch("agent_leasing.agent.resident_one_agent.agent.set_span_data"),
        patch(
            "agent_leasing.agent.resident_one_agent.agent_helper.prefetch_property_overview_and_insights",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch.object(settings, "emergency_service_transfer_advanced_enabled", True),
    ):
        mock_span.return_value.__enter__ = MagicMock()
        mock_span.return_value.__exit__ = MagicMock(return_value=False)
        yield


def _create_mock_thinker_agent():
    """Create a mock thinker agent for testing."""
    mock_thinker = MagicMock()
    mock_thinker.agent.return_value = MagicMock()
    return mock_thinker


class TestCreateThinkerTool:
    """Tests for create_thinker_tool function."""

    def test_creates_function_tool_with_correct_name(self, resident_context_voice_knck):
        """Test that create_thinker_tool creates a tool with the correct name."""
        mock_thinker = _create_mock_thinker_agent()
        tool = create_thinker_tool(resident_context_voice_knck, mock_thinker)
        assert tool.name == THINKER_TOOL_NAME

    def test_creates_function_tool_with_correct_description(self, resident_context_voice_knck):
        """Test that create_thinker_tool creates a tool with the correct description."""
        mock_thinker = _create_mock_thinker_agent()
        tool = create_thinker_tool(resident_context_voice_knck, mock_thinker)
        assert tool.description == THINKER_TOOL_DESCRIPTION

    @pytest.mark.asyncio
    async def test_thinker_tool_calls_resident_agent(self, resident_context_voice_knck):
        """Test that the thinker tool properly calls the pre-initialized ResidentAgent."""
        context = resident_context_voice_knck

        # Create a mock thinker agent
        mock_agent = MagicMock()
        mock_thinker = MagicMock()
        mock_thinker.agent.return_value = mock_agent

        tool = create_thinker_tool(context, mock_thinker)

        # Create a mock run context
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

        # Mock the Runner output
        mock_output = MagicMock()
        mock_output.last_response_id = "test-response-id"
        mock_output.new_items = []

        with (
            patch(
                "agent_leasing.agent.resident_one_agent.realtime.run_agent_with_orphan_recovery",
                new_callable=AsyncMock,
            ) as mock_runner_run,
            patch(
                "agent_leasing.agent.resident_one_agent.realtime.ItemHelpers.text_message_outputs",
                return_value="Test response",
            ),
        ):
            mock_runner_run.return_value = mock_output

            # Call the tool function directly
            result = await tool.on_invoke_tool(mock_run_context, '{"input": "What is my rent?"}')

            # Verify the thinker agent was used (agent() was called)
            mock_thinker.agent.assert_called_once()

            # Verify Runner.run was called
            mock_runner_run.assert_called_once()

            # Verify the response was humanized and returned
            assert "Test response" in result

    @pytest.mark.asyncio
    async def test_thinker_tool_handles_errors_gracefully(self, resident_context_voice_knck):
        """Test that the thinker tool handles errors gracefully."""
        context = resident_context_voice_knck

        # Create a mock thinker agent that raises an error
        mock_thinker = MagicMock()
        mock_thinker.agent.side_effect = Exception("Test error")

        tool = create_thinker_tool(context, mock_thinker)

        context.history = []
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

        result = await tool.on_invoke_tool(mock_run_context, '{"input": "Test input"}')

        # Should return a user-friendly error message
        assert "encountered an issue" in result.lower()

    @pytest.mark.asyncio
    async def test_thinker_tool_includes_history_without_response_chain(self, resident_context_voice_knck):
        """Test that the thinker tool includes history when no response chain exists (first call)."""
        context = resident_context_voice_knck

        # Create a mock thinker agent
        mock_agent = MagicMock()
        mock_thinker = MagicMock()
        mock_thinker.agent.return_value = mock_agent

        tool = create_thinker_tool(context, mock_thinker)

        context.history = [
            {"role": "user", "content": "Previous message"},
            {"role": "assistant", "content": "Previous response"},
        ]
        # No response chain yet — history should be prepended
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

        mock_output = MagicMock()
        mock_output.last_response_id = "new-response-id"
        mock_output.new_items = []

        with (
            patch(
                "agent_leasing.agent.resident_one_agent.realtime.run_agent_with_orphan_recovery",
                new_callable=AsyncMock,
            ) as mock_runner_run,
            patch(
                "agent_leasing.agent.resident_one_agent.realtime.ItemHelpers.text_message_outputs",
                return_value="Response",
            ),
        ):
            mock_runner_run.return_value = mock_output

            await tool.on_invoke_tool(mock_run_context, '{"input": "Current question"}')

            # Verify Runner.run was called with history prepended
            call_args = mock_runner_run.call_args
            input_items = call_args.kwargs.get("input") or call_args[1].get("input")

            # History should be prepended to the input
            assert len(input_items) == 3  # 2 history items + 1 current input
            assert input_items[0]["content"] == "Previous message"
            assert "Current question" in input_items[2]["content"]
            assert '[Latest user transcript: "Previous message"]' in input_items[2]["content"]


class TestResidentRealtimeResponderAgent:
    """Tests for ResidentRealtimeResponderAgent class."""

    def test_init_sets_correct_name(self, resident_context_voice_knck):
        """Test that __init__ sets the correct agent name."""
        agent = ResidentRealtimeResponderAgent(resident_context_voice_knck)
        assert agent.name == "resident-one-realtime-agent"

    def test_init_loads_responder_prompt(self, resident_context_voice_knck):
        """Test that __init__ loads the responder prompt."""
        agent = ResidentRealtimeResponderAgent(resident_context_voice_knck)
        assert agent.responder_prompt is not None
        assert len(agent.responder_prompt) > 0

    @pytest.mark.asyncio
    async def test_create_agent_returns_realtime_agent(self, resident_context_voice_knck):
        """Test that _create_agent returns a RealtimeAgent."""
        with _responder_agent_patches():
            async with ResidentRealtimeResponderAgent(resident_context_voice_knck) as agent_wrapper:
                agent = agent_wrapper.agent()
                assert isinstance(agent, RealtimeAgent)
                assert agent.name == "Realtime Resident Agent (One)"

    @pytest.mark.asyncio
    async def test_create_agent_includes_thinker_tool(self, resident_context_voice_knck):
        """Test that _create_agent includes the thinker tool."""
        with _responder_agent_patches():
            async with ResidentRealtimeResponderAgent(resident_context_voice_knck) as agent_wrapper:
                agent = agent_wrapper.agent()
                tool_names = [t.name for t in agent.tools]
                assert THINKER_TOOL_NAME in tool_names

    @pytest.mark.asyncio
    async def test_create_agent_includes_end_call_tool(self, resident_context_voice_knck):
        """Test that _create_agent includes the end_call tool."""
        with _responder_agent_patches():
            async with ResidentRealtimeResponderAgent(resident_context_voice_knck) as agent_wrapper:
                agent = agent_wrapper.agent()
                tool_names = [t.name for t in agent.tools]
                assert "end_call" in tool_names

    @pytest.mark.asyncio
    async def test_create_agent_includes_set_conversation_language(self, resident_context_voice_knck):
        """Test that _create_agent includes set_conversation_language for the responder."""
        with _responder_agent_patches():
            async with ResidentRealtimeResponderAgent(resident_context_voice_knck) as agent_wrapper:
                agent = agent_wrapper.agent()
                tool_names = [t.name for t in agent.tools]
                assert "set_conversation_language" in tool_names

    @pytest.mark.asyncio
    async def test_create_agent_includes_transfer_to_staff_voice(self, resident_context_voice_knck):
        """Test that _create_agent includes the correct tools."""

        with (
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_disabled_modules_with_pte",
                new_callable=AsyncMock,
                return_value=([], False),
            ),
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_mcp_servers",
                return_value={},
            ),
            patch("agent_leasing.agent.resident_one_agent.agent.custom_span") as mock_span,
            patch("agent_leasing.agent.resident_one_agent.agent.set_span_data"),
        ):
            mock_span.return_value.__enter__ = MagicMock()
            mock_span.return_value.__exit__ = MagicMock()

            with _responder_agent_patches():
                async with ResidentRealtimeResponderAgent(resident_context_voice_knck) as agent_wrapper:
                    agent = agent_wrapper.agent()
                    tool_names = [t.name for t in agent.tools]
                    # Verify expected tools are present.
                    # The knck payload is configured for RPCC (`dispatch_schedule_active: "RPCC"`).
                    assert "resident_thinker_tool" in tool_names
                    assert "end_call" in tool_names
                    assert "emergency_service_transfer_rpcc" in tool_names
                    assert "transfer_to_staff_voice" in tool_names
                    assert "set_conversation_language" in tool_names

    @pytest.mark.asyncio
    async def test_create_agent_loads_advanced_tool_when_product_is_advanced(self, resident_context_voice_knck):
        """Agent should load emergency_service_transfer_advanced when the property is ADVANCED."""
        # Override the payload's SKU so product resolves to ADVANCED instead of RPCC.
        resident_context_voice_knck.ask_request.product_info.dispatch_schedule_active = "AI Maintenance"

        with (
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_disabled_modules_with_pte",
                new_callable=AsyncMock,
                return_value=([], False),
            ),
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_mcp_servers",
                return_value={},
            ),
            patch("agent_leasing.agent.resident_one_agent.agent.custom_span") as mock_span,
            patch("agent_leasing.agent.resident_one_agent.agent.set_span_data"),
        ):
            mock_span.return_value.__enter__ = MagicMock()
            mock_span.return_value.__exit__ = MagicMock()

            with _responder_agent_patches():
                async with ResidentRealtimeResponderAgent(resident_context_voice_knck) as agent_wrapper:
                    agent = agent_wrapper.agent()
                    tool_names = [t.name for t in agent.tools]
                    assert "emergency_service_transfer_advanced" in tool_names
                    assert "emergency_service_transfer_rpcc" not in tool_names
