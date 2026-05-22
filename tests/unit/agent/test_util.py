import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from cashews import cache
from mcp.types import CallToolResult, TextContent

from agent_leasing.agent.resident_one_agent.agent import ResidentAgent
from agent_leasing.agent.simple.agent import SimpleAgent
from agent_leasing.agent.util import (
    AgentWithMCP,
    UnsupportedAgentException,
    agent_selector,
    call_and_save_tool,
    extract_tool_result,
    is_disabled,
    is_enabled,
    log_internal_messages,
)
from agent_leasing.api.model import AskRequest, Persona, Product, ProductInfo, UCReference
from agent_leasing.clients.ldp import get_disabled_modules_with_pte
from agent_leasing.clients.mcp import CachingMCPServer
from agent_leasing.models.context import SessionScope
from agent_leasing.settings import settings


class _TestAgentWithMCPImpl(AgentWithMCP):
    async def _create_agent(self):
        return object()

    def agent(self):
        return self.agent_instance


@pytest.fixture
def session_context():
    req = AskRequest(
        product="agent_leasing_applicant_chat",
        request_id="test-request-id",
        chat_session_id="test-session-id",
        prompt="",
        product_info=ProductInfo(knock_property_id="123"),
    )
    ctx = SessionScope(
        ask_request=req,
    )
    return ctx


class TestSessionScope:
    def test_session_context_persona_prospect(self):
        req = AskRequest(
            product="simple",
            request_id="2",
            chat_session_id="def",
            prompt="",
            product_info=ProductInfo(knock_property_id="123"),
        )
        ctx = SessionScope(ask_request=req)
        assert ctx.persona == Persona.PROSPECT

    def test_session_context_persona_resident(self, ask_request_resident_chat_ll):
        ask_request_resident_chat_ll.product = Product.RESIDENT_ONE_CHAT.value
        ctx = SessionScope(ask_request=ask_request_resident_chat_ll)
        assert ctx.persona == Persona.RESIDENT

    def test_session_context_persona_resident_voice(self, ask_request_resident_chat_ll):
        ask_request_resident_chat_ll.product = Product.RESIDENT_ONE_VOICE.value
        ctx = SessionScope(ask_request=ask_request_resident_chat_ll)
        assert ctx.persona == Persona.RESIDENT

    def test_pending_activity_publishes_starts_empty(self):
        ctx = SessionScope()
        # Property exposes a fresh set per instance (no shared-default bug).
        assert ctx.pending_activity_publishes == set()
        other = SessionScope()
        ctx.pending_activity_publishes.add("sentinel")
        assert other.pending_activity_publishes == set()

    def test_pending_activity_publishes_excluded_from_cache_dump(self):
        ctx = SessionScope()
        ctx.pending_activity_publishes.add("sentinel")
        # Field(exclude=True) keeps asyncio.Task storage out of
        # `to_cache` / `model_dump` output.
        dumped = ctx.to_cache()
        assert "pending_activity_publishes" not in dumped

    def test_pending_activity_publishes_survives_model_copy_deep(self):
        # Empty set deep-copies cleanly; non-empty sets of asyncio.Task
        # remain caller's responsibility to clear before copy (deepcopy
        # would traverse them regardless of pydantic field settings).
        ctx = SessionScope()
        clone = ctx.model_copy(deep=True)
        assert clone.pending_activity_publishes == set()

    def test_frustrated_user_emitted_defaults_false(self):
        ctx = SessionScope()
        assert ctx.frustrated_user_emitted is False

    def test_frustrated_user_emitted_survives_cache_roundtrip(self):
        # Once-per-conversation gate must persist across messages so a
        # repeat-frustration turn after a Redis hydrate stays suppressed.
        ctx = SessionScope()
        ctx.frustrated_user_emitted = True
        restored = SessionScope.from_cache(ctx.to_cache())
        assert restored.frustrated_user_emitted is True

    def test_current_time_refreshed_on_cache_roundtrip(self):
        # `current_time` is injected into the LLM system prompt at every turn
        # (resident_one_agent/agent.py). Caching it would freeze the LLM at the
        # session-start timestamp and break time-aware reasoning on turn 2+.
        ctx = SessionScope()
        ctx.current_time = datetime.now(UTC) - timedelta(hours=1)
        before = datetime.now(UTC)
        restored = SessionScope.from_cache(ctx.to_cache())
        after = datetime.now(UTC)
        assert before <= restored.current_time <= after

    def test_langsmith_run_tree_excluded_from_cache_dump(self):
        ctx = SessionScope()
        ctx.langsmith_run_tree = {"trace": "header-from-prior-turn"}
        assert "langsmith_run_tree" not in ctx.to_cache()

    @pytest.mark.parametrize(
        "field,value",
        [
            ("handoff_in_progress", True),
            ("office_closed_warning_given", True),
            ("transfer_summary_requested", True),
            ("thinker_running", True),
            ("thinker_finished_at", 1.0),
            ("call_management_in_progress", True),
        ],
    )
    def test_transient_flags_excluded_from_cache_dump(self, field, value):
        # Restoring True from a crashed mid-turn write would block interrupt
        # suppression / fillers on the next session.
        ctx = SessionScope()
        setattr(ctx, field, value)
        assert field not in ctx.to_cache()


class TestIsDisabled:
    @pytest.mark.parametrize(
        "disabled_items,expected",
        [
            (None, False),
            ([], False),
            (["MR", "PACKAGES"], True),
            (["PACKAGES"], False),
        ],
    )
    def test_is_disabled_handles_none_and_membership(self, disabled_items, expected):
        assert is_disabled("MR", disabled_items) is expected


class TestIsEnabled:
    @pytest.mark.parametrize(
        "disabled_items,expected",
        [
            (None, True),
            ([], True),
            (["MR", "PACKAGES"], False),
            (["PACKAGES"], True),
        ],
    )
    def test_is_enabled_handles_none_and_membership(self, disabled_items, expected):
        assert is_enabled("MR", disabled_items) is expected


class TestAgentSelector:
    def test_resident_one(self, resident_context_chat_ll):
        agent_wth_mcp = agent_selector("simple", resident_context_chat_ll)
        assert isinstance(agent_wth_mcp, SimpleAgent)
        agent_wth_mcp = agent_selector("resident_one_chat", resident_context_chat_ll)
        assert isinstance(agent_wth_mcp, ResidentAgent)
        with pytest.raises(ValueError):
            agent_selector("nonsense", resident_context_chat_ll)


