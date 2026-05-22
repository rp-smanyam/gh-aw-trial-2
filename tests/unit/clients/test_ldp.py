import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from agent_leasing.clients import ldp as ldp_module
from agent_leasing.clients.ldp import (
    LDPError,
    _parse_enabled_modules_with_pte,
    _parse_resident_summary,
    call_ldp_api,
    fetch_ldp_property_data,
    get_available_services,
    get_ldp_data,
)


@pytest.fixture(autouse=True)
async def _reset_ldp_http_session():
    """Reset the module-level aiohttp session so each test sees a fresh one.

    Real sessions created by tests that don't mock aiohttp.ClientSession (most of
    test_call_ldp_api) would otherwise leak with "Unclosed client session" warnings.
    Awaiting close() teardown-side hands aiohttp the chance to release sockets.
    """
    ldp_module._http_session = None
    yield
    await ldp_module.close()


class TestCallLdpApi:
    """Tests for call_ldp_api function with LDPError handling."""

    @pytest.mark.asyncio
    @patch("agent_leasing.clients.ldp.settings")
    @patch("agent_leasing.clients.ldp.get_ldp_auth_token")
    @patch("aiohttp.ClientSession.post")
    async def test_raises_ldp_error_on_http_error_status(self, mock_post, mock_get_token, mock_settings):
        mock_settings.ldp_auth_enabled = True
        mock_get_token.return_value = "test-token"

        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.__aenter__.return_value = mock_response
        mock_response.__aexit__.return_value = None
        mock_post.return_value = mock_response

        with pytest.raises(LDPError, match="HTTP 500"):
            await call_ldp_api("http://example.com/api", {"key": "value"})

    @pytest.mark.asyncio
    @patch("agent_leasing.clients.ldp.settings")
    @patch("agent_leasing.clients.ldp.get_ldp_auth_token")
    @patch("aiohttp.ClientSession.post")
    async def test_raises_ldp_error_on_json_decode_error(self, mock_post, mock_get_token, mock_settings):
        mock_settings.ldp_auth_enabled = True
        mock_get_token.return_value = "test-token"

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.side_effect = json.decoder.JSONDecodeError("", "", 0)
        mock_response.__aenter__.return_value = mock_response
        mock_response.__aexit__.return_value = None
        mock_post.return_value = mock_response

        with pytest.raises(LDPError, match="JSON parsing failed"):
            await call_ldp_api("http://example.com/api", {"key": "value"})

    @pytest.mark.asyncio
    @patch("agent_leasing.clients.ldp.settings")
    @patch("agent_leasing.clients.ldp.get_ldp_auth_token")
    @patch("aiohttp.ClientSession.post")
    async def test_raises_ldp_error_on_connection_error(self, mock_post, mock_get_token, mock_settings):
        mock_settings.ldp_auth_enabled = True
        mock_get_token.return_value = "test-token"

        mock_post.side_effect = aiohttp.ClientError("Connection refused")

        with pytest.raises(LDPError, match="Connection failed"):
            await call_ldp_api("http://example.com/api", {"key": "value"})

    @pytest.mark.asyncio
    @patch("agent_leasing.clients.ldp.settings")
    @patch("agent_leasing.clients.ldp.get_ldp_auth_token")
    @patch("aiohttp.ClientSession.post")
    async def test_raises_ldp_error_on_timeout(self, mock_post, mock_get_token, mock_settings):
        mock_settings.ldp_auth_enabled = True
        mock_get_token.return_value = "test-token"
        mock_post.side_effect = TimeoutError("Connection timed out")

        with pytest.raises(LDPError, match="Request timed out"):
            await call_ldp_api("http://example.com/api", {"key": "value"})

    @pytest.mark.asyncio
    @patch("agent_leasing.clients.ldp.settings")
    @patch("agent_leasing.clients.ldp.get_ldp_auth_token")
    @patch("aiohttp.ClientSession.post")
    async def test_returns_json_on_success(self, mock_post, mock_get_token, mock_settings):
        mock_settings.ldp_auth_enabled = True
        mock_get_token.return_value = "test-token"

        expected_response = {"records": [{"data": "test"}]}
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = expected_response
        mock_response.__aenter__.return_value = mock_response
        mock_response.__aexit__.return_value = None
        mock_post.return_value = mock_response

        result = await call_ldp_api("http://example.com/api", {"key": "value"})
        assert result == expected_response

    @pytest.mark.asyncio
    @patch("agent_leasing.clients.ldp.settings")
    @patch("agent_leasing.clients.ldp.get_ldp_auth_token")
    @patch("aiohttp.ClientSession.post")
    async def test_returns_json_on_201_status(self, mock_post, mock_get_token, mock_settings):
        mock_settings.ldp_auth_enabled = True
        mock_get_token.return_value = "test-token"

        expected_response = {"created": True}
        mock_response = AsyncMock()
        mock_response.status = 201
        mock_response.json.return_value = expected_response
        mock_response.__aenter__.return_value = mock_response
        mock_response.__aexit__.return_value = None
        mock_post.return_value = mock_response

        result = await call_ldp_api("http://example.com/api", {"key": "value"})
        assert result == expected_response


