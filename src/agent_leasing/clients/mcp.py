import asyncio
import json
import time
from datetime import datetime
from typing import Any, Awaitable, Callable

import httpx
import structlog
from agents import AgentBase, RunContextWrapper
from agents.mcp import MCPServerStreamableHttp, MCPServerStreamableHttpParams, ToolFilter
from cashews import cache
from langsmith import traceable
from mcp import Tool as MCPTool
from mcp.types import CallToolResult, TextContent

from agent_leasing.agent.tools.mcp_pre_processors import VerificationError
from agent_leasing.models.context import SessionScope
from agent_leasing.settings import settings

logger = structlog.getLogger()


class CachingMCPServer(MCPServerStreamableHttp):
    """
    MCP client instance that caches specified tools.

    Always caches tool outputs as strings. This is a naive implementation of client caching
    that makes some broad assumptions about how and what to cache.

    Additional args for caching in CachingMCPServer:
        - cacheable_tools: List of tool names to cache
        - ttl: Time to live aka cache expiry
        - tool_post_processors: Dict mapping tool names to lists of post-processing functions
        - idempotent_tools: List of tool names that are safe to retry on timeout
        - tool_call_timeout_seconds: Timeout for individual tool calls (default: None = no timeout)
        - max_retries: Number of retries for idempotent tools on timeout (default: 1)

    """

    def __init__(
        self,
        params: MCPServerStreamableHttpParams,
        cache_tools_list: bool = False,
        name: str | None = None,
        auth_function: Callable[[], Awaitable[str]] | None = None,
        client_session_timeout_seconds: float | None = 5,
        tool_filter: ToolFilter = None,
        use_structured_content: bool = False,
        cacheable_tools: list[str] = None,
        ttl: str = "2h",
        context: SessionScope = None,
        tool_post_processors: dict[str, list[Callable[[CallToolResult], CallToolResult]]] | None = None,
        tool_pre_processors: dict[str, list[Callable[[CallToolResult], CallToolResult]]] | None = None,
        idempotent_tools: list[str] | None = None,
        tool_call_timeout_seconds: float | None = None,
        max_retries: int = 1,
    ):
        super().__init__(
            params,
            cache_tools_list,
            name,
            client_session_timeout_seconds,
            tool_filter,
            use_structured_content,
        )
        if not cacheable_tools:
            cacheable_tools = []
        self.params["headers"] = {}
        self.cacheable_tools = cacheable_tools
        self.cache_expires = ttl
        self.auth_function = auth_function
        self.context = context
        if self.auth_function:
            self.params["httpx_client_factory"] = _create_dynamic_auth_client_factory(self.params["headers"])
        self.tool_pre_processors = tool_pre_processors or {}
        self.tool_post_processors = tool_post_processors or {}
        self.idempotent_tools = idempotent_tools or []
        self.tool_call_timeout_seconds = tool_call_timeout_seconds
        self.max_retries = max_retries

    async def connect(self):
        """Connect to MCP server, setting auth token if auth_function is provided."""
        auth_ms = 0
        if self.auth_function:
            logger.debug(f"Running auth for MCP server connection: {self.name}")
            auth_start = time.monotonic()
            try:
                auth_token = await self.auth_function(self.context)
                if auth_token:
                    self.params["headers"]["Authorization"] = f"Bearer {auth_token}"
                    logger.debug(f"Auth token set for MCP server: {self.name}")
                else:
                    logger.warning(f"Auth function returned no token for MCP server: {self.name}")
            except Exception as e:
                logger.error(f"Failed to get auth token for MCP server {self.name}: {type(e).__name__}: {e!r}")
                raise
            finally:
                auth_ms = int((time.monotonic() - auth_start) * 1000)

        # Call parent connect method
        logger.debug(f"Connecting to MCP server: {self.name}", url=self.params.get("url"))
        handshake_start = time.monotonic()
        result = await super().connect()
        handshake_ms = int((time.monotonic() - handshake_start) * 1000)

        if settings.startup_latency_logging_enabled:
            logger.info(
                f"MCP server connected: {self.name}",
                event_type="mcp_server_connected",
                mcp_server=self.name,
                auth_ms=auth_ms,
                handshake_ms=handshake_ms,
                total_ms=auth_ms + handshake_ms,
            )
        else:
            logger.debug(f"Connected to MCP server: {self.name}")
        return result

    async def list_tools(
        self,
        run_context: RunContextWrapper[Any] | None = None,
        agent: AgentBase | None = None,
    ) -> list[MCPTool]:
        """Override list_tools to gracefully recover from situation where MCP server connection failed."""
        try:
            timeout = getattr(self, "client_session_timeout_seconds", None) or 5
            return await asyncio.wait_for(super().list_tools(run_context, agent), timeout=timeout)
        except TimeoutError as e:
            logger.warning(f"MCP list_tools timed out for {self.name}: {e}")
            return []
        except Exception as e:
            logger.warning(f"MCP list_tools failed for {self.name}: {e}")
            return []

    async def check_health(self) -> bool:
        """Check if the underlying MCP connection is alive.

        Bypasses the tools cache and exception-swallowing list_tools() override
        to perform a real network round-trip to the MCP server.
        """
        try:
            self.invalidate_tools_cache()
            await asyncio.wait_for(
                super().list_tools(),
                timeout=self.client_session_timeout_seconds or 5,
            )
            return True
        except Exception as e:
            logger.debug(f"Health check failed for {self.name}: [{type(e).__name__}] {e}")
            return False

    @traceable(run_type="tool")
    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        skip_pre_processors: bool = False,
        skip_post_processors: bool = False,
        **kwargs: Any,
    ) -> CallToolResult:
        """
        Call a tool and cache the output if the tool is in the cacheable_tools list.

        Cached tools are cached by the tool name and parameters and
        not keyed to individual user sessions.

        Errors are caught and returned as tool results with isError=True,
        allowing the LLM to handle failures gracefully instead of crashing.

        Set skip_pre_processors=True for internal calls (e.g. prefetch) that
        should bypass verification checks.

        Set skip_post_processors=True for internal calls (e.g. prefetch) that
        should bypass post-processors. Prefetch is conceptually a side-effect-free
        path — without this flag, post-processors like the task-activity emitter
        would fire as if the LLM had requested the data, producing spurious
        analytics events on every first turn (KNCK-39556 PR4 follow-up).

        Both flags are intercepted before kwargs are forwarded to the parent
        ``MCPServerStreamableHttp.call_tool``, so they have no surface to the
        LLM tool-call route — only internal Python callers can set them.

        Extra kwargs (e.g. ``meta``) are forwarded unchanged to the parent
        ``MCPServerStreamableHttp.call_tool`` so new SDK fields pass through.
        """
        t0 = time.monotonic()
        outcome = "success"
        error_type: str | None = None
        logger.info(
            f"MCP tool call: {self.name} -> {tool_name} with args: {arguments}",
            event_type="tool_called",
            tool_name=tool_name,
            mcp_server=self.name,
        )
        try:
            result = await self._call_tool_impl(
                tool_name,
                arguments,
                skip_pre_processors=skip_pre_processors,
                skip_post_processors=skip_post_processors,
                **kwargs,
            )
            if getattr(result, "isError", False):
                outcome = "failure"
            return result
        except VerificationError as e:
            # Verification errors are expected and should be returned as tool errors
            # so the LLM can ask for verification
            outcome = "verification_required"
            logger.info(
                f"Verification required for tool {tool_name}: {e}",
                event_type="tool_completed",
                tool_name=tool_name,
                mcp_server=self.name,
                tool_outcome=outcome,
            )
            return CallToolResult(
                content=[TextContent(text=str(e), type="text")],
                isError=True,
            )
        except Exception as e:
            outcome = "failure"
            error_type = type(e).__name__
            logger.warning(
                f"MCP tool error caught and converted: {tool_name} - [{error_type}] {e}",
                event_type="tool_completed",
                tool_name=tool_name,
                mcp_server=self.name,
                tool_outcome=outcome,
                error_type=error_type,
            )

            return CallToolResult(
                content=[TextContent(text=self._format_tool_error(tool_name, e), type="text")],
                isError=True,
            )
        finally:
            tool_call_ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                f"MCP tool call complete: {self.name} -> {tool_name} ({outcome})",
                event_type="tool_completed",
                tool_name=tool_name,
                mcp_server=self.name,
                tool_outcome=outcome,
                tool_call_ms=tool_call_ms,
                **({"error_type": error_type} if error_type else {}),
            )

    def _format_tool_error(self, tool_name: str, error: Exception) -> str:
        """Format an error into a user-friendly message for the LLM."""
        error_str = str(error).lower()

        if "timeout" in error_str or "timed out" in error_str:
            return (
                f"TOOL_ERROR: The '{tool_name}' tool timed out. "
                "The service may be temporarily slow. "
                "Please inform the user you're having trouble retrieving this information "
                "and offer to connect them with staff if needed."
            )

        if "connection" in error_str or "connect" in error_str:
            return (
                f"TOOL_ERROR: Unable to connect to the '{tool_name}' service. "
                "Please inform the user you're having trouble retrieving this information "
                "and offer to connect them with staff if needed."
            )

        return (
            f"TOOL_ERROR: The '{tool_name}' tool encountered an error. "
            "Please inform the user you're having trouble retrieving this information "
            "and offer to connect them with staff if needed."
        )

    async def _call_tool_impl(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        skip_pre_processors: bool = False,
        skip_post_processors: bool = False,
        **kwargs: Any,
    ) -> CallToolResult:
        """Internal implementation of call_tool with caching and post-processing."""
        if not skip_pre_processors:
            arguments = self._apply_pre_processing(tool_name, arguments)

        if settings.caching_enabled and tool_name in self.cacheable_tools:
            result = await self._call_with_caching(tool_name, arguments, **kwargs)
        else:
            result = await self._run_mcp_tool(tool_name, arguments, **kwargs)

        if skip_post_processors:
            return result
        return self._apply_post_processing(tool_name, result, arguments)

    async def _call_with_caching(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        **kwargs: Any,
    ) -> CallToolResult:
        """Execute tool with caching logic."""
        cache_key = self._build_cache_key(tool_name, arguments)
        logger.debug(f"Cachable tool: {tool_name}, cache_key: {cache_key}")

        cached = await cache.get(cache_key, default=None)
        if cached:
            logger.debug(f"Returning cached output for tool: {tool_name}")
            return self._build_cached_result(cached)

        result = await self._run_mcp_tool(tool_name, arguments, **kwargs)
        await self._cache_if_successful(cache_key, tool_name, result)
        return result

    def _build_cache_key(self, tool_name: str, arguments: dict[str, Any] | None) -> str:
        """Build a cache key from tool name and arguments."""
        params = json.dumps(arguments, sort_keys=True) if arguments else "{}"
        return f"{tool_name}:{params}"

    def _build_cached_result(self, cached_output: str) -> CallToolResult:
        """Build a CallToolResult from cached string output."""
        try:
            structured_content = json.loads(cached_output)
        except json.JSONDecodeError:
            structured_content = None

        return CallToolResult(
            content=[TextContent(text=cached_output, type="text")],
            structuredContent=structured_content,
        )

    async def _cache_if_successful(self, cache_key: str, tool_name: str, result: CallToolResult) -> None:
        """Cache the result if it's successful (not an error)."""
        if result and len(result.content) > 0 and not result.isError:
            logger.debug(f"Set cache: {cache_key}={result.content[0].text}")
            await cache.set(cache_key, result.content[0].text, expire=self.cache_expires)
        elif result and result.isError:
            logger.debug(f"Skipping cache for tool {tool_name} due to error response")

    def _apply_pre_processing(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Apply preprocessing function if one is registered for this tool."""
        if tool_name not in self.tool_pre_processors:
            return arguments

        try:
            pre_processor_list = self.tool_pre_processors[tool_name]

            for pre_processor in pre_processor_list:
                try:
                    arguments = pre_processor(arguments, context=self.context)
                except VerificationError:
                    # Re-raise verification errors to be caught by call_tool
                    raise
                except Exception as e:
                    logger.warning(
                        f"Pre-processing failed for tool {tool_name} -> {pre_processor.__name__}(): {e}. Skipping."
                    )
            logger.debug(f"Applied pre-processing for tool: {tool_name}")
            return arguments
        except VerificationError:
            # Re-raise verification errors to be caught by call_tool
            raise
        except Exception as e:
            logger.warning(f"Preprocessing failed for tool {tool_name}: {e}. Returning original arguments.")
            return arguments

    def _apply_post_processing(
        self,
        tool_name: str,
        result: CallToolResult,
        arguments: dict[str, Any] | None = None,
    ) -> CallToolResult:
        """Apply post-processing function if one is registered for this tool.

        `arguments` is the dict the LLM passed to the tool; post-processors
        that want the user-side input (e.g., `chat_summary`) read it from
        this kwarg.
        """
        if tool_name not in self.tool_post_processors:
            return result

        try:
            post_processor_list = self.tool_post_processors[tool_name]

            for post_processor in post_processor_list:
                try:
                    result = post_processor(result, context=self.context, arguments=arguments)
                except Exception as e:
                    logger.warning(
                        f"Post-processing failed for tool {tool_name} -> {post_processor.__name__}(): {e}. Skipping."
                    )
            logger.debug(f"Applied post-processing for tool: {tool_name}")
            return result
        except Exception as e:
            logger.warning(f"Post-processing failed for tool {tool_name}: {e}. Returning original result.")
            return result

    @staticmethod
    async def invalidate_property_caches(property_id: str) -> dict:
        """Invalidate the cache for the given property."""

        # Property-level keys
        cache_keys = {
            f"ldp_property_data:{property_id}": None,
        }

        for key in cache_keys.keys():
            logger.info(f"Invalidating property cache for key: {key}")
            invalidated_key_response = await cache.delete(key)
            cache_keys[key] = invalidated_key_response

        return cache_keys

    async def _run_mcp_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        **kwargs: Any,
    ):
        """Run an MCP tool with timeout, retry for idempotent tools, and auth failure detection."""

        # Track this MCP tool call in the context for data curation logging
        if self.context:
            mcp_tool_call_record = {
                "mcp_server": self.name,
                "tool_name": tool_name,
                "arguments": arguments,
                "timestamp": datetime.now().isoformat(),
            }
            self.context.mcp_tool_calls.append(mcp_tool_call_record)

        if self.auth_function:
            logger.debug(f"Refreshing auth token for tool: {self.name}:{tool_name}")
            try:
                auth_token = await self.auth_function(self.context)
                if auth_token:
                    self.params["headers"]["Authorization"] = f"Bearer {auth_token}"
                    logger.debug(f"Auth token refreshed for tool: {self.name}:{tool_name}")
                else:
                    logger.warning(f"Auth function returned no token for tool: {self.name}:{tool_name}")
            except Exception as e:
                logger.error(f"Failed to refresh auth token for tool {self.name}:{tool_name}: {e}")
                # Continue with existing token rather than failing

        return await self._execute_with_timeout_and_retry(tool_name, arguments, **kwargs)

    async def _execute_with_timeout_and_retry(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        **kwargs: Any,
    ):
        """Execute tool call with optional timeout and retry logic for idempotent tools."""
        is_idempotent = tool_name in self.idempotent_tools
        max_attempts = (self.max_retries + 1) if is_idempotent else 1
        last_exception = None

        for attempt in range(max_attempts):
            try:
                if attempt > 0:
                    logger.info(f"Retrying {self.name}:{tool_name} (attempt {attempt + 1}/{max_attempts})")

                return await self._execute_single_call(tool_name, arguments, **kwargs)

            except TimeoutError as e:
                last_exception = e
                if is_idempotent and attempt < max_attempts - 1:
                    logger.warning(
                        f"Timeout on {self.name}:{tool_name} (attempt {attempt + 1}/{max_attempts}), will retry"
                    )
                else:
                    logger.warning(
                        f"Timeout on {self.name}:{tool_name} after {attempt + 1} attempt(s), no more retries"
                    )
                    raise

            except Exception as e:
                if self._is_auth_failure(e):
                    logger.warning(f"Auth failure detected for {self.name}:{tool_name}, attempting reconnection")
                    return await self._handle_connection_failure_and_retry(tool_name, arguments, **kwargs)
                elif self._is_closed_connection(e):
                    logger.warning(f"Closed connection detected for {self.name}:{tool_name}, attempting reconnection")
                    return await self._handle_connection_failure_and_retry(tool_name, arguments, **kwargs)
                else:
                    # Re-raise non-reconnectable exceptions immediately
                    raise

        # Should not reach here, but just in case
        raise last_exception

    async def _execute_single_call(self, tool_name: str, arguments: dict[str, Any], **kwargs: Any):
        """Execute a single tool call with optional timeout."""
        if self.tool_call_timeout_seconds:
            return await asyncio.wait_for(
                super().call_tool(tool_name, arguments, **kwargs),
                timeout=self.tool_call_timeout_seconds,
            )
        else:
            return await super().call_tool(tool_name, arguments, **kwargs)

    def _is_closed_connection(self, exception: Exception) -> bool:
        """Check if an exception indicates the underlying connection was closed.

        anyio raises ClosedResourceError when reading/writing to a closed stream,
        and BrokenResourceError when the transport is broken mid-transfer. Both
        can occur when a pooled MCP connection's HTTP/SSE session is closed
        server-side while the pool still holds the client-side object as live.
        """
        return type(exception).__name__ in ("ClosedResourceError", "BrokenResourceError")

    def _is_auth_failure(self, exception: Exception) -> bool:
        """Check if an exception indicates an authentication failure."""
        # Check for HTTP 401 Unauthorized errors
        if hasattr(exception, "response") and hasattr(exception.response, "status_code"):
            return exception.response.status_code == 401

        # Check for HTTPStatusError with 401
        if "HTTPStatusError" in str(type(exception)) and "401" in str(exception):
            return True

        # Check for common auth error messages
        error_msg = str(exception).lower()
        auth_indicators = [
            "unauthorized",
            "401",
            "authentication failed",
            "invalid token",
            "token expired",
        ]
        return any(indicator in error_msg for indicator in auth_indicators)

    async def _handle_connection_failure_and_retry(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        **kwargs: Any,
    ):
        """Handle a connection failure by reconnecting and retrying the tool call.

        Used for both auth failures (401) and closed-connection errors (ClosedResourceError,
        BrokenResourceError). Refreshes the auth token when an auth_function is available.
        """
        try:
            logger.info(f"Attempting to reconnect {self.name} after connection failure")

            # Step 1: Cleanup existing connection
            await self.cleanup()
            logger.debug(f"Cleaned up existing connection for {self.name}")

            # Step 2 & 3: Refresh auth token if an auth_function is available
            if self.auth_function:
                auth_token = await self.auth_function(self.context)
                if auth_token:
                    self.params["headers"]["Authorization"] = f"Bearer {auth_token}"
                    logger.debug(f"Updated auth token for {self.name}")
                else:
                    logger.warning(f"Auth function returned no token during reconnection for {self.name}")

            # Step 4: Reconnect
            await self.connect()
            logger.info(f"Successfully reconnected {self.name}")

            # Step 5: Retry the original tool call
            return await super().call_tool(tool_name, arguments, **kwargs)

        except Exception as recovery_error:
            logger.error(f"Failed to reconnect {self.name}: {recovery_error}")
            raise Exception(f"Reconnection failed for {self.name}: {recovery_error}") from recovery_error


def _create_dynamic_auth_client_factory(headers_ref: dict) -> Callable:
    """Create an httpx client factory that injects auth headers dynamically per-request.

    The standard httpx.AsyncClient copies headers at construction time, so mutations
    to self.params["headers"] after connect() have no effect. This factory adds an
    event hook that reads Authorization from the mutable headers_ref dict before
    every HTTP request, ensuring token refreshes in _run_mcp_tool() are effective.
    """

    def factory(
        headers: dict | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        from mcp.shared._httpx_utils import create_mcp_http_client

        # Strip Authorization from default headers — the event hook handles it dynamically
        safe_headers = (
            {k: v for k, v in headers.items() if k.lower() != "authorization"} if headers else None
        ) or None

        client = create_mcp_http_client(headers=safe_headers, timeout=timeout, auth=auth)

        async def _inject_auth(request: httpx.Request) -> None:
            auth_value = headers_ref.get("Authorization")
            if auth_value:
                request.headers["Authorization"] = auth_value

        client.event_hooks["request"].append(_inject_auth)
        return client

    return factory


# Backwards compatibility alias
FilteredCachingMCPServer = CachingMCPServer
