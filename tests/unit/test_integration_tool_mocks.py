from mcp.types import CallToolResult

from agent_leasing.clients.mcp import CachingMCPServer
from tests.integration.helpers import apply_tool_mocks


class TestApplyToolMocks:
    async def test_local_tool_return_value(self):
        import agent_leasing.agent.tools as tools_module

        original = tools_module.create_link.on_invoke_tool
        tool_mocks = {"local:create_link": {"return_value": "SENTINEL_LINK"}}

        with apply_tool_mocks(tool_mocks):
            result = await tools_module.create_link.on_invoke_tool(None, "{}")
            assert result == "SENTINEL_LINK"

        assert tools_module.create_link.on_invoke_tool is original

    async def test_local_tool_error_returns_string_by_default(self):
        import agent_leasing.agent.tools as tools_module

        tool_mocks = {"local:create_link": {"error": "boom"}}
        with apply_tool_mocks(tool_mocks):
            result = await tools_module.create_link.on_invoke_tool(None, "{}")
            assert result == "boom"

    async def test_mcp_tool_return_value_is_converted_to_call_tool_result(self):
        server = CachingMCPServer(
            name="Loft MCP Server",
            params={"url": "http://example.test", "headers": {}},
            cache_tools_list=False,
        )

        tool_mocks = {"mcp:loft:get_residents_packages": {"return_value": {"packages_list": [], "packages_count": 0}}}
        with apply_tool_mocks(tool_mocks):
            result = await server._run_mcp_tool("get_residents_packages", {"resident_id": "1"})

        assert isinstance(result, CallToolResult)
        assert result.structuredContent["packages_list"] == []
