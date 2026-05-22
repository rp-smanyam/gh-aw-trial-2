"""Tests for uncovered lines in agent_leasing.agent.util."""

import asyncio
from contextlib import AsyncExitStack
from unittest.mock import Mock, patch

import pytest

from agent_leasing.agent.util import (
    CHANNEL_INSTRUCTIONS,
    AgentArchitecture,
    AgentWithMCP,
    get_architecture_from_context,
    get_channel_instructions,
    log_internal_messages,
)
from agent_leasing.api.model import AskRequest, ProductInfo, UCReference
from agent_leasing.models.context import SessionScope

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _TestAgentWithMCPImpl(AgentWithMCP):
    async def _create_agent(self):
        return object()

    def agent(self):
        return self.agent_instance


def _make_context(product: str = "resident_one_chat") -> SessionScope:
    # Resident products require UCReference fields
    product_info_kwargs = {"knock_property_id": "123"}
    if "resident" in product.lower():
        product_info_kwargs.update(
            uc_company_id=UCReference(id=1, source="OS"),
            uc_property_id=UCReference(id=2, source="OS"),
            uc_resident_household_id=UCReference(id=3, source="OS"),
            uc_resident_member_id=UCReference(id=4, source="OS"),
            ab_resident_id=UCReference(id="res_1", source="AB"),
            uc_lease_id=UCReference(id=5, source="OS"),
            uc_portal_base_url="https://test.example.com",
        )
    req = AskRequest(
        product=product,
        request_id="test-request-id",
        chat_session_id="test-session-id",
        prompt="",
        product_info=ProductInfo(**product_info_kwargs),
    )
    return SessionScope(ask_request=req)


class _HangingMCP:
    """MCP mock whose connect() hangs forever, triggering TimeoutError."""

    client_session_timeout_seconds = 0.01

    async def connect(self):
        await asyncio.sleep(999)

    async def cleanup(self):
        pass


class _CancelRaisingMCP:
    """MCP mock whose connect() raises CancelledError directly."""

    client_session_timeout_seconds = 5

    async def connect(self):
        raise asyncio.CancelledError()

    async def cleanup(self):
        pass


class _GenericErrorMCP:
    """MCP mock whose connect() raises a generic Exception."""

    client_session_timeout_seconds = 5

    async def connect(self):
        raise RuntimeError("connection refused")

    async def cleanup(self):
        pass


# ---------------------------------------------------------------------------
# _connect_mcp_servers error paths (lines 96-127)
# ---------------------------------------------------------------------------


class TestConnectMCPServersErrorPaths:
    @pytest.mark.asyncio
    async def test_timeout_with_pending_connect_task(self):
        """TimeoutError path where connect_task is not done (lines 101-109).

        A hanging __aenter__ means the connect_task is still pending when
        wait_for raises TimeoutError.  The code should cancel the task and
        remove the server from mcp_servers.
        """
        mcp_servers = {"hanging": _HangingMCP()}
        exit_stack = AsyncExitStack()

        await AgentWithMCP._connect_mcp_servers(exit_stack, mcp_servers)

        assert "hanging" not in mcp_servers
        assert mcp_servers == {}

    @pytest.mark.asyncio
    async def test_cancelled_error_non_external(self):
        """CancelledError path where outer_task.cancelling() == 0 (lines 110-118).

        When __aenter__ raises CancelledError but the outer task is NOT being
        cancelled externally, the error is swallowed and the server is pruned.
        """
        mcp_servers = {"cancel_raiser": _CancelRaisingMCP()}
        exit_stack = AsyncExitStack()

        await AgentWithMCP._connect_mcp_servers(exit_stack, mcp_servers)

        assert "cancel_raiser" not in mcp_servers
        assert mcp_servers == {}

    @pytest.mark.asyncio
    async def test_cancelled_error_external_propagates(self):
        """CancelledError path where outer_task.cancelling() > 0 (lines 112-115).

        When the outer task is being externally cancelled, the CancelledError
        must propagate (re-raise).  We use a hanging MCP so the task is still
        in wait_for when we cancel the outer task.
        """

        class _SlowMCP:
            """MCP that hangs long enough for us to cancel the outer task."""

            client_session_timeout_seconds = 60

            async def connect(self):
                await asyncio.sleep(999)

            async def cleanup(self):
                pass

        async def run_connect():
            mcp_servers = {"slow": _SlowMCP()}
            exit_stack = AsyncExitStack()
            await AgentWithMCP._connect_mcp_servers(exit_stack, mcp_servers)

        task = asyncio.create_task(run_connect())
        # Give the task a chance to reach the await inside wait_for
        await asyncio.sleep(0.01)
        # Cancel the outer task externally
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_generic_exception_with_pending_connect_task(self):
        """General Exception path where connect_task is not done (lines 119-127).

        A RuntimeError during __aenter__ should cancel the pending connect_task
        and prune the server.
        """
        mcp_servers = {"broken": _GenericErrorMCP()}
        exit_stack = AsyncExitStack()

        await AgentWithMCP._connect_mcp_servers(exit_stack, mcp_servers)

        assert "broken" not in mcp_servers
        assert mcp_servers == {}

    @pytest.mark.asyncio
    async def test_multiple_servers_partial_failure(self):
        """One server hangs, one raises, one succeeds -- only failures pruned."""

        class _SucceedingMCP:
            client_session_timeout_seconds = 5

            async def connect(self):
                pass

            async def cleanup(self):
                pass

        mcp_servers = {
            "good": _SucceedingMCP(),
            "hanging": _HangingMCP(),
            "broken": _GenericErrorMCP(),
        }
        exit_stack = AsyncExitStack()

        await AgentWithMCP._connect_mcp_servers(exit_stack, mcp_servers)

        assert "good" in mcp_servers
        assert "hanging" not in mcp_servers
        assert "broken" not in mcp_servers


