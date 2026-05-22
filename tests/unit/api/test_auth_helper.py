"""Tests for agent_leasing.api.auth.auth_helper — get_auth_token and convenience wrappers."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agent_leasing.api.auth import auth_helper
from agent_leasing.api.auth.auth_helper import (
    TOKEN_EXPIRY_BUFFER,
    get_auth_token,
    get_books_auth_token,
    get_facilities_mcp_auth_token,
    get_knock_mcp_auth_token,
    get_ldp_auth_token,
    get_loft_mcp_auth_token,
    get_onsite_mcp_auth_token,
)


@pytest.fixture(autouse=True)
def clear_auth_cache():
    """Ensure module-level caches are clean before and after every test."""
    auth_helper._auth_cache.clear()
    yield
    auth_helper._auth_cache.clear()


def _make_auth_settings(*, auth_enabled=True, scope="test-scope"):
    """Build a minimal AUTH_SETTINGS entry for testing."""
    return {
        "auth_enabled": auth_enabled,
        "token_endpoint": "https://auth.example.com/token",
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "scope": scope,
    }


def _mock_httpx_response(json_body: dict, status_code: int = 200):
    """Return a mock httpx.Response with the given JSON payload."""
    resp = MagicMock(spec=httpx.Response)
    resp.json.return_value = json_body
    resp.status_code = status_code
    return resp


# ---------------------------------------------------------------------------
# get_auth_token
# ---------------------------------------------------------------------------
class TestGetAuthToken:
    """Core tests for get_auth_token(auth_server)."""

    async def test_fetches_new_token_and_caches_it(self):
        """Happy path: fetches a token via HTTP, returns it, and caches it."""
        settings_patch = {"test_server": _make_auth_settings()}
        mock_response = _mock_httpx_response({"access_token": "tok-abc", "expires_in": 3600})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_response

        with (
            patch.object(auth_helper, "AUTH_SETTINGS", settings_patch),
            patch.object(auth_helper, "_http_client", mock_client),
        ):
            token = await get_auth_token("test_server")

        assert token == "tok-abc"
        # Verify the token was cached
        assert "test_server" in auth_helper._auth_cache
        cached_token, _ = auth_helper._auth_cache["test_server"]
        assert cached_token == "tok-abc"

    async def test_returns_cached_token_when_not_expired(self):
        """If a cached token exists and is not expired, return it without HTTP call."""
        future_expiry = time.time() + 9999
        auth_helper._auth_cache["cached_server"] = ("cached-tok", future_expiry)

        settings_patch = {"cached_server": _make_auth_settings()}

        with patch.object(auth_helper, "AUTH_SETTINGS", settings_patch):
            token = await get_auth_token("cached_server")

        assert token == "cached-tok"

    async def test_refetches_token_when_expired(self):
        """If cached token is expired, fetch a new one."""
        past_expiry = time.time() - 100
        auth_helper._auth_cache["expired_server"] = ("old-tok", past_expiry)

        settings_patch = {"expired_server": _make_auth_settings()}
        mock_response = _mock_httpx_response({"access_token": "new-tok", "expires_in": 7200})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_response

        with (
            patch.object(auth_helper, "AUTH_SETTINGS", settings_patch),
            patch.object(auth_helper, "_http_client", mock_client),
        ):
            token = await get_auth_token("expired_server")

        assert token == "new-tok"

    async def test_raises_for_unknown_auth_server(self):
        """Unknown auth_server key raises ValueError."""
        with pytest.raises(ValueError, match="Unknown auth server: nonexistent"):
            await get_auth_token("nonexistent")

    async def test_raises_when_auth_not_enabled(self):
        """Auth disabled for server raises ValueError."""
        settings_patch = {"disabled_server": _make_auth_settings(auth_enabled=False)}

        with (
            patch.object(auth_helper, "AUTH_SETTINGS", settings_patch),
            pytest.raises(ValueError, match="Auth is not enabled for disabled_server"),
        ):
            await get_auth_token("disabled_server")

    async def test_raises_for_missing_required_settings(self):
        """Missing client_id / client_secret / token_endpoint raises ValueError."""
        incomplete = {
            "auth_enabled": True,
            "token_endpoint": "",
            "client_id": "some-id",
            "client_secret": "",
            "scope": "",
        }
        settings_patch = {"incomplete_server": incomplete}

        with (
            patch.object(auth_helper, "AUTH_SETTINGS", settings_patch),
            pytest.raises(ValueError, match="Missing required auth settings"),
        ):
            await get_auth_token("incomplete_server")

    async def test_raises_when_no_access_token_in_response(self):
        """Response without access_token raises ValueError."""
        settings_patch = {"bad_resp_server": _make_auth_settings()}
        mock_response = _mock_httpx_response({"error": "invalid_client"}, status_code=401)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_response

        with (
            patch.object(auth_helper, "AUTH_SETTINGS", settings_patch),
            patch.object(auth_helper, "_http_client", mock_client),
            pytest.raises(ValueError, match="No access token in response"),
        ):
            await get_auth_token("bad_resp_server")

    async def test_retries_once_on_transient_failure(self):
        """First POST fails, retry succeeds — token is returned."""
        settings_patch = {"retry_server": _make_auth_settings()}
        fail_response = _mock_httpx_response({"error": "server_error"}, status_code=500)
        ok_response = _mock_httpx_response({"access_token": "retry-tok", "expires_in": 3600})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = [fail_response, ok_response]

        with (
            patch.object(auth_helper, "AUTH_SETTINGS", settings_patch),
            patch.object(auth_helper, "_http_client", mock_client),
            patch("agent_leasing.api.auth.auth_helper.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            token = await get_auth_token("retry_server")

        assert token == "retry-tok"
        assert mock_client.post.call_count == 2
        mock_sleep.assert_awaited_once_with(0.5)

    async def test_raises_after_retry_exhausted(self):
        """Both attempts fail — exception is raised."""
        settings_patch = {"fail_server": _make_auth_settings()}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = httpx.ConnectTimeout("timeout")

        with (
            patch.object(auth_helper, "AUTH_SETTINGS", settings_patch),
            patch.object(auth_helper, "_http_client", mock_client),
            patch("agent_leasing.api.auth.auth_helper.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(httpx.ConnectTimeout),
        ):
            await get_auth_token("fail_server")

        assert mock_client.post.call_count == 2

    async def test_concurrent_requests_share_cache(self):
        """Concurrent callers both get valid tokens and cache is populated."""
        settings_patch = {"race_server": _make_auth_settings()}
        mock_response = _mock_httpx_response({"access_token": "first-tok", "expires_in": 3600})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_response

        call_count = 0

        async def counting_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return mock_response

        mock_client.post = counting_post

        with (
            patch.object(auth_helper, "AUTH_SETTINGS", settings_patch),
            patch.object(auth_helper, "_http_client", mock_client),
        ):
            # Run two concurrent calls
            results = await asyncio.gather(
                get_auth_token("race_server"),
                get_auth_token("race_server"),
            )

        # Both should return valid tokens
        assert results[0] == "first-tok"
        assert results[1] == "first-tok"
        # Without locks, both may fetch (up to 2 HTTP calls), but cache should be populated
        assert call_count <= 2
        assert "race_server" in auth_helper._auth_cache

    async def test_expiry_uses_buffer(self):
        """Token expiry in cache accounts for TOKEN_EXPIRY_BUFFER."""
        settings_patch = {"buf_server": _make_auth_settings()}
        expires_in = 3600
        mock_response = _mock_httpx_response({"access_token": "buf-tok", "expires_in": expires_in})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_response

        before = time.time()

        with (
            patch.object(auth_helper, "AUTH_SETTINGS", settings_patch),
            patch.object(auth_helper, "_http_client", mock_client),
        ):
            await get_auth_token("buf_server")

        after = time.time()

        _, cached_expiry = auth_helper._auth_cache["buf_server"]
        # expiry_time = current_time + expires_in - TOKEN_EXPIRY_BUFFER
        assert cached_expiry >= before + expires_in - TOKEN_EXPIRY_BUFFER
        assert cached_expiry <= after + expires_in - TOKEN_EXPIRY_BUFFER

    async def test_default_expires_in_when_not_in_response(self):
        """If expires_in is absent from response, default to 3600."""
        settings_patch = {"default_exp_server": _make_auth_settings()}
        mock_response = _mock_httpx_response({"access_token": "def-tok"})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_response

        before = time.time()

        with (
            patch.object(auth_helper, "AUTH_SETTINGS", settings_patch),
            patch.object(auth_helper, "_http_client", mock_client),
        ):
            await get_auth_token("default_exp_server")

        _, cached_expiry = auth_helper._auth_cache["default_exp_server"]
        # Should have used 3600 as default expires_in
        assert cached_expiry >= before + 3600 - TOKEN_EXPIRY_BUFFER

    async def test_sends_correct_post_payload(self):
        """Verify the POST body contains grant_type, client_id, client_secret, scope."""
        settings_patch = {"payload_server": _make_auth_settings(scope="my-scope")}
        mock_response = _mock_httpx_response({"access_token": "x", "expires_in": 100})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_response

        with (
            patch.object(auth_helper, "AUTH_SETTINGS", settings_patch),
            patch.object(auth_helper, "_http_client", mock_client),
        ):
            await get_auth_token("payload_server")

        mock_client.post.assert_called_once_with(
            "https://auth.example.com/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
                "scope": "my-scope",
            },
        )


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------
class TestGetFacilitiesMcpAuthToken:
    async def test_delegates_to_get_auth_token(self):
        with patch.object(auth_helper, "get_auth_token", new_callable=AsyncMock, return_value="fac-tok") as mock:
            result = await get_facilities_mcp_auth_token(context=None)
        assert result == "fac-tok"
        mock.assert_awaited_once_with("facilities")

    async def test_with_context(self, resident_context_chat_ll):
        with patch.object(auth_helper, "get_auth_token", new_callable=AsyncMock, return_value="fac-tok") as mock:
            result = await get_facilities_mcp_auth_token(resident_context_chat_ll)
        assert result == "fac-tok"
        mock.assert_awaited_once_with("facilities")


class TestGetKnockMcpAuthToken:
    async def test_delegates_to_get_auth_token(self):
        with patch.object(auth_helper, "get_auth_token", new_callable=AsyncMock, return_value="knock-tok") as mock:
            result = await get_knock_mcp_auth_token(context=None)
        assert result == "knock-tok"
        mock.assert_awaited_once_with("knock_mcp")

    async def test_with_context(self, resident_context_chat_ll):
        with patch.object(auth_helper, "get_auth_token", new_callable=AsyncMock, return_value="knock-tok") as mock:
            result = await get_knock_mcp_auth_token(resident_context_chat_ll)
        assert result == "knock-tok"
        mock.assert_awaited_once_with("knock_mcp")


class TestGetLoftMcpAuthToken:
    async def test_returns_cidp_token_when_present(self, resident_context_chat_ll):
        """When context has a cidp_token, return it directly without calling get_auth_token."""
        resident_context_chat_ll.ask_request.product_info.uc_consumer_identity_token = MagicMock()
        resident_context_chat_ll.ask_request.product_info.uc_consumer_identity_token.id = "cidp-xyz"

        with patch.object(auth_helper, "get_auth_token", new_callable=AsyncMock) as mock:
            result = await get_loft_mcp_auth_token(resident_context_chat_ll)

        assert result == "cidp-xyz"
        mock.assert_not_awaited()

    async def test_falls_back_to_get_auth_token_when_cidp_none(self, resident_context_chat_ll):
        """When uc_consumer_identity_token is None, fall back to get_auth_token."""
        resident_context_chat_ll.ask_request.product_info.uc_consumer_identity_token = None

        with patch.object(auth_helper, "get_auth_token", new_callable=AsyncMock, return_value="loft-tok") as mock:
            result = await get_loft_mcp_auth_token(resident_context_chat_ll)

        assert result == "loft-tok"
        mock.assert_awaited_once_with("loft_mcp")

    async def test_falls_back_when_cidp_id_is_none(self, resident_context_chat_ll):
        """When uc_consumer_identity_token exists but id is None, fall back."""
        resident_context_chat_ll.ask_request.product_info.uc_consumer_identity_token = MagicMock()
        resident_context_chat_ll.ask_request.product_info.uc_consumer_identity_token.id = None

        with patch.object(auth_helper, "get_auth_token", new_callable=AsyncMock, return_value="loft-tok") as mock:
            result = await get_loft_mcp_auth_token(resident_context_chat_ll)

        assert result == "loft-tok"
        mock.assert_awaited_once_with("loft_mcp")

    async def test_falls_back_when_no_context(self):
        """When context is None, fall back to get_auth_token."""
        with patch.object(auth_helper, "get_auth_token", new_callable=AsyncMock, return_value="loft-tok") as mock:
            result = await get_loft_mcp_auth_token(None)

        assert result == "loft-tok"
        mock.assert_awaited_once_with("loft_mcp")


class TestGetOnsiteMcpAuthToken:
    async def test_delegates_to_get_auth_token(self):
        with patch.object(auth_helper, "get_auth_token", new_callable=AsyncMock, return_value="os-tok") as mock:
            result = await get_onsite_mcp_auth_token(context=None)
        assert result == "os-tok"
        mock.assert_awaited_once_with("one_site_mcp")

    async def test_with_context(self, resident_context_chat_ll):
        with patch.object(auth_helper, "get_auth_token", new_callable=AsyncMock, return_value="os-tok") as mock:
            result = await get_onsite_mcp_auth_token(resident_context_chat_ll)
        assert result == "os-tok"
        mock.assert_awaited_once_with("one_site_mcp")


class TestGetLdpAuthToken:
    async def test_delegates_to_get_auth_token(self):
        with patch.object(auth_helper, "get_auth_token", new_callable=AsyncMock, return_value="ldp-tok") as mock:
            result = await get_ldp_auth_token(context=None)
        assert result == "ldp-tok"
        mock.assert_awaited_once_with("ldp")


class TestGetBooksAuthToken:
    async def test_delegates_to_get_auth_token(self):
        with patch.object(auth_helper, "get_auth_token", new_callable=AsyncMock, return_value="books-tok") as mock:
            result = await get_books_auth_token(context=None)
        assert result == "books-tok"
        mock.assert_awaited_once_with("books")