class TestCachingMCPServer:
    """Unit tests for CachingMCPServer call_tool method."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_empty_list_on_error(self):
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
        )

        with patch.object(
            CachingMCPServer.__bases__[0],
            "list_tools",
            new_callable=AsyncMock,
            side_effect=Exception("connection failed"),
        ):
            tools = await server.list_tools()
            assert tools == []

    @pytest.mark.asyncio
    async def test_list_tools_returns_empty_list_on_timeout(self):
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
        )

        with patch.object(
            CachingMCPServer.__bases__[0],
            "list_tools",
            new_callable=AsyncMock,
            side_effect=asyncio.TimeoutError(),
        ):
            tools = await server.list_tools()
            assert tools == []

    @pytest.mark.asyncio
    async def test_list_tools_propagates_cancelled_error(self):
        """CancelledError should propagate to prevent task leaks."""
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
        )

        with patch.object(
            CachingMCPServer.__bases__[0],
            "list_tools",
            new_callable=AsyncMock,
            side_effect=asyncio.CancelledError("Cancelled by cancel scope"),
        ):
            with pytest.raises(asyncio.CancelledError):
                await server.list_tools()

    @pytest.mark.asyncio
    async def test_list_tools_returns_tools_on_success(self):
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
        )

        fake_tools = [Mock(), Mock()]
        with patch.object(
            CachingMCPServer.__bases__[0],
            "list_tools",
            new_callable=AsyncMock,
            return_value=fake_tools,
        ):
            tools = await server.list_tools()
            assert tools == fake_tools


class TestAgentWithMCP:
    @pytest.mark.asyncio
    async def test_aenter_prunes_server_when_mcp_connect_cancelled(self, session_context):
        agent_wth_mcp = _TestAgentWithMCPImpl(session_context)
        mcp = AsyncMock()
        mcp.connect = AsyncMock(side_effect=asyncio.CancelledError("Cancelled by cancel scope"))
        agent_wth_mcp.mcp_servers = {"test": mcp}

        await agent_wth_mcp.__aenter__()
        assert agent_wth_mcp.mcp_servers == {}

    @pytest.mark.asyncio
    async def test_aenter_prunes_server_when_mcp_connect_times_out(self, session_context):
        agent_wth_mcp = _TestAgentWithMCPImpl(session_context)
        mcp = AsyncMock()
        mcp.connect = AsyncMock(side_effect=asyncio.TimeoutError())
        agent_wth_mcp.mcp_servers = {"test": mcp}

        await agent_wth_mcp.__aenter__()

        assert agent_wth_mcp.mcp_servers == {}

    @pytest.mark.asyncio
    async def test_invalidate_property_caches(self):
        cache.setup("mem://")
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            cacheable_tools=[],
        )
        ldp_key = "ldp_property_data:21521"
        await cache.set(ldp_key, "ldp_data")
        await server.invalidate_property_caches("21521")
        assert await cache.get(ldp_key) is None

    @pytest.mark.asyncio
    async def test_call_tool_cache_miss_then_hit(self, session_context):
        """
        Test that call_tool retrieves data from MCP server on cache miss,
        caches it, and then uses cache on subsequent calls.
        """
        # Mock response from the MCP server
        mock_mcp_response = CallToolResult(
            content=[TextContent(text="Property data from MCP server", type="text")],
            structuredContent=None,
        )

        # Create server instance with cached tools
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            cacheable_tools=["get_rent_information"],
            context=session_context,
        )

        with (
            patch("agent_leasing.clients.mcp.cache") as mock_cache,
        ):
            # Mock cache.get to return None (cache miss) first, then cached data
            mock_cache.get = AsyncMock(side_effect=[None, "Property data from MCP server"])
            mock_cache.set = AsyncMock()

            # Mock the parent class's call_tool method
            with patch.object(
                CachingMCPServer.__bases__[0],
                "call_tool",
                new_callable=AsyncMock,
                return_value=mock_mcp_response,
            ) as mock_parent_call_tool:
                # First call - should hit MCP server and cache result
                result1 = await server.call_tool(
                    "get_rent_information",
                    {"property_id": 1, "renter_type": "applicant"},
                )

                # Verify cache.get was called to check for cached data
                mock_cache.get.assert_called_with(
                    'get_rent_information:{"property_id": 1, "renter_type": "applicant"}',
                    default=None,
                )

                # Verify parent's call_tool was called (cache miss)
                mock_parent_call_tool.assert_called_once_with(
                    "get_rent_information",
                    {"property_id": 1, "renter_type": "applicant"},
                )

                # Verify result content
                assert result1.content[0].text == "Property data from MCP server"

                # Reset mocks for second call
                mock_cache.get.reset_mock()
                mock_cache.set.reset_mock()
                mock_parent_call_tool.reset_mock()

                # Second call - should use cache
                result2 = await server.call_tool(
                    "get_rent_information",
                    {"property_id": 1, "renter_type": "applicant"},
                )

                # Verify cache.get was called again
                mock_cache.get.assert_called_with(
                    'get_rent_information:{"property_id": 1, "renter_type": "applicant"}',
                    default=None,
                )

                # Verify parent's call_tool was NOT called (cache hit)
                mock_parent_call_tool.assert_not_called()

                # Verify cache.set was NOT called (data already cached)
                mock_cache.set.assert_not_called()

                # Verify result content from cache
                assert result2.content[0].text == "Property data from MCP server"
                assert result1.content[0].text == result2.content[0].text

    @pytest.mark.asyncio
    async def test_call_tool_non_cached_tool(self, session_context):
        """
        Test that call_tool bypasses cache for tools not in cacheable_tools list.
        """
        # Mock response from the MCP server
        mock_mcp_response = CallToolResult(
            content=[TextContent(text="Non-cached tool response", type="text")],
            structuredContent=None,
        )

        # Create server instance with cached tools (not including the tool we'll call)
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            cacheable_tools=["get_rent_information"],  # different tool
            context=session_context,
        )

        with patch("agent_leasing.clients.mcp.cache") as mock_cache:
            mock_cache.get = AsyncMock()
            mock_cache.set = AsyncMock()

            # Mock the parent class's call_tool method
            with patch.object(
                CachingMCPServer.__bases__[0],
                "call_tool",
                new_callable=AsyncMock,
                return_value=mock_mcp_response,
            ) as mock_parent_call_tool:
                # Call a tool that's not in cacheable_tools
                result = await server.call_tool("get_availability", {"property_id": 1})

                # Verify cache was not accessed
                mock_cache.get.assert_not_called()
                mock_cache.set.assert_not_called()

                # Verify parent's call_tool was called directly
                mock_parent_call_tool.assert_called_once_with("get_availability", {"property_id": 1})

                # Verify result content
                assert result.content[0].text == "Non-cached tool response"

    @pytest.mark.asyncio
    async def test_call_tool_with_none_arguments(self, session_context):
        """
        Test that call_tool handles None arguments without AttributeError.
        """
        # Mock response from the MCP server
        mock_mcp_response = CallToolResult(
            content=[TextContent(text="Tool response with no args", type="text")],
            structuredContent=None,
        )

        # Create server instance with cached tools
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            cacheable_tools=["list_tools"],
            context=session_context,
        )

        with patch("agent_leasing.clients.mcp.cache") as mock_cache:
            mock_cache.get = AsyncMock(return_value=None)
            mock_cache.set = AsyncMock()

            # Mock the parent class's call_tool method
            with patch.object(
                CachingMCPServer.__bases__[0],
                "call_tool",
                new_callable=AsyncMock,
                return_value=mock_mcp_response,
            ) as mock_parent_call_tool:
                # Call tool with None arguments - should not raise AttributeError
                result = await server.call_tool("list_tools", None)

                # Verify cache.get was called with proper cache key for None arguments
                mock_cache.get.assert_called_with("list_tools:{}", default=None)

                # Verify parent's call_tool was called once
                mock_parent_call_tool.assert_called_once_with("list_tools", None)

                # Verify result content
                assert result.content[0].text == "Tool response with no args"

    @pytest.mark.asyncio
    async def test_call_tool_with_special_characters_in_arguments(self, session_context):
        """
        Test that call_tool properly handles arguments with special characters (colons).
        """
        # Mock response from the MCP server
        mock_mcp_response = CallToolResult(
            content=[TextContent(text="Response for special chars", type="text")],
            structuredContent=None,
        )

        # Create server instance with cached tools
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            cacheable_tools=["search_tool"],
            context=session_context,
        )

        with (
            patch("agent_leasing.clients.mcp.cache") as mock_cache,
        ):
            mock_cache.get = AsyncMock(return_value=None)
            mock_cache.set = AsyncMock()

            # Mock the parent class's call_tool method
            with patch.object(
                CachingMCPServer.__bases__[0],
                "call_tool",
                new_callable=AsyncMock,
                return_value=mock_mcp_response,
            ) as mock_parent_call_tool:
                # Call tool with arguments containing colons
                args = {"query": "search:term:with:colons", "filter": "type:test"}
                result = await server.call_tool("search_tool", args)

                # Verify cache.get was called with JSON-serialized cache key
                # This ensures no collision due to colons in values
                expected_cache_key = 'search_tool:{"filter": "type:test", "query": "search:term:with:colons"}'
                mock_cache.get.assert_called_with(expected_cache_key, default=None)

                # Verify parent's call_tool was called once
                mock_parent_call_tool.assert_called_once_with("search_tool", args)

                # Verify result content
                assert result.content[0].text == "Response for special chars"

    @pytest.mark.asyncio
    async def test_call_tool_no_duplicate_calls_on_empty_content(self, session_context):
        """
        Test that call_tool doesn't make duplicate calls when content is empty.
        """
        # Mock response with empty content
        mock_empty_response = CallToolResult(
            content=[],
            structuredContent=None,
        )

        # Create server instance with cached tools
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            cacheable_tools=["empty_tool"],
            context=session_context,
        )

        with (
            patch("agent_leasing.clients.mcp.cache") as mock_cache,
        ):
            mock_cache.get = AsyncMock(return_value=None)
            mock_cache.set = AsyncMock()

            # Mock the parent class's call_tool method
            with patch.object(
                CachingMCPServer.__bases__[0],
                "call_tool",
                new_callable=AsyncMock,
                return_value=mock_empty_response,
            ) as mock_parent_call_tool:
                # Call tool that returns empty content
                result = await server.call_tool("empty_tool", {"param": "value"})

                # Verify parent's call_tool was called exactly once (not twice)
                mock_parent_call_tool.assert_called_once_with("empty_tool", {"param": "value"})

                # Verify cache.set was NOT called (no content to cache)
                mock_cache.set.assert_not_called()

                # Verify we got the empty response back
                assert len(result.content) == 0

    @pytest.mark.asyncio
    async def test_call_tool_with_auth_function_success(self, session_context):
        """
        Test that call_tool calls auth_function and sets Authorization header before MCP call.
        """
        # Mock response from the MCP server
        mock_mcp_response = CallToolResult(
            content=[TextContent(text="Authenticated response", type="text")],
            structuredContent=None,
        )

        # Mock auth function that returns a token
        mock_auth_function = AsyncMock(return_value="test-token-123")

        # Create server instance with auth_function
        server = CachingMCPServer(
            params={"url": "http://mock-server", "headers": {}},
            auth_function=mock_auth_function,
            context=session_context,
        )

        # Mock the parent class's call_tool method
        with patch.object(
            CachingMCPServer.__bases__[0],
            "call_tool",
            new_callable=AsyncMock,
            return_value=mock_mcp_response,
        ) as mock_parent_call_tool:
            # Call a tool
            result = await server.call_tool("create_service_request", {"type": "maintenance"})

            # Verify auth function was called
            mock_auth_function.assert_called_once()

            # Verify Authorization header was set in params
            assert server.params["headers"]["Authorization"] == "Bearer test-token-123"

            # Verify parent's call_tool was called
            mock_parent_call_tool.assert_called_once_with("create_service_request", {"type": "maintenance"})

            # Verify result content
            assert result.content[0].text == "Authenticated response"

    @pytest.mark.asyncio
    async def test_call_tool_with_auth_function_none_token(self, session_context):
        """
        Test that call_tool handles auth_function returning None gracefully.
        """
        # Mock response from the MCP server
        mock_mcp_response = CallToolResult(
            content=[TextContent(text="Response without auth", type="text")],
            structuredContent=None,
        )

        # Mock auth function that returns None
        mock_auth_function = AsyncMock(return_value=None)

        # Create server instance with auth_function
        server = CachingMCPServer(
            params={"url": "http://mock-server", "headers": {}},
            auth_function=mock_auth_function,
            context=session_context,
        )

        # Mock the parent class's call_tool method
        with patch.object(
            CachingMCPServer.__bases__[0],
            "call_tool",
            new_callable=AsyncMock,
            return_value=mock_mcp_response,
        ) as mock_parent_call_tool:
            # Call a tool
            result = await server.call_tool("get_active_service_requests", {})

            # Verify auth function was called
            mock_auth_function.assert_called_once()

            # Verify Authorization header was NOT set (token was None)
            assert "Authorization" not in server.params["headers"]

            # Verify parent's call_tool was called
            mock_parent_call_tool.assert_called_once_with("get_active_service_requests", {})

            # Verify result content
            assert result.content[0].text == "Response without auth"

    @pytest.mark.asyncio
    async def test_call_tool_without_auth_function(self, session_context):
        """
        Test that call_tool works normally when auth_function is None.
        """
        # Mock response from the MCP server
        mock_mcp_response = CallToolResult(
            content=[TextContent(text="Normal response", type="text")],
            structuredContent=None,
        )

        # Create server instance without auth_function
        server = CachingMCPServer(
            params={"url": "http://mock-server", "headers": {}},
            auth_function=None,
            context=session_context,
        )

        # Mock the parent class's call_tool method
        with patch.object(
            CachingMCPServer.__bases__[0],
            "call_tool",
            new_callable=AsyncMock,
            return_value=mock_mcp_response,
        ) as mock_parent_call_tool:
            # Call a tool
            result = await server.call_tool("get_property_info", {"id": "123"})

            # Verify parent's call_tool was called directly
            mock_parent_call_tool.assert_called_once_with("get_property_info", {"id": "123"})

            # Verify result content
            assert result.content[0].text == "Normal response"

    @pytest.mark.asyncio
    async def test_call_tool_with_auth_function_and_caching(self, session_context):
        """
        Test that auth_function works correctly with cached tools.
        """
        # Mock response from the MCP server
        mock_mcp_response = CallToolResult(
            content=[TextContent(text="Cached authenticated response", type="text")],
            structuredContent=None,
        )

        # Mock auth function that returns a token
        mock_auth_function = AsyncMock(return_value="cached-token-456")

        # Create server instance with auth_function and cached tools
        server = CachingMCPServer(
            params={"url": "http://mock-server", "headers": {}},
            auth_function=mock_auth_function,
            cacheable_tools=["get_rent_information"],
            context=session_context,
        )

        with patch("agent_leasing.clients.mcp.cache") as mock_cache:
            # Mock cache miss
            mock_cache.get = AsyncMock(return_value=None)
            mock_cache.set = AsyncMock()

            # Mock the parent class's call_tool method
            with patch.object(
                CachingMCPServer.__bases__[0],
                "call_tool",
                new_callable=AsyncMock,
                return_value=mock_mcp_response,
            ) as mock_parent_call_tool:
                # Call a cached tool
                result = await server.call_tool("get_rent_information", {"property_id": "456"})

                # Verify auth function was called
                mock_auth_function.assert_called_once()

                # Verify Authorization header was set
                assert server.params["headers"]["Authorization"] == "Bearer cached-token-456"

                # Verify cache was checked
                mock_cache.get.assert_called_once()

                # Verify parent's call_tool was called
                mock_parent_call_tool.assert_called_once_with("get_rent_information", {"property_id": "456"})

                # Verify result was cached
                mock_cache.set.assert_called_once()

                # Verify result content
                assert result.content[0].text == "Cached authenticated response"

    @pytest.mark.asyncio
    async def test_call_tool_auth_function_called_every_time(self, session_context):
        """
        Test that auth_function is called on every tool invocation to get fresh tokens.
        """
        # Mock responses from the MCP server
        mock_mcp_response1 = CallToolResult(
            content=[TextContent(text="First response", type="text")],
            structuredContent=None,
        )
        mock_mcp_response2 = CallToolResult(
            content=[TextContent(text="Second response", type="text")],
            structuredContent=None,
        )

        # Mock auth function that returns different tokens
        mock_auth_function = AsyncMock(side_effect=["token-1", "token-2"])

        # Create server instance with auth_function
        server = CachingMCPServer(
            params={"url": "http://mock-server", "headers": {}},
            auth_function=mock_auth_function,
            context=session_context,
        )

        # Mock the parent class's call_tool method
        with patch.object(
            CachingMCPServer.__bases__[0],
            "call_tool",
            new_callable=AsyncMock,
            side_effect=[mock_mcp_response1, mock_mcp_response2],
        ) as mock_parent_call_tool:
            # First call
            result1 = await server.call_tool("create_service_request", {"type": "plumbing"})

            # Verify first token was set
            assert server.params["headers"]["Authorization"] == "Bearer token-1"
            assert result1.content[0].text == "First response"

            # Second call
            result2 = await server.call_tool("create_service_request", {"type": "electrical"})

            # Verify second token was set (fresh token retrieved)
            assert server.params["headers"]["Authorization"] == "Bearer token-2"
            assert result2.content[0].text == "Second response"

            # Verify auth function was called twice
            assert mock_auth_function.call_count == 2

            # Verify parent's call_tool was called twice
            assert mock_parent_call_tool.call_count == 2

    @pytest.mark.asyncio
    async def test_is_auth_failure_detection(self):
        """Test _is_auth_failure method with various exception types."""
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            auth_function=AsyncMock(return_value="test-token"),
        )

        # Test HTTP 401 error
        mock_401_exception = Exception("401 Unauthorized")
        mock_401_exception.response = type("obj", (object,), {"status_code": 401})
        assert server._is_auth_failure(mock_401_exception) is True

        # Test HTTPStatusError with 401
        mock_http_status_error = Exception("HTTPStatusError: 401 Client Error")
        assert server._is_auth_failure(mock_http_status_error) is True

        # Test unauthorized message
        mock_unauthorized = Exception("unauthorized access")
        assert server._is_auth_failure(mock_unauthorized) is True

        # Test token expired message
        mock_expired = Exception("token expired")
        assert server._is_auth_failure(mock_expired) is True

        # Test invalid token message
        mock_invalid = Exception("invalid token")
        assert server._is_auth_failure(mock_invalid) is True

        # Test authentication failed message
        mock_auth_failed = Exception("authentication failed")
        assert server._is_auth_failure(mock_auth_failed) is True

        # Test non-auth error
        mock_other_error = Exception("Connection timeout")
        assert server._is_auth_failure(mock_other_error) is False

    @pytest.mark.asyncio
    async def test_handle_connection_failure_and_retry_success(self):
        """Test successful connection failure recovery."""
        mock_auth_function = AsyncMock(return_value="fresh-token")
        server = CachingMCPServer(
            params={"url": "http://mock-server", "headers": {}},
            auth_function=mock_auth_function,
        )

        # Mock successful retry response
        mock_retry_response = CallToolResult(
            content=[TextContent(text="Success after retry", type="text")],
            structuredContent=None,
        )

        with patch.object(server, "cleanup", new_callable=AsyncMock) as mock_cleanup:
            with patch.object(server, "connect", new_callable=AsyncMock) as mock_connect:
                with patch.object(
                    CachingMCPServer.__bases__[0],
                    "call_tool",
                    new_callable=AsyncMock,
                    return_value=mock_retry_response,
                ) as mock_parent_call_tool:
                    result = await server._handle_connection_failure_and_retry("test_tool", {"param": "value"})

                    # Verify cleanup was called
                    mock_cleanup.assert_called_once()

                    # Verify fresh token was set
                    assert server.params["headers"]["Authorization"] == "Bearer fresh-token"

                    # Verify reconnect was called
                    mock_connect.assert_called_once()

                    # Verify retry call was made
                    mock_parent_call_tool.assert_called_once_with("test_tool", {"param": "value"})

                    assert result == mock_retry_response

    @pytest.mark.asyncio
    async def test_handle_connection_failure_no_auth_function(self):
        """Test connection failure handling when no auth_function - still reconnects without token refresh."""
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            auth_function=None,
        )
        mock_retry_response = CallToolResult(
            content=[TextContent(text="Success after retry", type="text")],
            structuredContent=None,
        )

        with patch.object(server, "cleanup", new_callable=AsyncMock) as mock_cleanup:
            with patch.object(server, "connect", new_callable=AsyncMock) as mock_connect:
                with patch.object(
                    CachingMCPServer.__bases__[0],
                    "call_tool",
                    new_callable=AsyncMock,
                    return_value=mock_retry_response,
                ):
                    result = await server._handle_connection_failure_and_retry("test_tool", {})

                    mock_cleanup.assert_called_once()
                    mock_connect.assert_called_once()
                    assert result == mock_retry_response

    @pytest.mark.asyncio
    async def test_handle_connection_failure_auth_function_returns_none(self):
        """Test connection failure handling continues even when auth_function returns None."""
        mock_auth_function = AsyncMock(return_value=None)
        server = CachingMCPServer(params={"url": "http://mock-server"}, auth_function=mock_auth_function)
        mock_retry_response = CallToolResult(
            content=[TextContent(text="Success after retry", type="text")],
            structuredContent=None,
        )

        with patch.object(server, "cleanup", new_callable=AsyncMock) as mock_cleanup:
            with patch.object(server, "connect", new_callable=AsyncMock) as mock_connect:
                with patch.object(
                    CachingMCPServer.__bases__[0],
                    "call_tool",
                    new_callable=AsyncMock,
                    return_value=mock_retry_response,
                ):
                    result = await server._handle_connection_failure_and_retry("test_tool", {})

                    mock_cleanup.assert_called_once()
                    mock_connect.assert_called_once()
                    assert result == mock_retry_response
                    assert "Authorization" not in server.params["headers"]

    @pytest.mark.asyncio
    async def test_handle_connection_failure_recovery_exception(self):
        """Test connection failure handling when recovery itself fails."""
        mock_auth_function = AsyncMock(side_effect=Exception("Auth service down"))
        server = CachingMCPServer(params={"url": "http://mock-server"}, auth_function=mock_auth_function)

        with patch.object(server, "cleanup", new_callable=AsyncMock):
            with pytest.raises(Exception, match="Reconnection failed.*Auth service down"):
                await server._handle_connection_failure_and_retry("test_tool", {})

    @pytest.mark.asyncio
    async def test_run_mcp_tool_with_auth_failure_and_recovery(self):
        """Test _run_mcp_tool with auth failure that gets recovered."""
        mock_auth_function = AsyncMock(return_value="fresh-token")
        server = CachingMCPServer(
            params={"url": "http://mock-server", "headers": {}},
            auth_function=mock_auth_function,
        )

        # Mock auth failure on first call, success on retry
        auth_error = Exception("401 Unauthorized")
        auth_error.response = type("obj", (object,), {"status_code": 401})

        success_response = CallToolResult(
            content=[TextContent(text="Success after recovery", type="text")],
            structuredContent=None,
        )

        with patch.object(
            CachingMCPServer.__bases__[0],
            "call_tool",
            new_callable=AsyncMock,
            side_effect=[auth_error, success_response],
        ) as mock_parent_call_tool:  # noqa: F841
            with patch.object(
                server,
                "_handle_connection_failure_and_retry",
                new_callable=AsyncMock,
                return_value=success_response,
            ) as mock_handle_connection_failure:
                result = await server._run_mcp_tool("test_tool", {"param": "value"})

                # Verify connection failure handler was called
                mock_handle_connection_failure.assert_called_once_with("test_tool", {"param": "value"})

                assert result == success_response

    @pytest.mark.asyncio
    async def test_run_mcp_tool_with_non_auth_exception(self):
        """Test _run_mcp_tool with non-auth exception that gets re-raised."""
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            auth_function=AsyncMock(return_value="token"),
        )

        # Mock non-auth error
        network_error = Exception("Connection timeout")

        with patch.object(
            CachingMCPServer.__bases__[0],
            "call_tool",
            new_callable=AsyncMock,
            side_effect=network_error,
        ):
            with pytest.raises(Exception, match="Connection timeout"):
                await server._run_mcp_tool("test_tool", {})

    @pytest.mark.asyncio
    async def test_connect_with_auth_function_exception(self):
        """Test connect method when auth_function raises exception."""
        mock_auth_function = AsyncMock(side_effect=Exception("Auth service unavailable"))
        server = CachingMCPServer(
            params={"url": "http://mock-server", "headers": {}},
            auth_function=mock_auth_function,
        )

        with pytest.raises(Exception, match="Auth service unavailable"):
            await server.connect()

    @pytest.mark.asyncio
    async def test_connect_with_auth_function_returns_none(self):
        """Test connect method when auth_function returns None."""
        mock_auth_function = AsyncMock(return_value=None)
        server = CachingMCPServer(
            params={"url": "http://mock-server", "headers": {}},
            auth_function=mock_auth_function,
        )

        with patch.object(CachingMCPServer.__bases__[0], "connect", new_callable=AsyncMock) as mock_parent_connect:
            await server.connect()

            # Should still call parent connect even if no token
            mock_parent_connect.assert_called_once()

            # Authorization header should not be set
            assert "Authorization" not in server.params["headers"]

    @pytest.mark.asyncio
    async def test_call_tool_cached_with_json_decode_error(self, session_context):
        """Test call_tool with cached data that has JSON decode error."""
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            cacheable_tools=["test_tool"],
            context=session_context,
        )

        with patch("agent_leasing.clients.mcp.cache") as mock_cache:
            # Mock cache returning invalid JSON
            mock_cache.get = AsyncMock(return_value="invalid json {")

            # Should return cached text even if JSON parsing fails
            result = await server.call_tool("test_tool", {})

            assert result.content[0].text == "invalid json {"
            assert result.structuredContent is None

    @pytest.mark.asyncio
    async def test_call_tool_cached_with_empty_content(self, session_context):
        """Test call_tool caching behavior when MCP returns empty content."""
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            cacheable_tools=["test_tool"],
            context=session_context,
        )

        # Mock MCP response with empty content
        mock_empty_response = CallToolResult(
            content=[],  # Empty content
            structuredContent=None,
        )

        with patch("agent_leasing.clients.mcp.cache") as mock_cache:
            mock_cache.get = AsyncMock(return_value=None)  # Cache miss
            mock_cache.set = AsyncMock()

            with patch.object(
                server,
                "_run_mcp_tool",
                new_callable=AsyncMock,
                return_value=mock_empty_response,
            ) as mock_run_mcp_tool:  # noqa: F841
                result = await server.call_tool("test_tool", {})

                # Should not attempt to cache empty content
                mock_cache.set.assert_not_called()

                assert result == mock_empty_response

    @pytest.mark.asyncio
    async def test_call_tool_does_not_cache_on_exception(self, session_context):
        """Test that exceptions from _run_mcp_tool are not cached."""
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            cacheable_tools=["get_rent_information"],
            context=session_context,
        )

        with patch("agent_leasing.clients.mcp.cache") as mock_cache:
            mock_cache.get = AsyncMock(return_value=None)  # Cache miss
            mock_cache.set = AsyncMock()

            with patch.object(
                server,
                "_run_mcp_tool",
                new_callable=AsyncMock,
                side_effect=Exception("Connection timed out"),
            ):
                # call_tool catches the exception and returns an error result
                result = await server.call_tool("get_rent_information", {"property_id": 123})

                # Verify cache.set was NOT called (error should not be cached)
                mock_cache.set.assert_not_called()

                # Verify we got an error result
                assert result.isError is True
                assert "TOOL_ERROR" in result.content[0].text

    @pytest.mark.asyncio
    async def test_call_tool_does_not_cache_error_response(self, session_context):
        """Test that MCP responses with isError=True are not cached."""
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            cacheable_tools=["get_rent_information"],
            context=session_context,
        )

        # Mock MCP response with isError=True
        mock_error_response = CallToolResult(
            content=[TextContent(text="Error from MCP server", type="text")],
            structuredContent=None,
            isError=True,
        )

        with patch("agent_leasing.clients.mcp.cache") as mock_cache:
            mock_cache.get = AsyncMock(return_value=None)  # Cache miss
            mock_cache.set = AsyncMock()

            with patch.object(
                server,
                "_run_mcp_tool",
                new_callable=AsyncMock,
                return_value=mock_error_response,
            ):
                result = await server.call_tool("get_rent_information", {"property_id": 123})

                # Verify cache.set was NOT called (error response should not be cached)
                mock_cache.set.assert_not_called()

                # Verify we got the error response back
                assert result.isError is True
                assert result.content[0].text == "Error from MCP server"

    @pytest.mark.asyncio
    async def test_call_tool_caches_successful_response_only(self, session_context):
        """Test that only successful responses (isError=False) are cached."""
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            cacheable_tools=["get_rent_information"],
            context=session_context,
        )

        # Mock successful MCP response
        mock_success_response = CallToolResult(
            content=[TextContent(text="Success data", type="text")],
            structuredContent=None,
            isError=False,
        )

        with patch("agent_leasing.clients.mcp.cache") as mock_cache:
            mock_cache.get = AsyncMock(return_value=None)  # Cache miss
            mock_cache.set = AsyncMock()

            with patch.object(
                server,
                "_run_mcp_tool",
                new_callable=AsyncMock,
                return_value=mock_success_response,
            ):
                result = await server.call_tool("get_rent_information", {"property_id": 123})

                # Verify cache.set WAS called for successful response
                mock_cache.set.assert_called_once()

                # Verify we got the success response back
                assert result.isError is False
                assert result.content[0].text == "Success data"

    @pytest.mark.asyncio
    async def test_call_tool_timeout_error_returns_graceful_result(self, session_context):
        """Test that timeout errors are caught and returned as tool results."""
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            context=session_context,
        )

        with patch.object(
            server,
            "_call_tool_impl",
            new_callable=AsyncMock,
            side_effect=Exception("Connection timed out"),
        ):
            result = await server.call_tool("get_property_info", {"id": "123"})

            # Should return a CallToolResult with isError=True
            assert result.isError is True
            assert "TOOL_ERROR" in result.content[0].text
            assert "timed out" in result.content[0].text
            assert "get_property_info" in result.content[0].text

    @pytest.mark.asyncio
    async def test_call_tool_connection_error_returns_graceful_result(self, session_context):
        """Test that connection errors are caught and returned as tool results."""
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            context=session_context,
        )

        with patch.object(
            server,
            "_call_tool_impl",
            new_callable=AsyncMock,
            side_effect=Exception("Failed to connect to server"),
        ):
            result = await server.call_tool("get_availability", {})

            assert result.isError is True
            assert "TOOL_ERROR" in result.content[0].text
            assert "connect" in result.content[0].text.lower()
            assert "get_availability" in result.content[0].text

    @pytest.mark.asyncio
    async def test_call_tool_generic_error_returns_graceful_result(self, session_context):
        """Test that generic errors are caught and returned as tool results."""
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            context=session_context,
        )

        with patch.object(
            server,
            "_call_tool_impl",
            new_callable=AsyncMock,
            side_effect=Exception("Some unexpected error"),
        ):
            result = await server.call_tool("create_service_request", {"type": "plumbing"})

            assert result.isError is True
            assert "TOOL_ERROR" in result.content[0].text
            assert "create_service_request" in result.content[0].text
            assert "encountered an error" in result.content[0].text

    def test_format_tool_error_timeout(self, session_context):
        """Test _format_tool_error for timeout errors."""
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            context=session_context,
        )

        error = Exception("Request timed out after 30 seconds")
        message = server._format_tool_error("test_tool", error)

        assert "TOOL_ERROR" in message
        assert "timed out" in message
        assert "test_tool" in message

    def test_format_tool_error_connection(self, session_context):
        """Test _format_tool_error for connection errors."""
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            context=session_context,
        )

        error = Exception("Unable to connect to remote host")
        message = server._format_tool_error("test_tool", error)

        assert "TOOL_ERROR" in message
        assert "connect" in message.lower()
        assert "test_tool" in message

    def test_format_tool_error_generic(self, session_context):
        """Test _format_tool_error for generic errors."""
        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            context=session_context,
        )

        error = Exception("Something went wrong")
        message = server._format_tool_error("test_tool", error)

        assert "TOOL_ERROR" in message
        assert "encountered an error" in message
        assert "test_tool" in message


# Arrange: Create post processors that append to the text
def first_processor(result: CallToolResult, **kwargs) -> CallToolResult:
    result.content[0].text = result.content[0].text + "_modified_once"
    return result


def second_processor(result: CallToolResult, **kwargs) -> CallToolResult:
    result.content[0].text = result.content[0].text + "_modified_twice"
    return result


def third_processor(result: CallToolResult, **kwargs) -> CallToolResult:
    result.content[0].text = result.content[0].text + "_modified_thrice"
    return result


def failing_processor(result: CallToolResult, **kwargs) -> CallToolResult:
    raise Exception("Failed to process result")


class TestCachingMCPServerPostProcessing:
    # fmt: off
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "processors, cached, expected_text",
        [
            ([], False, ""),
            ([first_processor], False, "_modified_once"),
            ([first_processor, second_processor], False, "_modified_once_modified_twice"),
            ([first_processor, second_processor, third_processor], False, "_modified_once_modified_twice_modified_thrice"),
            ([failing_processor], False, ""),  # Fails immediately, returns original
            ([first_processor, failing_processor, third_processor], False, "_modified_once_modified_thrice"),  # Fails after first, returns partial
            ([], True, ""),
            ([first_processor], True, "_modified_once"),
            ([first_processor, second_processor], True, "_modified_once_modified_twice"),
            ([first_processor, second_processor, third_processor], True, "_modified_once_modified_twice_modified_thrice"),
            ([failing_processor], True, ""),  # Fails immediately, returns original
            ([first_processor, failing_processor, third_processor], True, "_modified_once_modified_thrice"),  # Fails after first, returns partial
        ],
    )
    # fmt: on
    async def test_apply_post_processing_with_multiple_processors(
        self, session_context, processors, cached, expected_text
    ):
        """Test that multiple post processors are applied in sequence."""

        server = CachingMCPServer(
            params={"url": "http://mock-server"},
            cacheable_tools=["test_tool"] if cached else [],
            context=session_context,
            tool_post_processors={"test_tool": processors},
        )

        if cached:
            # Test cached path
            with patch("agent_leasing.clients.mcp.cache") as mock_cache:
                mock_cache.get = AsyncMock(return_value="original")
                # Act
                result = await server.call_tool("test_tool", {})
        else:
            # Test non-cached path

            # Mock MCP response
            mock_response = CallToolResult(
                content=[TextContent(text="original", type="text")],
                structuredContent=None,
            )

            with patch.object(
                server,
                "_run_mcp_tool",
                new_callable=AsyncMock,
                return_value=mock_response,
            ):
                # Act
                result = await server.call_tool("test_tool", {})

        # Assert
        assert result.content[0].text == "original" + expected_text


class TestUtilityFunctions:
    """Test cases for utility functions in agent_util."""

    def test_log_internal_messages_with_message_output(self):
        """Test log_internal_messages with MessageOutputItem."""
        from agents import Agent, MessageOutputItem

        mock_agent = Agent(name="test-agent")
        mock_message_item = MessageOutputItem(
            agent=mock_agent,
            raw_item=type("obj", (object,), {"content": "test message"}),
        )

        mock_result = type("obj", (object,), {"new_items": [mock_message_item]})

        # Should not raise exception
        log_internal_messages(mock_result)

    def test_log_internal_messages_with_handoff_output(self):
        """Test log_internal_messages with HandoffOutputItem."""
        from agents import Agent, HandoffOutputItem

        mock_source_agent = Agent(name="source-agent")
        mock_target_agent = Agent(name="target-agent")

        mock_handoff_item = HandoffOutputItem(
            agent=mock_source_agent,
            source_agent=mock_source_agent,
            target_agent=mock_target_agent,
            raw_item=type("obj", (object,), {}),
        )

        mock_result = type("obj", (object,), {"new_items": [mock_handoff_item]})

        # Should not raise exception
        log_internal_messages(mock_result)

    def test_log_internal_messages_with_tool_call_item(self):
        """Test log_internal_messages with ToolCallItem."""
        from agents import Agent, ToolCallItem

        mock_agent = Agent(name="test-agent")
        mock_tool_call_item = ToolCallItem(agent=mock_agent, raw_item=type("obj", (object,), {"name": "test_tool"}))

        mock_result = type("obj", (object,), {"new_items": [mock_tool_call_item]})

        # Should not raise exception
        log_internal_messages(mock_result)

    def test_log_internal_messages_with_tool_call_output_item(self):
        """Test log_internal_messages with ToolCallOutputItem."""
        from agents import Agent, ToolCallOutputItem

        mock_agent = Agent(name="test-agent")
        mock_tool_output_item = ToolCallOutputItem(
            agent=mock_agent, output="tool output", raw_item=type("obj", (object,), {})
        )

        mock_result = type("obj", (object,), {"new_items": [mock_tool_output_item]})

        # Should not raise exception
        log_internal_messages(mock_result)

    def test_log_internal_messages_with_unknown_item(self):
        """Test log_internal_messages with unknown item type."""
        from agents import Agent

        mock_agent = Agent(name="test-agent")

        # Create a mock item that doesn't match known types
        class UnknownItem:
            def __init__(self):
                self.agent = mock_agent

        mock_unknown_item = UnknownItem()

        mock_result = type("obj", (object,), {"new_items": [mock_unknown_item]})

        # Should not raise exception
        log_internal_messages(mock_result)

    def test_extract_tool_result_with_structured_content_result(self):
        """Test extract_tool_result with structured content containing 'result' key."""
        result = CallToolResult(
            content=[TextContent(text="fallback text", type="text")],
            structuredContent={"result": {"data": "structured data"}},
        )

        extracted = extract_tool_result(result)
        assert extracted == {"data": "structured data"}

    def test_extract_tool_result_with_structured_content_no_result(self):
        """Test extract_tool_result with structured content without 'result' key."""
        result = CallToolResult(
            content=[TextContent(text="fallback text", type="text")],
            structuredContent={"data": "direct structured data"},
        )

        extracted = extract_tool_result(result)
        assert extracted == {"data": "direct structured data"}

    def test_extract_tool_result_with_text_content(self):
        """Test extract_tool_result with text content only."""
        result = CallToolResult(
            content=[TextContent(text="text response", type="text")],
            structuredContent=None,
        )

        extracted = extract_tool_result(result)
        assert extracted == "text response"

    def test_extract_tool_result_with_empty_content(self):
        """Test extract_tool_result with empty content."""
        result = CallToolResult(content=[], structuredContent=None)

        extracted = extract_tool_result(result)
        assert extracted is None


class TestAgentSelectorExtended:
    """Extended test cases for agent_selector function."""

    @pytest.mark.skip(reason="Skipping due having a temporary dual mapping for one agent.")
    def test_agent_selector_all_product_types(self, ask_request_simple):
        """Test agent_selector with all supported product types."""
        # Test all the product types from the agent_selector function
        test_cases = [
            (Product.SIMPLE.value, "SimpleAgent"),
            (Product.RESIDENT_ONE_CHAT.value, "ResidentAgent"),
            (Product.RESIDENT_ONE_EMAIL.value, "ResidentAgent"),
            (Product.RESIDENT_ONE_SMS.value, "ResidentAgent"),
            (Product.RESIDENT_ONE_VOICE.value, "RealtimeResidentResponderAgent"),
        ]

        for product_name, expected_class_name in test_cases:
            try:
                context = SessionScope(ask_request=ask_request_simple)
                agent = agent_selector(product_name, context)
                assert agent is not None
                assert expected_class_name in str(type(agent))
            except ImportError:
                # Some agents might not be available in test environment
                # This is acceptable for coverage purposes
                pass

    def test_agent_selector_unsupported_agent(self, ask_request_simple):
        """Test agent_selector with unsupported agent name."""
        with pytest.raises(UnsupportedAgentException, match="Unsupported agent: invalid_agent"):
            agent_selector("invalid_agent", ask_request_simple)

    def test_unsupported_agent_exception_inheritance(self):
        """Test that UnsupportedAgentException inherits from ValueError."""
        exception = UnsupportedAgentException("test message")
        assert isinstance(exception, ValueError)
        assert str(exception) == "test message"


class TestSessionScopeExtended:
    """Extended test cases for SessionScope class."""

    def test_session_context_properties(self):
        """Test SessionScope property accessors."""
        req = AskRequest(
            product="test_product",
            request_id="test_request_123",
            chat_session_id="test_session_456",
            prompt="test prompt",
            product_info=ProductInfo(knock_property_id="789"),
        )

        ctx = SessionScope(ask_request=req)

        # Test property accessors
        assert ctx.property_id == "789"
        # prospect_id comes from ask_request.prospect_id, not request_id
        assert ctx.prospect_id is None  # No prospect_id set in product_info

    def test_session_context_persona_with_string_product(self):
        """Test SessionScope persona detection with string product containing keywords."""
        # Valid resident product_info for resident persona tests
        valid_resident_product_info = ProductInfo(
            knock_property_id="123",
            uc_portal_base_url="https://cassidysouth.qa1.loftliving.com",
            uc_resident_member_id=UCReference(id=1, source=""),
            uc_resident_household_id=UCReference(id=2, source=""),
            uc_company_id=UCReference(id=3, source=""),
            uc_property_id=UCReference(id=4, source=""),
            ab_resident_id=UCReference(id=5, source=""),
            uc_lease_id=UCReference(id=6, source=""),
        )

        # Test resident detection with custom product string
        req_applicant = AskRequest(
            product="some_agent_leasing_resident_chat_feature",
            request_id="1",
            chat_session_id="abc",
            prompt="",
            product_info=valid_resident_product_info,
        )
        ctx_applicant = SessionScope(ask_request=req_applicant)
        assert ctx_applicant.persona == Persona.RESIDENT

        # Test resident voice detection with custom product string
        req_applicant_voice = AskRequest(
            product="agent_leasing_resident_voice_system",
            request_id="2",
            chat_session_id="def",
            prompt="",
            product_info=valid_resident_product_info,
        )
        ctx_applicant_voice = SessionScope(ask_request=req_applicant_voice)
        assert ctx_applicant_voice.persona == Persona.RESIDENT

        # Test resident detection - use actual Product enum values
        req_resident = AskRequest(
            product=Product.RESIDENT_ONE_CHAT.value,  # Use actual enum value
            request_id="3",
            chat_session_id="ghi",
            prompt="",
            product_info=valid_resident_product_info,
        )
        ctx_resident = SessionScope(ask_request=req_resident)
        assert ctx_resident.persona == Persona.RESIDENT

        # Test resident voice detection
        req_resident_voice = AskRequest(
            product=Product.RESIDENT_ONE_VOICE.value,  # Use actual enum value
            request_id="4",
            chat_session_id="jkl",
            prompt="",
            product_info=valid_resident_product_info,
        )
        ctx_resident_voice = SessionScope(ask_request=req_resident_voice)
        assert ctx_resident_voice.persona == Persona.RESIDENT

        # Test default to prospect
        req_other = AskRequest(
            product="some_other_product",
            request_id="5",
            chat_session_id="mno",
            prompt="",
            product_info=ProductInfo(knock_property_id="123"),
        )
        ctx_other = SessionScope(ask_request=req_other)
        assert ctx_other.persona == Persona.PROSPECT

    def test_session_context_persona_with_non_string_product(self):
        """Test SessionScope persona detection with non-string product."""
        # Test with a product that doesn't match any persona patterns
        req = AskRequest(
            product="unknown_product_type",
            request_id="1",
            chat_session_id="abc",
            prompt="",
            product_info=ProductInfo(knock_property_id="123"),
        )
        ctx = SessionScope(ask_request=req)
        assert ctx.persona == Persona.PROSPECT  # Should default to prospect

    def test_session_context_default_values(self):
        """Test SessionScope default field values."""
        req = AskRequest(
            product="test",
            request_id="1",
            chat_session_id="abc",
            prompt="",
            product_info=ProductInfo(knock_property_id="123"),
        )
        ctx = SessionScope(ask_request=req)

        # Test default values
        assert ctx.has_sms_consent is False
        assert ctx.current_time is not None


@pytest.mark.skipif(
    settings.ldp_modules_all_enabled,
    reason="Disabled modules are bypassed when LDP modules are forced enabled.",
)
class TestGetDisabledModules:
    @pytest.mark.asyncio
    @patch("agent_leasing.clients.ldp.get_ldp_data")
    async def test_all_modules_are_enabled(self, mock_get_ldp_data):
        mock_get_ldp_data.return_value = {
            "records": [
                {
                    "extras": {
                        "loftLiving": {
                            "modules": [
                                "PAYMENT_CENTER",
                                "PARKING_PASS",
                                "PACKAGES",
                                "EVENTS",
                                "MR",
                            ],
                        },
                    },
                },
            ],
        }

        assert await get_disabled_modules_with_pte("modules_1") == ([], False)

    @pytest.mark.asyncio
    @patch("agent_leasing.clients.ldp.get_ldp_data")
    async def test_only_packages_is_disabled(self, mock_get_ldp_data):
        mock_get_ldp_data.return_value = {
            "records": [
                {
                    "extras": {
                        "loftLiving": {
                            "modules": [
                                "PAYMENT_CENTER",
                                "PARKING_PASS",
                                "EVENTS",
                                "MR",
                            ],
                        },
                    },
                },
            ],
        }

        assert await get_disabled_modules_with_pte("modules_4") == (["PACKAGES"], False)

    @pytest.mark.asyncio
    @patch("agent_leasing.clients.ldp.get_ldp_data")
    async def test_all_modules_on_empty_response(self, mock_get_ldp_data):
        """If nothing is returned disable all modules."""
        mock_get_ldp_data.return_value = {"records": []}

        assert await get_disabled_modules_with_pte("modules_empty") == (
            [
                "MR",
                "PAYMENT_CENTER",
                "PACKAGES",
                "PARKING_PASS",
                "EVENTS",
            ],
            False,
        )

    @pytest.mark.asyncio
    @patch("agent_leasing.clients.ldp.get_ldp_data")
    async def test_all_modules_on_ldp_error(self, mock_get_ldp_data):
        """If LDP raises an error, disable all modules."""
        from agent_leasing.clients.ldp import LDPError

        mock_get_ldp_data.side_effect = LDPError("Connection failed")

        assert await get_disabled_modules_with_pte("modules_error") == (
            [
                "MR",
                "PAYMENT_CENTER",
                "PACKAGES",
                "PARKING_PASS",
                "EVENTS",
            ],
            False,
        )

    @pytest.mark.asyncio
    @patch("agent_leasing.clients.ldp.get_ldp_data")
    async def test_all_modules_on_no_modules_in_response(self, mock_get_ldp_data):
        """If LDP returns records but no modules, disable all modules."""
        mock_get_ldp_data.return_value = {"records": [{"extras": {"loftLiving": {}}}]}

        assert await get_disabled_modules_with_pte("modules_no_modules") == (
            [
                "MR",
                "PAYMENT_CENTER",
                "PACKAGES",
                "PARKING_PASS",
                "EVENTS",
            ],
            False,
        )


class TestCallAndSaveTool:
    """Unit tests for call_and_save_tool function."""

    @pytest.mark.asyncio
    async def test_call_and_save_tool_without_extract_attribute(self, resident_context_chat_ll):
        """Test call_and_save_tool saves full structured content when no extract_attribute is specified."""
        # Mock MCP server
        mock_mcp_server = AsyncMock()

        # Create the mock response as provided
        mock_response = CallToolResult(
            meta=None,
            content=[
                TextContent(
                    type="text",
                    text='{"property_id":21521,"summary":"DATA"}',
                    annotations=None,
                    meta=None,
                )
            ],
            structuredContent={
                "result": {
                    "property_id": 21521,
                    "summary": "DATA",
                }
            },
            isError=False,
        )

        mock_mcp_server.call_tool = AsyncMock(return_value=mock_response)

        # Call the function
        await call_and_save_tool(
            mcp_server=mock_mcp_server,
            tool_name="get_property_marketing_info",
            arguments={"property_id": 21521},
            context=resident_context_chat_ll,
            store_attribute="property_overview",
        )

        # Verify call_tool was called with correct arguments
        mock_mcp_server.call_tool.assert_called_once_with(
            "get_property_marketing_info",
            {"property_id": 21521},
            skip_pre_processors=False,
            skip_post_processors=False,
        )

        # Verify the full result was stored on the context
        assert hasattr(resident_context_chat_ll, "property_overview")
        assert resident_context_chat_ll.property_overview == {
            "property_id": 21521,
            "summary": "DATA",
        }

    @pytest.mark.asyncio
    async def test_call_and_save_tool_with_extract_attribute(self, resident_context_chat_ll):
        """Test call_and_save_tool extracts specific attribute from structured content."""
        # Mock MCP server
        mock_mcp_server = AsyncMock()

        # Create the mock response with structured content
        mock_response = CallToolResult(
            meta=None,
            content=[TextContent(type="text", text='{"property_id":21521,"summary":"..."}')],
            structuredContent={
                "result": {
                    "property_id": 21521,
                    "summary": "Apartment summary text",
                }
            },
            isError=False,
        )

        mock_mcp_server.call_tool = AsyncMock(return_value=mock_response)

        # Call the function with extract_attribute
        await call_and_save_tool(
            mcp_server=mock_mcp_server,
            tool_name="get_property_marketing_info",
            arguments={"property_id": 21521},
            context=resident_context_chat_ll,
            store_attribute="property_summary",
            extract_attribute="summary",
        )

        # Verify call_tool was called
        mock_mcp_server.call_tool.assert_called_once_with(
            "get_property_marketing_info",
            {"property_id": 21521},
            skip_pre_processors=False,
            skip_post_processors=False,
        )

        # Verify only the extracted attribute was stored
        assert hasattr(resident_context_chat_ll, "property_summary")
        assert resident_context_chat_ll.property_summary == "Apartment summary text"

    @pytest.mark.asyncio
    async def test_call_and_save_tool_with_cached_attribute(self, resident_context_chat_ll):
        """Test call_and_save_tool skips tool call when attribute already exists."""
        # Mock MCP server
        mock_mcp_server = AsyncMock()

        # Pre-populate the attribute on the context
        resident_context_chat_ll.property_overview = {"cached": "data"}

        # Call the function
        await call_and_save_tool(
            mcp_server=mock_mcp_server,
            tool_name="get_property_marketing_info",
            arguments={"property_id": 21521},
            context=resident_context_chat_ll,
            store_attribute="property_overview",
        )

        # Verify call_tool was NOT called (using cache)
        mock_mcp_server.call_tool.assert_not_called()

        # Verify the cached value remains unchanged
        assert resident_context_chat_ll.property_overview == {"cached": "data"}

    @pytest.mark.asyncio
    async def test_call_and_save_tool_with_extract_attribute_from_object(self, resident_context_chat_ll):
        """Test call_and_save_tool can extract attribute from object with hasattr."""
        # Mock MCP server
        mock_mcp_server = AsyncMock()

        # Create a mock object with an attribute
        class MockResult:
            def __init__(self):
                self.unit_count = 250

        mock_response = CallToolResult(
            meta=None,
            content=[TextContent(type="text", text="mock")],
            structuredContent={"result": MockResult()},
            isError=False,
        )

        mock_mcp_server.call_tool = AsyncMock(return_value=mock_response)

        # Call the function with extract_attribute
        await call_and_save_tool(
            mcp_server=mock_mcp_server,
            tool_name="get_property",
            arguments={"property_id": 21521},
            context=resident_context_chat_ll,
            store_attribute="unit_count",
            extract_attribute="unit_count",
        )

        # Verify the attribute was extracted from the object
        assert hasattr(resident_context_chat_ll, "unit_count")
        assert resident_context_chat_ll.unit_count == 250

    @pytest.mark.asyncio
    async def test_call_and_save_tool_with_filter_function(self, resident_context_chat_ll):
        """Test call_and_save_tool applies filter function to result."""
        # Mock MCP server
        mock_mcp_server = AsyncMock()

        # Create mock response with a list of items
        mock_response = CallToolResult(
            meta=None,
            content=[TextContent(type="text", text="mock")],
            structuredContent={
                "result": [
                    {"id": 1, "status": "active"},
                    {"id": 2, "status": "inactive"},
                    {"id": 3, "status": "active"},
                ]
            },
            isError=False,
        )

        mock_mcp_server.call_tool = AsyncMock(return_value=mock_response)

        # Define filter function to keep only active items
        def filter_active(items):
            return [item for item in items if item["status"] == "active"]

        # Call the function with filter_function
        await call_and_save_tool(
            mcp_server=mock_mcp_server,
            tool_name="get_items",
            arguments={"property_id": 21521},
            context=resident_context_chat_ll,
            store_attribute="active_items",
            filter_function=filter_active,
        )

        # Verify the result was filtered before storing
        assert hasattr(resident_context_chat_ll, "active_items")
        assert len(resident_context_chat_ll.active_items) == 2
        assert all(item["status"] == "active" for item in resident_context_chat_ll.active_items)

    @pytest.mark.asyncio
    async def test_call_and_save_tool_with_extract_and_filter(self, resident_context_chat_ll):
        """Test call_and_save_tool applies both extract_attribute and filter_function."""
        # Mock MCP server
        mock_mcp_server = AsyncMock()

        # Create mock response with nested data
        mock_response = CallToolResult(
            meta=None,
            content=[TextContent(type="text", text="mock")],
            structuredContent={
                "result": {
                    "items": [
                        {"id": 1, "priority": "high"},
                        {"id": 2, "priority": "low"},
                        {"id": 3, "priority": "high"},
                    ]
                }
            },
            isError=False,
        )

        mock_mcp_server.call_tool = AsyncMock(return_value=mock_response)

        # Define filter function
        def filter_high_priority(items):
            return [item for item in items if item["priority"] == "high"]

        # Call the function with both extract_attribute and filter_function
        await call_and_save_tool(
            mcp_server=mock_mcp_server,
            tool_name="get_tasks",
            arguments={"property_id": 21521},
            context=resident_context_chat_ll,
            store_attribute="high_priority_tasks",
            extract_attribute="items",
            filter_function=filter_high_priority,
        )

        # Verify both extraction and filtering were applied
        assert hasattr(resident_context_chat_ll, "high_priority_tasks")
        assert len(resident_context_chat_ll.high_priority_tasks) == 2
        assert all(task["priority"] == "high" for task in resident_context_chat_ll.high_priority_tasks)

    @pytest.mark.asyncio
    async def test_call_and_save_tool_extract_attribute_not_in_dict(self, resident_context_chat_ll):
        """Test call_and_save_tool when extract_attribute doesn't exist in dict result."""
        # Mock MCP server
        mock_mcp_server = AsyncMock()

        # Create mock response without the requested attribute
        mock_response = CallToolResult(
            meta=None,
            content=[TextContent(type="text", text="mock")],
            structuredContent={
                "result": {
                    "property_id": 21521,
                    "name": "Test Property",
                }
            },
            isError=False,
        )

        mock_mcp_server.call_tool = AsyncMock(return_value=mock_response)

        # Call the function with non-existent extract_attribute
        await call_and_save_tool(
            mcp_server=mock_mcp_server,
            tool_name="get_property",
            arguments={"property_id": 21521},
            context=resident_context_chat_ll,
            store_attribute="property_summary",
            extract_attribute="summary",  # Doesn't exist
        )

        # Verify the full result is stored when extraction fails
        assert hasattr(resident_context_chat_ll, "property_summary")
        assert resident_context_chat_ll.property_summary == {
            "property_id": 21521,
            "name": "Test Property",
        }

    @pytest.mark.asyncio
    async def test_call_and_save_tool_extract_attribute_not_in_object(self, resident_context_chat_ll):
        """Test call_and_save_tool when extract_attribute doesn't exist in object result."""
        # Mock MCP server
        mock_mcp_server = AsyncMock()

        # Create a mock object without the requested attribute
        class MockResult:
            def __init__(self):
                self.property_id = 21521

        mock_response = CallToolResult(
            meta=None,
            content=[TextContent(type="text", text="mock")],
            structuredContent={"result": MockResult()},
            isError=False,
        )

        mock_mcp_server.call_tool = AsyncMock(return_value=mock_response)

        # Call the function with non-existent extract_attribute
        await call_and_save_tool(
            mcp_server=mock_mcp_server,
            tool_name="get_property",
            arguments={"property_id": 21521},
            context=resident_context_chat_ll,
            store_attribute="property_name",
            extract_attribute="name",  # Doesn't exist
        )

        # Verify the full object is stored when extraction fails
        assert hasattr(resident_context_chat_ll, "property_name")
        assert isinstance(resident_context_chat_ll.property_name, MockResult)
        assert resident_context_chat_ll.property_name.property_id == 21521

    @pytest.mark.asyncio
    async def test_call_and_save_tool_with_none_result(self, resident_context_chat_ll):
        """Test call_and_save_tool when tool returns None result."""
        # Mock MCP server
        mock_mcp_server = AsyncMock()

        # Create mock response with None content
        mock_response = CallToolResult(
            meta=None,
            content=[TextContent(type="text", text="")],
            structuredContent=None,
            isError=False,
        )

        mock_mcp_server.call_tool = AsyncMock(return_value=mock_response)

        # Call the function
        await call_and_save_tool(
            mcp_server=mock_mcp_server,
            tool_name="get_optional_data",
            arguments={"property_id": 21521},
            context=resident_context_chat_ll,
            store_attribute="optional_data",
        )

        # Verify empty string is stored
        assert hasattr(resident_context_chat_ll, "optional_data")
        assert resident_context_chat_ll.optional_data == ""

    @pytest.mark.asyncio
    async def test_call_and_save_tool_with_none_result_and_extract_attribute(self, resident_context_chat_ll):
        """Test call_and_save_tool with extract_attribute when result is None."""
        # Mock MCP server
        mock_mcp_server = AsyncMock()

        # Create mock response with None content
        mock_response = CallToolResult(
            meta=None,
            content=[TextContent(type="text", text="")],
            structuredContent=None,
            isError=False,
        )

        mock_mcp_server.call_tool = AsyncMock(return_value=mock_response)

        # Call the function with extract_attribute
        await call_and_save_tool(
            mcp_server=mock_mcp_server,
            tool_name="get_optional_data",
            arguments={"property_id": 21521},
            context=resident_context_chat_ll,
            store_attribute="optional_field",
            extract_attribute="field",
        )

        # Verify empty string is stored (extraction skipped for None)
        assert hasattr(resident_context_chat_ll, "optional_field")
        assert resident_context_chat_ll.optional_field == ""

    @pytest.mark.asyncio
    async def test_call_and_save_tool_filter_function_with_none(self, resident_context_chat_ll):
        """Test call_and_save_tool with filter_function when result is None."""
        # Mock MCP server
        mock_mcp_server = AsyncMock()

        # Create mock response with None
        mock_response = CallToolResult(
            meta=None,
            content=[TextContent(type="text", text="")],
            structuredContent=None,
            isError=False,
        )

        mock_mcp_server.call_tool = AsyncMock(return_value=mock_response)

        # Define filter function that handles None
        def safe_filter(result):
            return result if result else []

        # Call the function with filter_function
        await call_and_save_tool(
            mcp_server=mock_mcp_server,
            tool_name="get_data",
            arguments={"property_id": 21521},
            context=resident_context_chat_ll,
            store_attribute="filtered_data",
            filter_function=safe_filter,
        )

        # Verify filtered result is stored
        assert hasattr(resident_context_chat_ll, "filtered_data")
        assert resident_context_chat_ll.filtered_data == []


