"""Tests for the thinker concurrency guard in create_thinker_tool.

The concurrency guard uses `context.thinker_running` on SessionScope that:
- Early-returns if the thinker is already running
- Sets the flag to True before the `async with trace` block
- Clears the flag to False in a `finally` block so it always resets,
  regardless of whether the function returns normally or via an exception handler
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_context():
    """Create a minimal mock SessionScope for create_thinker_tool."""
    ctx = MagicMock()
    ctx.history = []
    ctx.previous_response_id = None
    ctx.openai_group_id = "test-group"
    ctx.openai_group_url = "https://example.com/trace"
    ctx.thinker_running = False
    ctx.ask_request = MagicMock()
    ctx.ask_request.product = "test"
    ctx.ask_request.property_id = "prop-1"
    ctx.ask_request.product_info.property_name = "Test Property"
    # Remove _session_handler to skip filler handling
    if hasattr(ctx, "_session_handler"):
        del ctx._session_handler
    return ctx


def _make_mock_run_context(context):
    """Create a minimal mock RunContextWrapper."""
    run_ctx = MagicMock()
    run_ctx.context = context
    return run_ctx


def _make_mock_thinker_agent():
    """Create a minimal mock ResidentAgent for the thinker."""
    agent = MagicMock()
    agent.agent.return_value = MagicMock()
    return agent


def _passthrough_function_tool(**kwargs):
    """A passthrough replacement for @function_tool that returns the raw async function."""

    def decorator(fn):
        return fn

    return decorator


# We need to patch function_tool BEFORE importing create_thinker_tool so the
# decorator is replaced at definition time.  We use importlib to do a fresh
# import inside each test helper.


def _import_create_thinker_tool():
    """Import create_thinker_tool with function_tool patched as passthrough."""
    import importlib

    with patch("agents.function_tool", _passthrough_function_tool):
        # Force re-import so the decorator patch takes effect
        import agent_leasing.agent.resident_one_agent.realtime as mod

        importlib.reload(mod)
        return mod.create_thinker_tool


@pytest.fixture(autouse=True)
def _reload_realtime_after_test():
    """Reload the module after each test to undo the function_tool patch."""
    yield
    import importlib

    import agent_leasing.agent.resident_one_agent.realtime as mod

    importlib.reload(mod)


def _build_tool():
    """Create a thinker tool with patched decorator and return the raw async function."""
    create_thinker_tool = _import_create_thinker_tool()
    context = _make_mock_context()
    thinker_agent = _make_mock_thinker_agent()
    tool_fn = create_thinker_tool(context, thinker_agent)
    return tool_fn, context, thinker_agent


def _make_runner_output():
    """Create a mock Runner.run output."""
    output = MagicMock()
    output.last_response_id = "resp-123"
    output.new_items = []
    return output


class TestThinkerConcurrencyGuard:
    """Tests for the thinker_running concurrency guard in create_thinker_tool."""

    @pytest.mark.asyncio
    async def test_concurrent_call_returns_early_exit_message(self):
        """A second concurrent call returns the early-exit message while the first is running."""
        tool_fn, context, thinker_agent = _build_tool()
        run_ctx = _make_mock_run_context(context)

        # Make Runner.run hang so the first call stays "in progress"
        first_call_started = asyncio.Event()
        first_call_release = asyncio.Event()

        async def slow_runner(*args, **kwargs):
            first_call_started.set()
            await first_call_release.wait()
            return _make_runner_output()

        with (
            patch("agent_leasing.agent.resident_one_agent.realtime.trace") as mock_trace,
            patch(
                "agent_leasing.agent.resident_one_agent.realtime.run_agent_with_orphan_recovery",
                new_callable=AsyncMock,
            ) as mock_runner_run,
            patch("agent_leasing.agent.resident_one_agent.realtime.ItemHelpers") as mock_helpers,
            patch("agent_leasing.agent.resident_one_agent.realtime.add_metadata_into_context"),
            patch("agent_leasing.agent.resident_one_agent.realtime.settings") as mock_settings,
        ):
            mock_settings.preamble_speech_detection_enabled = False
            mock_trace.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_trace.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_runner_run.side_effect = slow_runner
            mock_helpers.text_message_outputs.return_value = "test response"

            # Start first call -- it will block inside Runner.run
            task1 = asyncio.create_task(tool_fn(run_ctx, "first request"))
            await first_call_started.wait()

            # Second call should get the early-exit message
            result2 = await tool_fn(run_ctx, "second request")
            assert "already processing" in result2

            # Release first call so it can complete
            first_call_release.set()
            result1 = await task1
            assert "VERBATIM" in result1  # Normal success path

    @pytest.mark.asyncio
    async def test_sequential_calls_both_succeed(self):
        """The flag is cleared after normal completion, so sequential calls both succeed."""
        tool_fn, context, thinker_agent = _build_tool()
        run_ctx = _make_mock_run_context(context)

        with (
            patch("agent_leasing.agent.resident_one_agent.realtime.trace") as mock_trace,
            patch(
                "agent_leasing.agent.resident_one_agent.realtime.run_agent_with_orphan_recovery",
                new_callable=AsyncMock,
            ) as mock_runner_run,
            patch("agent_leasing.agent.resident_one_agent.realtime.ItemHelpers") as mock_helpers,
            patch("agent_leasing.agent.resident_one_agent.realtime.add_metadata_into_context"),
            patch("agent_leasing.agent.resident_one_agent.realtime.settings") as mock_settings,
        ):
            mock_settings.preamble_speech_detection_enabled = False
            mock_trace.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_trace.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_runner_run.return_value = _make_runner_output()
            mock_helpers.text_message_outputs.return_value = "response text"

            result1 = await tool_fn(run_ctx, "first request")
            assert "VERBATIM" in result1

            result2 = await tool_fn(run_ctx, "second request")
            assert "VERBATIM" in result2

    @pytest.mark.asyncio
    async def test_flag_cleared_after_exception(self):
        """The flag is cleared after an exception, so the next call still works."""
        tool_fn, context, thinker_agent = _build_tool()
        run_ctx = _make_mock_run_context(context)

        with (
            patch("agent_leasing.agent.resident_one_agent.realtime.trace") as mock_trace,
            patch(
                "agent_leasing.agent.resident_one_agent.realtime.run_agent_with_orphan_recovery",
                new_callable=AsyncMock,
            ) as mock_runner_run,
            patch("agent_leasing.agent.resident_one_agent.realtime.ItemHelpers") as mock_helpers,
            patch("agent_leasing.agent.resident_one_agent.realtime.add_metadata_into_context"),
            patch("agent_leasing.agent.resident_one_agent.realtime.settings") as mock_settings,
        ):
            mock_settings.preamble_speech_detection_enabled = False
            mock_trace.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_trace.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_helpers.text_message_outputs.return_value = "response text"

            # First call raises an exception inside Runner.run
            mock_runner_run.side_effect = RuntimeError("something broke")
            result1 = await tool_fn(run_ctx, "failing request")
            assert "encountered an issue" in result1

            # Second call should succeed (flag was cleared)
            mock_runner_run.side_effect = None
            mock_runner_run.return_value = _make_runner_output()
            result2 = await tool_fn(run_ctx, "retry request")
            assert "VERBATIM" in result2

    @pytest.mark.asyncio
    async def test_concurrent_rejection_logs_info(self):
        """The concurrent rejection path logs at INFO (designed-behavior, not an app error)."""
        tool_fn, context, thinker_agent = _build_tool()
        run_ctx = _make_mock_run_context(context)

        first_call_started = asyncio.Event()
        first_call_release = asyncio.Event()

        async def slow_runner(*args, **kwargs):
            first_call_started.set()
            await first_call_release.wait()
            return _make_runner_output()

        with (
            patch("agent_leasing.agent.resident_one_agent.realtime.trace") as mock_trace,
            patch(
                "agent_leasing.agent.resident_one_agent.realtime.run_agent_with_orphan_recovery",
                new_callable=AsyncMock,
            ) as mock_runner_run,
            patch("agent_leasing.agent.resident_one_agent.realtime.ItemHelpers") as mock_helpers,
            patch("agent_leasing.agent.resident_one_agent.realtime.add_metadata_into_context"),
            patch("agent_leasing.agent.resident_one_agent.realtime.settings") as mock_settings,
            patch("agent_leasing.agent.resident_one_agent.realtime.logger") as mock_logger,
        ):
            mock_settings.preamble_speech_detection_enabled = False
            mock_trace.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_trace.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_runner_run.side_effect = slow_runner
            mock_helpers.text_message_outputs.return_value = "test response"

            task1 = asyncio.create_task(tool_fn(run_ctx, "first request"))
            await first_call_started.wait()

            # Second call triggers the info-level concurrency-guard log
            await tool_fn(run_ctx, "second request")
            mock_logger.info.assert_any_call("Thinker already running, skipping concurrent invocation")

            first_call_release.set()
            await task1
