"""Tests for resident_one_agent."""

from agent_leasing.agent.resident_one_agent.agent import get_mcp_servers
from agent_leasing.clients.ldp import (
    MODULE_TO_MCP_TOOLS,
    get_disabled_tools_from_disabled_modules,
)


class TestResidentAgentCreateAgent:
    """Tests for ResidentAgent._create_agent and MCP server configuration."""

    def test_get_mcp_servers_excludes_tools_for_disabled_modules(self, resident_context_unified_chat_ll, monkeypatch):
        """get_mcp_servers should not expose tools for disabled modules (e.g. PARKING_PASS)."""

        # Simulate LDP marking PARKING_PASS as disabled and derive disabled tools from the mapping.
        disabled_modules = ["PARKING_PASS"]
        resident_context_unified_chat_ll.disabled_modules = disabled_modules
        resident_context_unified_chat_ll.disabled_tools = get_disabled_tools_from_disabled_modules(
            MODULE_TO_MCP_TOOLS,
            disabled_modules,
        )

        captured_allowed_tools: list[list[str]] = []

        def fake_create_static_tool_filter(*, allowed_tool_names, **kwargs):  # noqa: ANN001, ARG001
            # Capture the allowed tool list for each MCP server for later assertions.
            captured_allowed_tools.append(list(allowed_tool_names))
            return None

        monkeypatch.setattr(
            "agent_leasing.agent.resident_one_agent.agent.create_static_tool_filter",
            fake_create_static_tool_filter,
        )

        _ = get_mcp_servers(resident_context_unified_chat_ll)

        # Flatten all captured tool lists from the various MCP servers.
        all_allowed_tools = {tool for tools in captured_allowed_tools for tool in tools}

        # When PARKING_PASS is disabled, its tool should not be exposed on any MCP server.
        assert "issue_guest_parking_pass" not in all_allowed_tools
