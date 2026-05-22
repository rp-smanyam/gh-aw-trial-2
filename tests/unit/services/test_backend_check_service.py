"""Tests for backend_check_service module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import CallToolResult, TextContent

import agent_leasing.services.backend_check_service as backend_check_module
from agent_leasing.services.backend_check_service import (
    _check_mcp_server,
    _check_rest_api,
    _get_mcp_healthcheck_ids,
    _load_healthcheck_ids,
    build_mcp_dependency_status,
)

FAKE_IDS = {
    "knock_property_id": 100,
    "knock_resident_id": 200,
    "uc_company_id": 300,
    "uc_property_id": 400,
    "uc_resident_household_id": 500,
    "uc_resident_member_id": 600,
    "ab_resident_id": 700,
    "uc_community_id": 800,
    "ab_unit_id": 900,
}


@pytest.fixture(autouse=True)
def _reset_healthcheck_cache():
    """Reset the module-level cache before each test."""
    backend_check_module.MCP_HEALTHCHECK_IDS = None
    backend_check_module.MCP_HEALTHCHECK_IDS_ERROR = None
    yield
    backend_check_module.MCP_HEALTHCHECK_IDS = None
    backend_check_module.MCP_HEALTHCHECK_IDS_ERROR = None


# ---------------------------------------------------------------------------
# _load_healthcheck_ids
# ---------------------------------------------------------------------------
class TestLoadHealthcheckIds:
    """Tests for _load_healthcheck_ids."""

    def test_loads_alpha_file(self, tmp_path):
        """Test that alpha environment loads the correct file."""
        example_data = {
            "product_info": {
                "knock_property_id": 1,
                "knock_resident_id": 2,
                "uc_company_id": {"id": 3},
                "uc_property_id": {"id": 4},
                "uc_resident_household_id": {"id": 5},
                "uc_resident_member_id": {"id": 6},
                "ab_resident_id": {"id": 7},
                "uc_community_id": {"id": 8},
                "ab_unit_id": {"id": 9},
            }
        }
        example_dir = tmp_path / "api" / "example_data" / "resident" / "chat"
        example_dir.mkdir(parents=True)
        (example_dir / "example_ask_request_ll.alpha.json").write_text(__import__("json").dumps(example_data))

        with (
            patch("agent_leasing.services.backend_check_service.settings") as mock_settings,
            patch("agent_leasing.services.backend_check_service.pathlib.Path") as mock_path,
        ):
            mock_settings.environment = "alpha"
            # Make parents[1] resolve to tmp_path so example_dir is found
            mock_path_instance = MagicMock()
            mock_path.return_value = mock_path_instance
            mock_path_instance.parents.__getitem__ = lambda self, idx: tmp_path

            result = _load_healthcheck_ids()

        assert result["knock_property_id"] == 1
        assert result["knock_resident_id"] == 2
        assert result["uc_company_id"] == 3
        assert result["ab_resident_id"] == 7

    def test_loads_default_file_for_unknown_env(self, tmp_path):
        """Test that unknown environment falls back to default file."""
        example_data = {
            "product_info": {
                "knock_property_id": 10,
                "knock_resident_id": 20,
                "uc_company_id": {"id": 30},
                "uc_property_id": {"id": 40},
                "uc_resident_household_id": {"id": 50},
                "uc_resident_member_id": {"id": 60},
                "ab_resident_id": {"id": 70},
                "uc_community_id": {"id": 80},
                "ab_unit_id": {"id": 90},
            }
        }
        example_dir = tmp_path / "api" / "example_data" / "resident" / "chat"
        example_dir.mkdir(parents=True)
        (example_dir / "example_ask_request_ll.json").write_text(__import__("json").dumps(example_data))

        with (
            patch("agent_leasing.services.backend_check_service.settings") as mock_settings,
            patch("agent_leasing.services.backend_check_service.pathlib.Path") as mock_path,
        ):
            mock_settings.environment = "local"
            mock_path_instance = MagicMock()
            mock_path.return_value = mock_path_instance
            mock_path_instance.parents.__getitem__ = lambda self, idx: tmp_path

            result = _load_healthcheck_ids()

        assert result["knock_property_id"] == 10


# ---------------------------------------------------------------------------
# _get_mcp_healthcheck_ids (lazy caching)
# ---------------------------------------------------------------------------
class TestGetMcpHealthcheckIds:
    """Tests for _get_mcp_healthcheck_ids."""

    def test_returns_cached_ids(self):
        """Test that cached IDs are returned without reloading."""
        backend_check_module.MCP_HEALTHCHECK_IDS = FAKE_IDS
        ids, error = _get_mcp_healthcheck_ids()
        assert ids == FAKE_IDS
        assert error is None

    def test_returns_cached_error(self):
        """Test that cached error is returned without retrying."""
        backend_check_module.MCP_HEALTHCHECK_IDS_ERROR = "some error"
        ids, error = _get_mcp_healthcheck_ids()
        assert ids is None
        assert error == "some error"

    def test_loads_and_caches_on_first_call(self):
        """Test that IDs are loaded and cached on first access."""
        with patch(
            "agent_leasing.services.backend_check_service._load_healthcheck_ids",
            return_value=FAKE_IDS,
        ):
            ids, error = _get_mcp_healthcheck_ids()
        assert ids == FAKE_IDS
        assert error is None
        assert backend_check_module.MCP_HEALTHCHECK_IDS == FAKE_IDS

    def test_caches_error_on_failure(self):
        """Test that loading error is cached on failure."""
        with patch(
            "agent_leasing.services.backend_check_service._load_healthcheck_ids",
            side_effect=FileNotFoundError("missing file"),
        ):
            ids, error = _get_mcp_healthcheck_ids()
        assert ids is None
        assert "missing file" in error
        assert backend_check_module.MCP_HEALTHCHECK_IDS_ERROR is not None


# ---------------------------------------------------------------------------
# _check_mcp_server
# ---------------------------------------------------------------------------
class TestCheckMcpServer:
    """Tests for _check_mcp_server."""

    @pytest.mark.asyncio
    async def test_healthy_server(self):
        """Test healthy MCP server returns healthy status."""
        mock_result = CallToolResult(content=[TextContent(type="text", text="ok")], isError=False)
        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(return_value=mock_result)
        mock_mcp.__aenter__ = AsyncMock(return_value=mock_mcp)
        mock_mcp.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "agent_leasing.services.backend_check_service.CachingMCPServer",
            return_value=mock_mcp,
        ):
            result = await _check_mcp_server(
                "http://localhost:8042",
                None,
                [("test_tool", {"param": "value"})],
            )

        assert result["status"] == "healthy"
        assert result["reason"] == "ok"
        assert len(result["tools"]) == 1
        assert result["tools"][0]["name"] == "test_tool"
        assert result["tools"][0]["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_degraded_server_tool_error(self):
        """Test MCP server with tool error returns degraded status."""
        mock_result = CallToolResult(content=[TextContent(type="text", text="tool failed")], isError=True)
        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(return_value=mock_result)
        mock_mcp.__aenter__ = AsyncMock(return_value=mock_mcp)
        mock_mcp.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "agent_leasing.services.backend_check_service.CachingMCPServer",
            return_value=mock_mcp,
        ):
            result = await _check_mcp_server(
                "http://localhost:8042",
                None,
                [("failing_tool", {"param": "value"})],
            )

        assert result["status"] == "degraded"
        assert "failing_tool" in result["reason"]

    @pytest.mark.asyncio
    async def test_degraded_server_tool_exception(self):
        """Test MCP server with tool exception returns degraded status."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(side_effect=RuntimeError("connection failed"))
        mock_mcp.__aenter__ = AsyncMock(return_value=mock_mcp)
        mock_mcp.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "agent_leasing.services.backend_check_service.CachingMCPServer",
            return_value=mock_mcp,
        ):
            result = await _check_mcp_server(
                "http://localhost:8042",
                None,
                [("broken_tool", {"param": "value"})],
            )

        assert result["status"] == "degraded"
        assert "connection failed" in result["reason"]

    @pytest.mark.asyncio
    async def test_degraded_server_connection_failure(self):
        """Test MCP server connection failure returns degraded status."""
        mock_mcp = AsyncMock()
        mock_mcp.__aenter__ = AsyncMock(side_effect=ConnectionError("cannot connect"))
        mock_mcp.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "agent_leasing.services.backend_check_service.CachingMCPServer",
            return_value=mock_mcp,
        ):
            result = await _check_mcp_server(
                "http://localhost:8042",
                None,
                [("some_tool", {"param": "value"})],
            )

        assert result["status"] == "degraded"
        assert "cannot connect" in result["reason"]

    @pytest.mark.asyncio
    async def test_tool_returns_none(self):
        """Test MCP server with None tool result returns degraded."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(return_value=None)
        mock_mcp.__aenter__ = AsyncMock(return_value=mock_mcp)
        mock_mcp.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "agent_leasing.services.backend_check_service.CachingMCPServer",
            return_value=mock_mcp,
        ):
            result = await _check_mcp_server(
                "http://localhost:8042",
                None,
                [("null_tool", {"param": "value"})],
            )

        assert result["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_multiple_tools_mixed_status(self):
        """Test MCP server with mixed tool results."""
        ok_result = CallToolResult(content=[TextContent(type="text", text="ok")], isError=False)
        err_result = CallToolResult(content=[TextContent(type="text", text="error")], isError=True)
        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(side_effect=[ok_result, err_result])
        mock_mcp.__aenter__ = AsyncMock(return_value=mock_mcp)
        mock_mcp.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "agent_leasing.services.backend_check_service.CachingMCPServer",
            return_value=mock_mcp,
        ):
            result = await _check_mcp_server(
                "http://localhost:8042",
                None,
                [("good_tool", {}), ("bad_tool", {})],
            )

        assert result["status"] == "degraded"
        assert len(result["tools"]) == 2
        assert result["tools"][0]["status"] == "healthy"
        assert result["tools"][1]["status"] == "degraded"


# ---------------------------------------------------------------------------
# _check_rest_api
# ---------------------------------------------------------------------------
class TestCheckRestApi:
    """Tests for _check_rest_api."""

    @pytest.mark.asyncio
    async def test_healthy_api(self):
        """Test healthy REST API returns healthy status."""
        with patch(
            "agent_leasing.services.backend_check_service.perform_api_call",
            new_callable=AsyncMock,
            return_value={"result": "ok"},
        ):
            result = await _check_rest_api(
                "test-api",
                host="http://localhost",
                endpoint="/test",
                method="POST",
                auth_server="test",
                payload={"key": "value"},
            )

        assert result["status"] == "healthy"
        assert result["reason"] == "ok"
        assert result["latency_ms"]["p50"] is not None

    @pytest.mark.asyncio
    async def test_degraded_api_none_response(self):
        """Test REST API returning None is marked degraded."""
        with patch(
            "agent_leasing.services.backend_check_service.perform_api_call",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await _check_rest_api(
                "test-api",
                host="http://localhost",
                endpoint="/test",
                method="POST",
                auth_server="test",
            )

        assert result["status"] == "degraded"
        assert result["reason"] == "No response from API"

    @pytest.mark.asyncio
    async def test_degraded_api_exception(self):
        """Test REST API raising exception is marked degraded."""
        with patch(
            "agent_leasing.services.backend_check_service.perform_api_call",
            new_callable=AsyncMock,
            side_effect=ConnectionError("timeout"),
        ):
            result = await _check_rest_api(
                "test-api",
                host="http://localhost",
                endpoint="/test",
                method="POST",
                auth_server="test",
            )

        assert result["status"] == "degraded"
        assert "timeout" in result["reason"]
        assert result["latency_ms"]["p50"] is not None


# ---------------------------------------------------------------------------
# build_mcp_dependency_status
# ---------------------------------------------------------------------------
class TestBuildMcpDependencyStatus:
    """Tests for build_mcp_dependency_status."""

    @pytest.mark.asyncio
    async def test_returns_degraded_when_ids_fail_to_load(self):
        """Test that failed ID loading returns degraded status."""
        with patch(
            "agent_leasing.services.backend_check_service._get_mcp_healthcheck_ids",
            return_value=(None, "Failed to load"),
        ):
            result = await build_mcp_dependency_status()

        assert result["status"] == "degraded"
        assert result["dependencies"] == []
        assert "Unable to load" in result["details"]["reason"]

    @pytest.mark.asyncio
    async def test_returns_healthy_when_all_pass(self):
        """Test healthy status when all MCP servers and APIs are healthy."""
        healthy_mcp = {"status": "healthy", "reason": "ok", "tools": []}
        healthy_api = {"status": "healthy", "reason": "ok", "latency_ms": {"p50": 10, "p99": 10}}

        with (
            patch(
                "agent_leasing.services.backend_check_service._get_mcp_healthcheck_ids",
                return_value=(FAKE_IDS, None),
            ),
            patch(
                "agent_leasing.services.backend_check_service._check_mcp_server",
                new_callable=AsyncMock,
                return_value=healthy_mcp,
            ),
            patch(
                "agent_leasing.services.backend_check_service._check_rest_api",
                new_callable=AsyncMock,
                return_value=healthy_api,
            ),
        ):
            result = await build_mcp_dependency_status()

        assert result["status"] == "healthy"
        assert "mcp_tools" in result
        assert "apis" in result

    @pytest.mark.asyncio
    async def test_returns_degraded_when_mcp_degraded(self):
        """Test degraded status when an MCP server is degraded."""
        degraded_mcp = {"status": "degraded", "reason": "tool failed", "tools": []}
        healthy_api = {"status": "healthy", "reason": "ok", "latency_ms": {"p50": 10, "p99": 10}}

        with (
            patch(
                "agent_leasing.services.backend_check_service._get_mcp_healthcheck_ids",
                return_value=(FAKE_IDS, None),
            ),
            patch(
                "agent_leasing.services.backend_check_service._check_mcp_server",
                new_callable=AsyncMock,
                return_value=degraded_mcp,
            ),
            patch(
                "agent_leasing.services.backend_check_service._check_rest_api",
                new_callable=AsyncMock,
                return_value=healthy_api,
            ),
        ):
            result = await build_mcp_dependency_status()

        assert result["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_returns_degraded_when_api_degraded(self):
        """Test degraded status when REST API is degraded."""
        healthy_mcp = {"status": "healthy", "reason": "ok", "tools": []}
        degraded_api = {"status": "degraded", "reason": "timeout", "latency_ms": {"p50": None, "p99": None}}

        with (
            patch(
                "agent_leasing.services.backend_check_service._get_mcp_healthcheck_ids",
                return_value=(FAKE_IDS, None),
            ),
            patch(
                "agent_leasing.services.backend_check_service._check_mcp_server",
                new_callable=AsyncMock,
                return_value=healthy_mcp,
            ),
            patch(
                "agent_leasing.services.backend_check_service._check_rest_api",
                new_callable=AsyncMock,
                return_value=degraded_api,
            ),
        ):
            result = await build_mcp_dependency_status()

        assert result["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_checks_all_four_mcp_servers(self):
        """Test that all four MCP servers are checked."""
        healthy_mcp = {"status": "healthy", "reason": "ok", "tools": []}
        healthy_api = {"status": "healthy", "reason": "ok", "latency_ms": {"p50": 10, "p99": 10}}

        with (
            patch(
                "agent_leasing.services.backend_check_service._get_mcp_healthcheck_ids",
                return_value=(FAKE_IDS, None),
            ),
            patch(
                "agent_leasing.services.backend_check_service._check_mcp_server",
                new_callable=AsyncMock,
                return_value=healthy_mcp,
            ) as mock_check_mcp,
            patch(
                "agent_leasing.services.backend_check_service._check_rest_api",
                new_callable=AsyncMock,
                return_value=healthy_api,
            ),
        ):
            result = await build_mcp_dependency_status()

        assert mock_check_mcp.call_count == 4
        assert "mcp-facilities" in result["mcp_tools"]
        assert "mcp-knock" in result["mcp_tools"]
        assert "mcp-loft" in result["mcp_tools"]
        assert "mcp-onsite" in result["mcp_tools"]