class TestGetPrompt:
    """Unit tests for AgentWithMCP._get_prompt method."""

    def test_get_prompt_loads_base_version(self, tmp_path):
        """Test that _get_prompt loads version 0 (base file) correctly."""
        # Create a test instruction file
        instructions_file = tmp_path / "INSTRUCTIONS.md"
        instructions_file.write_text("Base version content")

        # Clear cache before test
        AgentWithMCP._PROMPT_CACHE.clear()

        # Load version 0
        result = AgentWithMCP._get_prompt(str(instructions_file), version=0)

        assert result == "Base version content"
        # Verify it was cached
        assert str(instructions_file.resolve()) in AgentWithMCP._PROMPT_CACHE

    def test_get_prompt_loads_versioned_file(self, tmp_path):
        """Test that _get_prompt loads versioned files correctly."""
        # Create base and versioned files
        base_file = tmp_path / "INSTRUCTIONS.md"
        base_file.write_text("Base version")

        v2_file = tmp_path / "INSTRUCTIONS_V2.md"
        v2_file.write_text("Version 2 content")

        # Clear cache before test
        AgentWithMCP._PROMPT_CACHE.clear()

        # Load version 2
        result = AgentWithMCP._get_prompt(str(base_file), version=2)

        assert result == "Version 2 content"

    def test_get_prompt_fallback_to_version_0(self, tmp_path):
        """Test that _get_prompt falls back to version 0 when requested version not found."""
        # Create only base file
        base_file = tmp_path / "INSTRUCTIONS.md"
        base_file.write_text("Base fallback content")

        # Clear cache before test
        AgentWithMCP._PROMPT_CACHE.clear()

        # Request version 5 which doesn't exist
        result = AgentWithMCP._get_prompt(str(base_file), version=5)

        # Should return version 0 content
        assert result == "Base fallback content"

    def test_get_prompt_raises_error_when_no_versions_exist(self, tmp_path):
        """Test that _get_prompt raises FileNotFoundError when no versions exist."""
        # Create path but no file
        non_existent_file = tmp_path / "NONEXISTENT.md"

        # Clear cache before test
        AgentWithMCP._PROMPT_CACHE.clear()

        # Should raise FileNotFoundError
        with pytest.raises(FileNotFoundError) as exc_info:
            AgentWithMCP._get_prompt(str(non_existent_file), version=0)

        assert "not found" in str(exc_info.value)

    def test_get_prompt_caching_behavior(self, tmp_path):
        """Test that _get_prompt caches all versions on first access."""
        # Create multiple versions
        base_file = tmp_path / "INSTRUCTIONS.md"
        base_file.write_text("Base")

        v1_file = tmp_path / "INSTRUCTIONS_V1.md"
        v1_file.write_text("Version 1")

        v2_file = tmp_path / "INSTRUCTIONS_V2.md"
        v2_file.write_text("Version 2")

        # Clear cache before test
        AgentWithMCP._PROMPT_CACHE.clear()

        # First call loads all versions
        result_v0 = AgentWithMCP._get_prompt(str(base_file), version=0)
        assert result_v0 == "Base"

        # Verify all versions were cached
        cache_key = str(base_file.resolve())
        assert cache_key in AgentWithMCP._PROMPT_CACHE
        assert 0 in AgentWithMCP._PROMPT_CACHE[cache_key]
        assert 1 in AgentWithMCP._PROMPT_CACHE[cache_key]
        assert 2 in AgentWithMCP._PROMPT_CACHE[cache_key]

        # Subsequent calls should use cache (modify files to verify)
        base_file.write_text("Modified base")
        v1_file.write_text("Modified v1")

        # Should still return cached content
        result_v1 = AgentWithMCP._get_prompt(str(base_file), version=1)
        assert result_v1 == "Version 1"  # Original cached content

    def test_get_prompt_multiple_files_independent_caches(self, tmp_path):
        """Test that different files have independent caches."""
        # Create two different instruction files
        file1 = tmp_path / "INSTRUCTIONS_A.md"
        file1.write_text("File A content")

        file2 = tmp_path / "INSTRUCTIONS_B.md"
        file2.write_text("File B content")

        # Clear cache before test
        AgentWithMCP._PROMPT_CACHE.clear()

        # Load both files
        result_a = AgentWithMCP._get_prompt(str(file1), version=0)
        result_b = AgentWithMCP._get_prompt(str(file2), version=0)

        assert result_a == "File A content"
        assert result_b == "File B content"

        # Verify separate cache entries
        assert str(file1.resolve()) in AgentWithMCP._PROMPT_CACHE
        assert str(file2.resolve()) in AgentWithMCP._PROMPT_CACHE

    def test_get_prompt_default_version_parameter(self, tmp_path):
        """Test that version parameter defaults to 0."""
        base_file = tmp_path / "INSTRUCTIONS.md"
        base_file.write_text("Default version content")

        # Clear cache before test
        AgentWithMCP._PROMPT_CACHE.clear()

        # Call without version parameter
        result = AgentWithMCP._get_prompt(str(base_file))

        assert result == "Default version content"

    def test_get_prompt_handles_special_characters_in_content(self, tmp_path):
        """Test that _get_prompt handles files with special characters correctly."""
        base_file = tmp_path / "INSTRUCTIONS.md"
        special_content = "Content with: colons, {braces}, and\nnewlines"
        base_file.write_text(special_content)

        # Clear cache before test
        AgentWithMCP._PROMPT_CACHE.clear()

        result = AgentWithMCP._get_prompt(str(base_file), version=0)

        assert result == special_content

    def test_get_prompt_ignores_invalid_version_filenames(self, tmp_path):
        """Test that _get_prompt ignores files with invalid version patterns."""
        base_file = tmp_path / "INSTRUCTIONS.md"
        base_file.write_text("Base content")

        # Create files that shouldn't be picked up
        (tmp_path / "INSTRUCTIONS_VBAD.md").write_text("Invalid 1")
        (tmp_path / "INSTRUCTIONS_V.md").write_text("Invalid 2")
        (tmp_path / "INSTRUCTIONS_V-1.md").write_text("Invalid 3")
        (tmp_path / "INSTRUCTIONS_V2.txt").write_text("Wrong extension")

        # Clear cache before test
        AgentWithMCP._PROMPT_CACHE.clear()

        # Should only load base version
        AgentWithMCP._get_prompt(str(base_file), version=0)

        cache_key = str(base_file.resolve())
        # Only version 0 should be in cache
        assert list(AgentWithMCP._PROMPT_CACHE[cache_key].keys()) == [0]

    def test_get_prompt_version_none_uses_default(self, tmp_path):
        """Test that version=None uses the default value of 0."""
        base_file = tmp_path / "INSTRUCTIONS.md"
        base_file.write_text("Base content")

        # Clear cache before test
        AgentWithMCP._PROMPT_CACHE.clear()

        # Call with version=None
        result = AgentWithMCP._get_prompt(str(base_file), version=None)

        assert result == "Base content"

    def test_get_prompt_fallback_to_version_0_logs_warning(self, tmp_path, caplog):
        """Test that fallback to version 0 logs a warning."""
        import logging

        # Create only base file
        base_file = tmp_path / "INSTRUCTIONS.md"
        base_file.write_text("Base content")

        # Clear cache before test
        AgentWithMCP._PROMPT_CACHE.clear()

        # Request non-existent version with logging enabled
        with caplog.at_level(logging.WARNING):
            result = AgentWithMCP._get_prompt(str(base_file), version=99)

        assert result == "Base content"
        # Verify warning was logged
        assert any("falling back to version 0" in record.message for record in caplog.records)

    def test_get_prompt_error_message_shows_available_versions(self, tmp_path):
        """Test that error message lists available versions."""
        # Create a versioned file but no base
        v1_file = tmp_path / "INSTRUCTIONS_V1.md"
        v1_file.write_text("Version 1")

        base_file = tmp_path / "INSTRUCTIONS.md"
        # Don't create base file

        # Clear cache before test
        AgentWithMCP._PROMPT_CACHE.clear()

        # Request non-existent version with no fallback
        with pytest.raises(FileNotFoundError) as exc_info:
            AgentWithMCP._get_prompt(str(base_file), version=99)

        # Error should mention available versions
        error_msg = str(exc_info.value)
        assert "Available versions" in error_msg
        assert "[1]" in error_msg