class TestGetLdpData:
    """Tests for get_ldp_data function."""

    @pytest.mark.asyncio
    @patch("agent_leasing.clients.ldp.call_ldp_api")
    @patch("agent_leasing.clients.ldp.settings")
    async def test_calls_ldp_api_with_correct_params(self, mock_settings, mock_call_ldp_api):
        mock_settings.ldp_rp_api_url = "http://ldp.example.com"
        mock_call_ldp_api.return_value = {"records": []}

        await get_ldp_data("12345")

        mock_call_ldp_api.assert_called_once()
        call_args = mock_call_ldp_api.call_args
        assert call_args[0][0] == "http://ldp.example.com/renter-read"
        assert call_args[1]["data"]["dataset_id"] == "lz_renter_data_hub"
        assert call_args[1]["data"]["table_name"] == "property_info"
        assert call_args[1]["data"]["filters"]["and"][0]["value"] == "12345"

    @pytest.mark.asyncio
    @patch("agent_leasing.clients.ldp.call_ldp_api")
    @patch("agent_leasing.clients.ldp.settings")
    async def test_returns_ldp_api_response(self, mock_settings, mock_call_ldp_api):
        mock_settings.ldp_rp_api_url = "http://ldp.example.com"
        expected_response = {"records": [{"property_id": "12345", "data": "test"}]}
        mock_call_ldp_api.return_value = expected_response

        result = await get_ldp_data("12345")
        assert result == expected_response


class TestLDPError:
    """Tests for LDPError exception class."""

    def test_ldp_error_is_exception(self):
        error = LDPError("test message")
        assert isinstance(error, Exception)
        assert str(error) == "test message"

    def test_ldp_error_can_chain_exceptions(self):
        original = ValueError("original error")
        error = LDPError("wrapped error")
        error.__cause__ = original
        assert error.__cause__ is original


class TestParseEnabledModulesWithPte:
    def test_returns_none_false_for_empty_response(self):
        enabled, pte = _parse_enabled_modules_with_pte({})
        assert enabled is None
        assert pte is False

    def test_returns_none_false_when_records_missing(self):
        enabled, pte = _parse_enabled_modules_with_pte({"foo": "bar"})
        assert enabled is None
        assert pte is False

    def test_returns_none_false_when_records_empty(self):
        enabled, pte = _parse_enabled_modules_with_pte({"records": []})
        assert enabled is None
        assert pte is False

    def test_returns_none_false_when_loft_living_missing(self):
        response = {"records": [{"extras": {}}]}
        enabled, pte = _parse_enabled_modules_with_pte(response)
        assert enabled is None
        assert pte is False

    def test_defaults_permission_to_enter_to_false(self):
        response = {"records": [{"extras": {"loftLiving": {"modules": ["MR", "EVENTS"]}}}]}
        enabled, pte = _parse_enabled_modules_with_pte(response)
        assert enabled == ["MR", "EVENTS"]
        assert pte is False

    def test_parses_modules_and_permission_to_enter(self):
        response = {
            "records": [
                {
                    "extras": {
                        "loftLiving": {
                            "modules": ["PAYMENT_CENTER", "PACKAGES"],
                            "permissionToEnter": True,
                        }
                    }
                }
            ]
        }
        enabled, pte = _parse_enabled_modules_with_pte(response)
        assert enabled == ["PAYMENT_CENTER", "PACKAGES"]
        assert pte is True

    def test_returns_modules_value_as_is(self):
        response = {
            "records": [
                {
                    "extras": {
                        "loftLiving": {
                            "modules": "PAYMENT_CENTER",
                            "permissionToEnter": False,
                        }
                    }
                }
            ]
        }
        enabled, pte = _parse_enabled_modules_with_pte(response)
        assert enabled == "PAYMENT_CENTER"
        assert pte is False


class TestParseResidentSummary:
    def test_returns_summary_from_valid_response(self):
        response = {"records": [{"resident_summary": "A nice property with amenities."}]}
        assert _parse_resident_summary(response) == "A nice property with amenities."

    def test_returns_none_for_empty_response(self):
        assert _parse_resident_summary({}) is None

    def test_returns_none_for_none_response(self):
        assert _parse_resident_summary(None) is None

    def test_returns_none_when_records_empty(self):
        assert _parse_resident_summary({"records": []}) is None

    def test_returns_none_when_field_missing(self):
        assert _parse_resident_summary({"records": [{"extras": {}}]}) is None