# ---------------------------------------------------------------------------
# get_architecture_from_context else branch (line 318)
# ---------------------------------------------------------------------------


class TestGetArchitectureFromContext:
    def test_returns_responder_thinker_for_non_resident_one_product(self):
        """When product does NOT contain 'resident_one_', return RESPONDER_THINKER."""
        ctx = _make_context(product="agent_leasing_applicant_chat")
        result = get_architecture_from_context(ctx)
        assert result == AgentArchitecture.RESPONDER_THINKER

    def test_returns_single_agent_for_resident_one_product(self):
        """Sanity check: 'resident_one_' in product returns SINGLE_AGENT."""
        ctx = _make_context(product="resident_one_chat")
        result = get_architecture_from_context(ctx)
        assert result == AgentArchitecture.SINGLE_AGENT


# ---------------------------------------------------------------------------
# log_internal_messages missing paths (lines 353, 356, 379-386)
# ---------------------------------------------------------------------------


class TestLogInternalMessages:
    def test_raises_value_error_when_no_new_items(self):
        """Line 353: ValueError when result has no new_items attribute."""
        result = object()  # plain object with no attributes
        with pytest.raises(ValueError, match="Needs to be of a class with new_items"):
            log_internal_messages(result)

    def test_guardrails_logged_when_present(self):
        """Lines 356, 379-386: _log_guardrail_internal_messages is called when
        both input_guardrail_results and output_guardrail_results exist."""

        # Build mock guardrail result entries
        input_gr = Mock()
        input_gr.guardrail.name = "pii_guardrail"
        input_gr.output.tripwire_triggered = True

        output_gr = Mock()
        output_gr.guardrail.name = "fair_housing_guardrail"
        output_gr.output.tripwire_triggered = False

        mock_result = Mock()
        mock_result.new_items = []  # has new_items (satisfies has_new_items)
        mock_result.input_guardrail_results = [input_gr]
        mock_result.output_guardrail_results = [output_gr]

        # Should not raise; exercises _log_guardrail_internal_messages
        log_internal_messages(mock_result)

    def test_guardrails_not_called_when_missing(self):
        """When result lacks guardrail attributes, _log_guardrail_internal_messages is skipped."""

        class _ResultWithoutGuardrails:
            new_items = []

        mock_result = _ResultWithoutGuardrails()
        assert not hasattr(mock_result, "input_guardrail_results")

        # Should not raise
        log_internal_messages(mock_result)

    def test_guardrails_with_all_triggered(self):
        """All guardrails triggered -- verifies list comprehension covers both lists."""
        ig1 = Mock()
        ig1.guardrail.name = "security"
        ig1.output.tripwire_triggered = True

        ig2 = Mock()
        ig2.guardrail.name = "prompt_injection"
        ig2.output.tripwire_triggered = True

        og1 = Mock()
        og1.guardrail.name = "competitor_blocking"
        og1.output.tripwire_triggered = True

        mock_result = Mock()
        mock_result.new_items = []
        mock_result.input_guardrail_results = [ig1, ig2]
        mock_result.output_guardrail_results = [og1]

        log_internal_messages(mock_result)


# ---------------------------------------------------------------------------
# get_channel_instructions file not found (lines 454-455)
# ---------------------------------------------------------------------------


class TestGetChannelInstructionsFileNotFound:
    def test_returns_fallback_when_channel_file_missing(self):
        """Line 455: logger.warning when channel file does not exist."""
        # Clear cache for an unknown channel
        CHANNEL_INSTRUCTIONS.pop("NONEXISTENT", None)

        ctx = _make_context(product="resident_one_chat")

        with patch("agent_leasing.agent.util.get_channel_from_context", return_value="NONEXISTENT"):
            channel, instructions = get_channel_instructions(ctx)

        assert channel == "NONEXISTENT"
        assert "Channel-specific instructions for NONEXISTENT are not available" in instructions
