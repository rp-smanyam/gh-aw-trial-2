"""Unit tests for CachingMCPServer timeout and retry functionality."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import CallToolResult, TextContent

from agent_leasing.agent.tools.mcp_pre_processors import VerificationError
from agent_leasing.clients.mcp import CachingMCPServer


@pytest.fixture
def mock_mcp_server():
    """Create a CachingMCPServer with mocked parent class."""
    server = MagicMock(spec=CachingMCPServer)
    server.name = "Test MCP Server"
    server.params = {"url": "http://test", "headers": {}}
    server.cacheable_tools = []
    server.cache_expires = "2h"
    server.auth_function = None
    server.context = None
    server.tool_pre_processors = {}
    server.tool_post_processors = {}
    server.idempotent_tools = ["idempotent_tool", "another_idempotent"]
    server.tool_call_timeout_seconds = 5.0
    server.max_retries = 2

    # Bind the real methods to the mock
    server._execute_with_timeout_and_retry = (
        lambda tool_name, arguments, **kwargs: CachingMCPServer._execute_with_timeout_and_retry(
            server, tool_name, arguments, **kwargs
        )
    )
    server._execute_single_call = lambda tool_name, arguments, **kwargs: CachingMCPServer._execute_single_call(
        server, tool_name, arguments, **kwargs
    )
    server._format_tool_error = lambda tool_name, error: CachingMCPServer._format_tool_error(server, tool_name, error)
    server._is_auth_failure = lambda e: CachingMCPServer._is_auth_failure(server, e)
    server._is_closed_connection = lambda e: CachingMCPServer._is_closed_connection(server, e)
    server.call_tool = lambda tool_name, arguments, skip_pre_processors=False, **kwargs: CachingMCPServer.call_tool(
        server, tool_name, arguments, skip_pre_processors=skip_pre_processors, **kwargs
    )

    return server


class TestTimeoutAndRetry:
    """Tests for timeout and retry functionality in CachingMCPServer."""

    @pytest.mark.asyncio
    async def test_successful_call_no_timeout(self, mock_mcp_server):
        """Test that a successful call returns immediately without retry."""
        expected_result = CallToolResult(content=[TextContent(text="success", type="text")])

        # Mock the parent's call_tool method
        async def mock_parent_call(*args, **kwargs):
            return expected_result

        with patch("agents.mcp.MCPServerStreamableHttp.call_tool", side_effect=mock_parent_call):
            result = await mock_mcp_server._execute_with_timeout_and_retry("idempotent_tool", {"arg": "value"})

        assert result == expected_result

    @pytest.mark.asyncio
    async def test_timeout_retries_idempotent_tool(self, mock_mcp_server):
        """Test that idempotent tools are retried on timeout."""
        expected_result = CallToolResult(content=[TextContent(text="success", type="text")])
        call_count = 0

        async def mock_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise TimeoutError("Connection timed out")
            return expected_result

        with patch("agents.mcp.MCPServerStreamableHttp.call_tool", side_effect=mock_call):
            result = await mock_mcp_server._execute_with_timeout_and_retry("idempotent_tool", {"arg": "value"})

        assert result == expected_result
        assert call_count == 2  # First attempt failed, second succeeded

    @pytest.mark.asyncio
    async def test_timeout_no_retry_non_idempotent_tool(self, mock_mcp_server):
        """Test that non-idempotent tools are NOT retried on timeout."""

        async def mock_call(*args, **kwargs):
            raise TimeoutError("Connection timed out")

        with patch("agents.mcp.MCPServerStreamableHttp.call_tool", side_effect=mock_call):
            with pytest.raises(TimeoutError):
                await mock_mcp_server._execute_with_timeout_and_retry("non_idempotent_tool", {"arg": "value"})

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self, mock_mcp_server):
        """Test that retries stop after max_retries is exceeded."""
        call_count = 0

        async def mock_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise TimeoutError("Connection timed out")

        with patch("agents.mcp.MCPServerStreamableHttp.call_tool", side_effect=mock_call):
            with pytest.raises(TimeoutError):
                await mock_mcp_server._execute_with_timeout_and_retry("idempotent_tool", {"arg": "value"})

        # max_retries=2 means 3 total attempts (1 initial + 2 retries)
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_non_timeout_exception_not_retried(self, mock_mcp_server):
        """Test that non-timeout exceptions are not retried."""
        call_count = 0

        async def mock_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise ValueError("Some other error")

        with patch("agents.mcp.MCPServerStreamableHttp.call_tool", side_effect=mock_call):
            with pytest.raises(ValueError):
                await mock_mcp_server._execute_with_timeout_and_retry("idempotent_tool", {"arg": "value"})

        assert call_count == 1  # No retry for non-timeout exceptions

    @pytest.mark.asyncio
    async def test_execute_single_call_with_timeout(self, mock_mcp_server):
        """Test that _execute_single_call applies timeout correctly."""
        expected_result = CallToolResult(content=[TextContent(text="success", type="text")])

        async def mock_parent_call(*args, **kwargs):
            return expected_result

        with patch("agents.mcp.MCPServerStreamableHttp.call_tool", side_effect=mock_parent_call):
            result = await mock_mcp_server._execute_single_call("some_tool", {"arg": "value"})

        assert result == expected_result

    @pytest.mark.asyncio
    async def test_execute_single_call_timeout_raises(self, mock_mcp_server):
        """Test that _execute_single_call raises TimeoutError when call exceeds timeout."""

        async def slow_call(*args, **kwargs):
            await asyncio.sleep(10)  # Longer than timeout
            return CallToolResult(content=[TextContent(text="success", type="text")])

        with patch("agents.mcp.MCPServerStreamableHttp.call_tool", side_effect=slow_call):
            with pytest.raises(TimeoutError):
                await mock_mcp_server._execute_single_call("some_tool", {"arg": "value"})

    @pytest.mark.asyncio
    async def test_no_timeout_when_timeout_seconds_is_none(self, mock_mcp_server):
        """Test that no timeout is applied when tool_call_timeout_seconds is None."""
        mock_mcp_server.tool_call_timeout_seconds = None
        expected_result = CallToolResult(content=[TextContent(text="success", type="text")])

        async def mock_parent_call(*args, **kwargs):
            return expected_result

        with patch("agents.mcp.MCPServerStreamableHttp.call_tool", side_effect=mock_parent_call):
            result = await mock_mcp_server._execute_single_call("some_tool", {"arg": "value"})

        assert result == expected_result


class TestCallToolErrorHandling:
    """Tests for call_tool error handling with timeout errors."""

    @pytest.mark.asyncio
    async def test_timeout_error_formatted_nicely(self, mock_mcp_server):
        """Test that timeout errors are formatted into user-friendly messages."""
        mock_mcp_server.tool_pre_processors = {}
        mock_mcp_server.tool_post_processors = {}

        async def mock_impl(*args, **kwargs):
            raise TimeoutError("Connection timed out")

        with patch.object(mock_mcp_server, "_call_tool_impl", side_effect=mock_impl):
            result = await mock_mcp_server.call_tool("some_tool", {"arg": "value"})

        assert result.isError is True
        assert "timed out" in result.content[0].text.lower()
        assert "TOOL_ERROR" in result.content[0].text

    @pytest.mark.asyncio
    async def test_format_tool_error_timeout(self, mock_mcp_server):
        """Test _format_tool_error for timeout errors."""
        error = TimeoutError("Connection timed out")
        message = mock_mcp_server._format_tool_error("test_tool", error)

        assert "TOOL_ERROR" in message
        assert "test_tool" in message
        assert "timed out" in message.lower()

    @pytest.mark.asyncio
    async def test_format_tool_error_connection(self, mock_mcp_server):
        """Test _format_tool_error for connection errors."""
        error = ConnectionError("Failed to connect")
        message = mock_mcp_server._format_tool_error("test_tool", error)

        assert "TOOL_ERROR" in message
        assert "test_tool" in message
        assert "connect" in message.lower()

    @pytest.mark.asyncio
    async def test_call_tool_error_includes_exception_type(self, mock_mcp_server):
        """Test that call_tool log message includes exception type for diagnosability."""
        mock_mcp_server.tool_pre_processors = {}
        mock_mcp_server.tool_post_processors = {}

        async def mock_impl(*args, **kwargs):
            raise RuntimeError("something broke")

        with patch.object(mock_mcp_server, "_call_tool_impl", side_effect=mock_impl):
            result = await mock_mcp_server.call_tool("some_tool", {"arg": "value"})

        assert result.isError is True


class TestCheckHealth:
    """Tests for CachingMCPServer.check_health() method."""

    @pytest.mark.asyncio
    async def test_check_health_returns_true_when_healthy(self):
        """check_health should return True when the server responds to list_tools."""
        server = MagicMock(spec=CachingMCPServer)
        server.client_session_timeout_seconds = 5
        server.invalidate_tools_cache = MagicMock()

        async def mock_list_tools(*args, **kwargs):
            return [{"name": "test_tool"}]

        with patch("agents.mcp.MCPServerStreamableHttp.list_tools", side_effect=mock_list_tools):
            server.check_health = lambda: CachingMCPServer.check_health(server)
            result = await server.check_health()

        assert result is True
        server.invalidate_tools_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_health_returns_false_when_connection_broken(self):
        """check_health should return False when the underlying connection is dead."""
        server = MagicMock(spec=CachingMCPServer)
        server.client_session_timeout_seconds = 5
        server.invalidate_tools_cache = MagicMock()

        async def mock_list_tools(*args, **kwargs):
            raise Exception("ClosedResourceError")

        with patch("agents.mcp.MCPServerStreamableHttp.list_tools", side_effect=mock_list_tools):
            server.check_health = lambda: CachingMCPServer.check_health(server)
            result = await server.check_health()

        assert result is False
        server.invalidate_tools_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_health_returns_false_on_timeout(self):
        """check_health should return False when list_tools times out."""
        server = MagicMock(spec=CachingMCPServer)
        server.client_session_timeout_seconds = 0.1
        server.invalidate_tools_cache = MagicMock()

        async def slow_list_tools(*args, **kwargs):
            await asyncio.sleep(10)
            return []

        with patch("agents.mcp.MCPServerStreamableHttp.list_tools", side_effect=slow_list_tools):
            server.check_health = lambda: CachingMCPServer.check_health(server)
            result = await server.check_health()

        assert result is False

    @pytest.mark.asyncio
    async def test_check_health_uses_default_timeout_when_none(self):
        """check_health should use 5s default when client_session_timeout_seconds is None."""
        server = MagicMock(spec=CachingMCPServer)
        server.client_session_timeout_seconds = None
        server.invalidate_tools_cache = MagicMock()

        async def mock_list_tools(*args, **kwargs):
            return [{"name": "test_tool"}]

        with patch("agents.mcp.MCPServerStreamableHttp.list_tools", side_effect=mock_list_tools):
            server.check_health = lambda: CachingMCPServer.check_health(server)
            result = await server.check_health()

        assert result is True


class TestSkipPreProcessors:
    """Tests for skip_pre_processors flag in call_tool and _call_tool_impl."""

    @pytest.fixture
    def server(self):
        """Create a server with a verification pre-processor on one tool."""
        s = MagicMock(spec=CachingMCPServer)
        s.name = "Test MCP Server"
        s.cacheable_tools = []
        s.cache_expires = "2h"
        s.context = MagicMock()
        s.context.identity_verified = {}
        s.idempotent_tools = []
        s.tool_call_timeout_seconds = None
        s.max_retries = 1

        def _raising_pre_processor(arguments, context=None, **kwargs):
            raise VerificationError("VERIFICATION_REQUIRED: Call verify_resident_identity first.")

        s.tool_pre_processors = {"get_active_service_requests": [_raising_pre_processor]}
        s.tool_post_processors = {}
        s._apply_pre_processing = lambda tool_name, arguments: CachingMCPServer._apply_pre_processing(
            s, tool_name, arguments
        )
        s._apply_post_processing = lambda tool_name, result, arguments=None: result
        s._call_tool_impl = (
            lambda tool_name, arguments, skip_pre_processors=False, **kwargs: CachingMCPServer._call_tool_impl(
                s, tool_name, arguments, skip_pre_processors=skip_pre_processors, **kwargs
            )
        )
        s.call_tool = lambda tool_name, arguments, skip_pre_processors=False, **kwargs: CachingMCPServer.call_tool(
            s, tool_name, arguments, skip_pre_processors=skip_pre_processors, **kwargs
        )
        return s

    @pytest.mark.asyncio
    async def test_verification_pre_processor_blocks_unverified_call(self, server):
        """Without skip_pre_processors, a VerificationError returns isError=True."""
        expected = CallToolResult(content=[TextContent(text="data", type="text")])
        server._run_mcp_tool = AsyncMock(return_value=expected)

        result = await server.call_tool("get_active_service_requests", {})

        assert result.isError is True
        assert "VERIFICATION_REQUIRED" in result.content[0].text
        server._run_mcp_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_pre_processors_bypasses_verification(self, server):
        """With skip_pre_processors=True, the pre-processor is skipped and the tool succeeds."""
        expected = CallToolResult(content=[TextContent(text="data", type="text")])
        server._run_mcp_tool = AsyncMock(return_value=expected)

        result = await server.call_tool("get_active_service_requests", {}, skip_pre_processors=True)

        assert result.isError is not True
        assert result.content[0].text == "data"
        server._run_mcp_tool.assert_called_once()


class TestSkipPostProcessors:
    """Tests for skip_post_processors flag — prefetch path must not fire side
    effects (e.g. task-activity emitter) the resident never triggered.
    """

    @pytest.fixture
    def server(self):
        s = MagicMock(spec=CachingMCPServer)
        s.name = "Test MCP Server"
        s.cacheable_tools = []
        s.cache_expires = "2h"
        s.context = MagicMock()
        s.context.identity_verified = {}
        s.idempotent_tools = []
        s.tool_call_timeout_seconds = None
        s.max_retries = 1
        s.tool_pre_processors = {}

        post_processor_calls: list[tuple[str, CallToolResult]] = []

        def _spy_post_processor(result, **_kwargs):
            post_processor_calls.append(("called", result))
            return result

        s.tool_post_processors = {"get_residents_packages": [_spy_post_processor]}
        s._post_processor_calls = post_processor_calls
        s._apply_pre_processing = lambda tool_name, arguments: arguments
        s._apply_post_processing = lambda tool_name, result, arguments=None: CachingMCPServer._apply_post_processing(
            s, tool_name, result, arguments
        )
        s._call_tool_impl = lambda *args, **kwargs: CachingMCPServer._call_tool_impl(s, *args, **kwargs)
        s.call_tool = lambda *args, **kwargs: CachingMCPServer.call_tool(s, *args, **kwargs)
        return s

    @pytest.mark.asyncio
    async def test_post_processor_fires_by_default(self, server):
        expected = CallToolResult(content=[TextContent(text="data", type="text")])
        server._run_mcp_tool = AsyncMock(return_value=expected)

        await server.call_tool("get_residents_packages", {})

        assert len(server._post_processor_calls) == 1

    @pytest.mark.asyncio
    async def test_skip_post_processors_suppresses_post_processing(self, server):
        """Prefetch path: post-processor must not run, even though the tool call succeeded."""
        expected = CallToolResult(content=[TextContent(text="data", type="text")])
        server._run_mcp_tool = AsyncMock(return_value=expected)

        result = await server.call_tool("get_residents_packages", {}, skip_post_processors=True)

        assert result.content[0].text == "data"
        assert server._post_processor_calls == []
        server._run_mcp_tool.assert_called_once()
