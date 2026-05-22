"""Integration tests for get_mcp_servers function."""

from agent_leasing.agent.resident_one_agent.agent import get_mcp_servers


class TestGetMcpServers:
    """Tests for get_mcp_servers function."""

    def test_get_mcp_servers_excludes_policy_ledger_when_payment_center_disabled(
        self, resident_context_unified_chat_ll
    ):
        """Test that Policy & Ledger MCP server is excluded when PAYMENT_CENTER module is disabled."""
        context = resident_context_unified_chat_ll
        context.disabled_modules = ["PAYMENT_CENTER"]
        context.disabled_tools = []

        mcp_servers = get_mcp_servers(context)

        assert "policy_and_ledger_mcp_server" not in mcp_servers
        assert "knock_mcp_server" in mcp_servers
        assert "loft_mcp_server" in mcp_servers

    def test_get_mcp_servers_includes_all_when_no_modules_disabled(self, resident_context_unified_chat_ll):
        """Test that all MCP servers are included when no modules are disabled."""
        context = resident_context_unified_chat_ll
        context.disabled_modules = []
        context.disabled_tools = []

        mcp_servers = get_mcp_servers(context)

        assert "knock_mcp_server" in mcp_servers
        assert "loft_mcp_server" in mcp_servers
        assert "policy_and_ledger_mcp_server" in mcp_servers