class TestFetchLdpPropertyData:
    @pytest.mark.asyncio
    @patch("agent_leasing.clients.ldp.get_ldp_data")
    async def test_returns_correct_structure(self, mock_get_ldp_data):
        mock_get_ldp_data.return_value = {
            "records": [
                {
                    "resident_summary": "Property summary",
                    "extras": {
                        "loftLiving": {
                            "modules": ["MR", "EVENTS"],
                            "permissionToEnter": True,
                        }
                    },
                }
            ]
        }

        result = await fetch_ldp_property_data.__wrapped__("12345")
        assert result["enabled_modules"] == ["MR", "EVENTS"]
        assert result["pte_setting"] is True
        assert result["resident_summary"] == "Property summary"

    @pytest.mark.asyncio
    @patch("agent_leasing.clients.ldp.get_ldp_data")
    async def test_raises_on_no_modules(self, mock_get_ldp_data):
        mock_get_ldp_data.return_value = {"records": [{"extras": {}}]}

        with pytest.raises(LDPError, match="No modules in LDP response"):
            await fetch_ldp_property_data.__wrapped__("12345")

    @pytest.mark.asyncio
    @patch("agent_leasing.clients.ldp.get_ldp_data")
    async def test_returns_none_resident_summary_when_missing(self, mock_get_ldp_data):
        mock_get_ldp_data.return_value = {
            "records": [
                {
                    "extras": {
                        "loftLiving": {
                            "modules": ["MR"],
                            "permissionToEnter": False,
                        }
                    }
                }
            ]
        }

        result = await fetch_ldp_property_data.__wrapped__("12345")
        assert result["enabled_modules"] == ["MR"]
        assert result["resident_summary"] is None


class TestGetAvailableServices:
    """Tests for get_available_services function."""

    def test_no_disabled_modules_returns_all_services(self):
        result = get_available_services([])
        assert result == ["maintenance", "billing", "packages", "guest parking passes", "community events"]

    def test_some_disabled_modules(self):
        result = get_available_services(["EVENTS", "PACKAGES"])
        assert result == ["maintenance", "billing", "guest parking passes"]

    def test_single_module_enabled(self):
        result = get_available_services(["PAYMENT_CENTER", "PARKING_PASS", "PACKAGES", "EVENTS"])
        assert result == ["maintenance"]

    def test_all_modules_disabled_returns_empty_list(self):
        result = get_available_services(["PAYMENT_CENTER", "PARKING_PASS", "PACKAGES", "EVENTS", "MR"])
        assert result == []

    def test_preserves_module_order(self):
        result = get_available_services(["PACKAGES"])
        assert result == ["maintenance", "billing", "guest parking passes", "community events"]

    def test_two_modules_enabled(self):
        result = get_available_services(["PARKING_PASS", "PACKAGES", "EVENTS"])
        assert result == ["maintenance", "billing"]


class TestModuleSessionLifecycle:
    """Cover lazy init, caching, and shutdown of the module-level aiohttp session."""

    def test_get_session_creates_and_caches(self, monkeypatch):
        live_loop = MagicMock()
        live_loop.is_closed.return_value = False
        mock_session_a = AsyncMock()
        mock_session_a._loop = live_loop
        mock_session_b = AsyncMock()
        mock_session_b._loop = live_loop
        constructor = MagicMock(side_effect=[mock_session_a, mock_session_b])
        monkeypatch.setattr("agent_leasing.clients.ldp.aiohttp.ClientSession", constructor)

        first = ldp_module._get_session()
        second = ldp_module._get_session()

        assert first is mock_session_a
        assert second is mock_session_a
        assert constructor.call_count == 1

    @pytest.mark.asyncio
    async def test_close_is_no_op_when_session_not_initialized(self):
        assert ldp_module._http_session is None
        await ldp_module.close()
        assert ldp_module._http_session is None

    @pytest.mark.asyncio
    async def test_close_closes_and_clears_session(self, monkeypatch):
        mock_session = AsyncMock()
        monkeypatch.setattr("agent_leasing.clients.ldp.aiohttp.ClientSession", lambda **kw: mock_session)
        ldp_module._get_session()

        await ldp_module.close()

        mock_session.close.assert_awaited_once()
        assert ldp_module._http_session is None

    @pytest.mark.asyncio
    async def test_close_clears_session_even_when_close_raises(self, monkeypatch):
        mock_session = AsyncMock()
        mock_session.close = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr("agent_leasing.clients.ldp.aiohttp.ClientSession", lambda **kw: mock_session)
        ldp_module._get_session()

        with pytest.raises(RuntimeError, match="boom"):
            await ldp_module.close()

        assert ldp_module._http_session is None

    def test_get_session_replaces_stale_session_bound_to_closed_loop(self, monkeypatch):
        """When the cached session's loop is closed (e.g. across tests), create a fresh one."""
        closed_loop = MagicMock()
        closed_loop.is_closed.return_value = True
        stale_session = AsyncMock()
        stale_session._loop = closed_loop

        fresh_session = AsyncMock()
        constructor = MagicMock(return_value=fresh_session)
        monkeypatch.setattr("agent_leasing.clients.ldp.aiohttp.ClientSession", constructor)

        ldp_module._http_session = stale_session

        result = ldp_module._get_session()

        assert result is fresh_session
        assert ldp_module._http_session is fresh_session
        constructor.assert_called_once()
