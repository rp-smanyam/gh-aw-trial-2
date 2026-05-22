"""Unit tests for agent_leasing.api.util module."""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from agent_leasing.api.util import (
    build_full_url,
    execute_api_request,
    perform_api_call,
    prepare_request_headers,
    prepare_request_parameters,
)


class TestBuildFullUrl:
    def test_simple_url(self):
        result = build_full_url("https://api.example.com", "/v1/users")
        assert result == "https://api.example.com/v1/users"

    def test_host_with_trailing_slash(self):
        result = build_full_url("https://api.example.com/", "/v1/users")
        assert result == "https://api.example.com/v1/users"

    def test_path_params_substituted(self):
        result = build_full_url(
            "https://api.example.com",
            "/v1/users/{user_id}/orders/{order_id}",
            path_params={"user_id": "123", "order_id": "456"},
        )
        assert result == "https://api.example.com/v1/users/123/orders/456"

    def test_path_params_none(self):
        result = build_full_url(
            "https://api.example.com",
            "/v1/users/{user_id}",
            path_params=None,
        )
        # No substitution, format placeholders remain
        assert "{user_id}" in result

    def test_path_params_empty_dict(self):
        result = build_full_url(
            "https://api.example.com",
            "/v1/users",
            path_params={},
        )
        assert result == "https://api.example.com/v1/users"

    def test_single_path_param(self):
        result = build_full_url(
            "https://api.example.com",
            "/v1/properties/{property_id}",
            path_params={"property_id": "99"},
        )
        assert result == "https://api.example.com/v1/properties/99"

    def test_host_with_base_path(self):
        result = build_full_url("https://api.example.com/base/", "endpoint")
        assert result == "https://api.example.com/base/endpoint"

    def test_missing_path_param_raises(self):
        with pytest.raises(KeyError):
            build_full_url(
                "https://api.example.com",
                "/v1/users/{user_id}",
                path_params={"wrong_key": "123"},
            )


class TestPrepareRequestHeaders:
    @patch("agent_leasing.api.util.get_auth_token", new_callable=AsyncMock)
    async def test_happy_path(self, mock_get_auth_token):
        mock_get_auth_token.return_value = "test-token-abc"

        headers = await prepare_request_headers("loft_mcp")

        assert headers["Content-Type"] == "application/json"
        assert headers["Authorization"] == "Bearer test-token-abc"
        mock_get_auth_token.assert_awaited_once_with("loft_mcp")

    @patch("agent_leasing.api.util.get_auth_token", new_callable=AsyncMock)
    async def test_extra_headers_merged(self, mock_get_auth_token):
        mock_get_auth_token.return_value = "token"

        headers = await prepare_request_headers(
            "loft_mcp",
            extra_headers={"X-Custom": "value", "X-Request-Id": "req-123"},
        )

        assert headers["X-Custom"] == "value"
        assert headers["X-Request-Id"] == "req-123"
        assert headers["Content-Type"] == "application/json"
        assert headers["Authorization"] == "Bearer token"

    @patch("agent_leasing.api.util.get_auth_token", new_callable=AsyncMock)
    async def test_extra_headers_override_defaults(self, mock_get_auth_token):
        mock_get_auth_token.return_value = "token"

        headers = await prepare_request_headers(
            "loft_mcp",
            extra_headers={"Content-Type": "text/xml"},
        )

        assert headers["Content-Type"] == "text/xml"

    @patch("agent_leasing.api.util.get_auth_token", new_callable=AsyncMock)
    async def test_auth_token_failure_logs_and_continues(self, mock_get_auth_token):
        mock_get_auth_token.side_effect = ValueError("Auth is not enabled for test_server")

        headers = await prepare_request_headers("test_server")

        assert headers["Content-Type"] == "application/json"
        assert "Authorization" not in headers

    @patch("agent_leasing.api.util.get_auth_token", new_callable=AsyncMock)
    async def test_auth_token_failure_with_extra_headers(self, mock_get_auth_token):
        mock_get_auth_token.side_effect = Exception("Network error")

        headers = await prepare_request_headers(
            "test_server",
            extra_headers={"X-Trace": "abc"},
        )

        assert "Authorization" not in headers
        assert headers["X-Trace"] == "abc"
        assert headers["Content-Type"] == "application/json"

    @patch("agent_leasing.api.util.get_auth_token", new_callable=AsyncMock)
    async def test_extra_headers_none(self, mock_get_auth_token):
        mock_get_auth_token.return_value = "token"

        headers = await prepare_request_headers("loft_mcp", extra_headers=None)

        assert len(headers) == 2  # Content-Type + Authorization
        assert headers["Content-Type"] == "application/json"
        assert headers["Authorization"] == "Bearer token"


class TestPrepareRequestParameters:
    def test_defaults_no_payload_no_query_params(self):
        result = prepare_request_parameters()
        assert result == {"headers": {}}

    def test_headers_passed_through(self):
        headers = {"Content-Type": "application/json", "Authorization": "Bearer tok"}
        result = prepare_request_parameters(headers=headers)
        assert result["headers"] is headers

    def test_none_headers_replaced_with_empty_dict(self):
        result = prepare_request_parameters(headers=None)
        assert result["headers"] == {}

    def test_json_payload_with_json_content_type(self):
        headers = {"Content-Type": "application/json"}
        payload = {"key": "value", "count": 42}

        result = prepare_request_parameters(headers=headers, payload=payload)

        assert result["json"] == payload
        assert "data" not in result

    def test_json_payload_with_json_charset_content_type(self):
        headers = {"Content-Type": "application/json; charset=utf-8"}
        payload = {"key": "value"}

        result = prepare_request_parameters(headers=headers, payload=payload)

        assert result["json"] == payload
        assert "data" not in result

    def test_data_payload_with_non_json_content_type(self):
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        payload = {"field": "value"}

        result = prepare_request_parameters(headers=headers, payload=payload)

        assert result["data"] == payload
        assert "json" not in result

    def test_data_payload_with_no_content_type(self):
        headers = {}
        payload = {"field": "value"}

        result = prepare_request_parameters(headers=headers, payload=payload)

        assert result["data"] == payload
        assert "json" not in result

    def test_no_payload_means_no_json_or_data_key(self):
        headers = {"Content-Type": "application/json"}

        result = prepare_request_parameters(headers=headers, payload=None)

        assert "json" not in result
        assert "data" not in result

    def test_query_params_included(self):
        result = prepare_request_parameters(
            headers={"Content-Type": "application/json"},
            query_params={"page": "1", "limit": "10"},
        )

        assert result["params"] == {"page": "1", "limit": "10"}

    def test_query_params_none_omitted(self):
        result = prepare_request_parameters(
            headers={"Content-Type": "application/json"},
            query_params=None,
        )

        assert "params" not in result

    def test_query_params_empty_dict_included(self):
        """An empty dict is not None, so it should still be included."""
        result = prepare_request_parameters(
            headers={"Content-Type": "application/json"},
            query_params={},
        )

        assert result["params"] == {}

    def test_all_params_combined(self):
        headers = {"Content-Type": "application/json", "Authorization": "Bearer tok"}
        payload = {"name": "test"}
        query_params = {"expand": "true"}

        result = prepare_request_parameters(headers=headers, payload=payload, query_params=query_params)

        assert result["headers"] is headers
        assert result["json"] == payload
        assert result["params"] == query_params
        assert "data" not in result

    def test_content_type_case_insensitive(self):
        headers = {"Content-Type": "Application/JSON"}
        payload = {"key": "value"}

        result = prepare_request_parameters(headers=headers, payload=payload)

        assert result["json"] == payload


class _FakeResponse:
    """Lightweight fake for aiohttp response used as async context manager."""

    def __init__(self, *, status, json_data=None, text_data="", reason="OK", headers=None):
        self.status = status
        self._json_data = json_data
        self._text_data = text_data
        self.reason = reason
        self.headers = headers or {}
        self.request_info = MagicMock()
        self.history = ()

    async def json(self):
        return self._json_data

    async def text(self):
        return self._text_data


class _FakeRequestCM:
    """Async context manager returned by session.request()."""

    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *args):
        pass


class _FakeSessionCM:
    """Async context manager returned by aiohttp.ClientSession()."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        pass


class _FakeSession:
    """Fake aiohttp session that records calls and returns a fake response."""

    def __init__(self, response):
        self._response = response
        self.request_calls = []

    def request(self, method, url, **kwargs):
        self.request_calls.append((method, url, kwargs))
        return _FakeRequestCM(self._response)


class TestExecuteApiRequest:
    def _patch_session(self, monkeypatch, response):
        """Monkeypatch aiohttp.ClientSession to return a fake session."""
        fake_session = _FakeSession(response)
        recorded_init_kwargs = {}

        def fake_client_session(**kwargs):
            recorded_init_kwargs.update(kwargs)
            return _FakeSessionCM(fake_session)

        monkeypatch.setattr("agent_leasing.api.util.aiohttp.ClientSession", fake_client_session)
        return fake_session, recorded_init_kwargs

    @pytest.mark.asyncio
    async def test_successful_json_response(self, monkeypatch):
        resp = _FakeResponse(status=200, json_data={"id": 1, "name": "test"}, text_data='{"id": 1}')
        session, _ = self._patch_session(monkeypatch, resp)

        result = await execute_api_request(
            url="https://api.example.com/v1/users",
            method="GET",
            request_kwargs={"headers": {"Authorization": "Bearer tok"}},
        )

        assert result == {"id": 1, "name": "test"}
        assert session.request_calls[0] == (
            "GET",
            "https://api.example.com/v1/users",
            {"headers": {"Authorization": "Bearer tok"}},
        )

    @pytest.mark.asyncio
    async def test_204_returns_none(self, monkeypatch):
        resp = _FakeResponse(status=204, reason="No Content")
        self._patch_session(monkeypatch, resp)

        result = await execute_api_request(
            url="https://api.example.com/v1/users/1",
            method="DELETE",
            request_kwargs={"headers": {}},
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_400_raises_client_response_error(self, monkeypatch):
        resp = _FakeResponse(status=400, text_data='{"error": "Bad Request"}', reason="Bad Request")
        self._patch_session(monkeypatch, resp)

        with pytest.raises(aiohttp.ClientResponseError):
            await execute_api_request(
                url="https://api.example.com/v1/users",
                method="POST",
                request_kwargs={"headers": {}, "json": {"bad": "data"}},
            )

    @pytest.mark.asyncio
    async def test_500_raises_client_response_error(self, monkeypatch):
        resp = _FakeResponse(status=500, text_data="Internal Server Error", reason="Internal Server Error")
        self._patch_session(monkeypatch, resp)

        with pytest.raises(aiohttp.ClientResponseError):
            await execute_api_request(
                url="https://api.example.com/v1/endpoint",
                method="GET",
                request_kwargs={"headers": {}},
            )

    @pytest.mark.asyncio
    async def test_custom_timeout(self, monkeypatch):
        resp = _FakeResponse(status=200, json_data={}, text_data="{}")
        _, init_kwargs = self._patch_session(monkeypatch, resp)

        await execute_api_request(
            url="https://api.example.com/v1/slow",
            method="GET",
            request_kwargs={"headers": {}},
            timeout_seconds=60,
        )

        assert init_kwargs["timeout"].total == 60

    @pytest.mark.asyncio
    async def test_default_timeout_300(self, monkeypatch):
        resp = _FakeResponse(status=200, json_data={}, text_data="{}")
        _, init_kwargs = self._patch_session(monkeypatch, resp)

        await execute_api_request(
            url="https://api.example.com/v1/default",
            method="GET",
            request_kwargs={"headers": {}},
        )

        assert init_kwargs["timeout"].total == 300

    @pytest.mark.asyncio
    async def test_request_kwargs_spread_into_request(self, monkeypatch):
        resp = _FakeResponse(status=200, json_data={}, text_data="{}")
        session, _ = self._patch_session(monkeypatch, resp)

        request_kwargs = {
            "headers": {"Content-Type": "application/json"},
            "json": {"name": "new_user"},
            "params": {"notify": "true"},
        }

        await execute_api_request(
            url="https://api.example.com/v1/users",
            method="POST",
            request_kwargs=request_kwargs,
        )

        assert session.request_calls[0] == (
            "POST",
            "https://api.example.com/v1/users",
            {
                "headers": {"Content-Type": "application/json"},
                "json": {"name": "new_user"},
                "params": {"notify": "true"},
            },
        )


class TestPerformApiCall:
    """Integration-style tests for the orchestrator function."""

    @patch("agent_leasing.api.util.execute_api_request", new_callable=AsyncMock)
    @patch("agent_leasing.api.util.get_auth_token", new_callable=AsyncMock)
    async def test_happy_path_get(self, mock_get_auth_token, mock_execute):
        mock_get_auth_token.return_value = "my-token"
        mock_execute.return_value = {"users": []}

        result = await perform_api_call(
            host="https://api.example.com",
            endpoint="/v1/users",
            method="GET",
            auth_server="loft_mcp",
        )

        assert result == {"users": []}

        # execute_api_request(url, method, request_kwargs, timeout_seconds=...)
        args, kwargs = mock_execute.call_args
        assert args[0] == "https://api.example.com/v1/users"
        assert args[1] == "GET"
        assert kwargs["timeout_seconds"] == 300
        request_kwargs = args[2]
        assert request_kwargs["headers"]["Authorization"] == "Bearer my-token"
        assert request_kwargs["headers"]["Content-Type"] == "application/json"

    @patch("agent_leasing.api.util.execute_api_request", new_callable=AsyncMock)
    @patch("agent_leasing.api.util.get_auth_token", new_callable=AsyncMock)
    async def test_post_with_payload(self, mock_get_auth_token, mock_execute):
        mock_get_auth_token.return_value = "token"
        mock_execute.return_value = {"id": 42}

        result = await perform_api_call(
            host="https://api.example.com",
            endpoint="/v1/users",
            method="POST",
            auth_server="knock_mcp",
            payload={"name": "Alice", "email": "alice@example.com"},
        )

        assert result == {"id": 42}
        request_kwargs = mock_execute.call_args[0][2]  # 3rd positional arg
        assert request_kwargs["json"] == {"name": "Alice", "email": "alice@example.com"}

    @patch("agent_leasing.api.util.execute_api_request", new_callable=AsyncMock)
    @patch("agent_leasing.api.util.get_auth_token", new_callable=AsyncMock)
    async def test_with_path_params(self, mock_get_auth_token, mock_execute):
        mock_get_auth_token.return_value = "token"
        mock_execute.return_value = {"name": "Bob"}

        await perform_api_call(
            host="https://api.example.com",
            endpoint="/v1/users/{user_id}",
            method="GET",
            auth_server="loft_mcp",
            path_params={"user_id": "55"},
        )

        assert mock_execute.call_args[0][0] == "https://api.example.com/v1/users/55"

    @patch("agent_leasing.api.util.execute_api_request", new_callable=AsyncMock)
    @patch("agent_leasing.api.util.get_auth_token", new_callable=AsyncMock)
    async def test_with_query_params(self, mock_get_auth_token, mock_execute):
        mock_get_auth_token.return_value = "token"
        mock_execute.return_value = {"results": []}

        await perform_api_call(
            host="https://api.example.com",
            endpoint="/v1/search",
            method="GET",
            auth_server="loft_mcp",
            query_params={"q": "hello", "page": "1"},
        )

        request_kwargs = mock_execute.call_args[0][2]
        assert request_kwargs["params"] == {"q": "hello", "page": "1"}

    @patch("agent_leasing.api.util.execute_api_request", new_callable=AsyncMock)
    @patch("agent_leasing.api.util.get_auth_token", new_callable=AsyncMock)
    async def test_custom_timeout(self, mock_get_auth_token, mock_execute):
        mock_get_auth_token.return_value = "token"
        mock_execute.return_value = {}

        await perform_api_call(
            host="https://api.example.com",
            endpoint="/v1/slow",
            method="GET",
            auth_server="loft_mcp",
            timeout_seconds=10,
        )

        assert mock_execute.call_args[1]["timeout_seconds"] == 10  # keyword arg

    @patch("agent_leasing.api.util.execute_api_request", new_callable=AsyncMock)
    @patch("agent_leasing.api.util.get_auth_token", new_callable=AsyncMock)
    async def test_extra_headers_forwarded(self, mock_get_auth_token, mock_execute):
        mock_get_auth_token.return_value = "token"
        mock_execute.return_value = {}

        await perform_api_call(
            host="https://api.example.com",
            endpoint="/v1/endpoint",
            method="GET",
            auth_server="loft_mcp",
            extra_headers={"X-Request-Id": "req-999"},
        )

        request_kwargs = mock_execute.call_args[0][2]
        assert request_kwargs["headers"]["X-Request-Id"] == "req-999"

    @patch("agent_leasing.api.util.execute_api_request", new_callable=AsyncMock)
    @patch("agent_leasing.api.util.get_auth_token", new_callable=AsyncMock)
    async def test_auth_failure_still_makes_request(self, mock_get_auth_token, mock_execute):
        """When auth token fails, the request proceeds without Authorization header."""
        mock_get_auth_token.side_effect = RuntimeError("Auth down")
        mock_execute.return_value = {"public": "data"}

        result = await perform_api_call(
            host="https://api.example.com",
            endpoint="/v1/public",
            method="GET",
            auth_server="broken_server",
        )

        assert result == {"public": "data"}
        request_kwargs = mock_execute.call_args[0][2]
        assert "Authorization" not in request_kwargs["headers"]

    @patch("agent_leasing.api.util.execute_api_request", new_callable=AsyncMock)
    @patch("agent_leasing.api.util.get_auth_token", new_callable=AsyncMock)
    async def test_execute_raises_propagates(self, mock_get_auth_token, mock_execute):
        """Errors from execute_api_request should propagate to the caller."""
        mock_get_auth_token.return_value = "token"
        mock_execute.side_effect = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=404,
            message="Not Found",
        )

        with pytest.raises(aiohttp.ClientResponseError):
            await perform_api_call(
                host="https://api.example.com",
                endpoint="/v1/missing",
                method="GET",
                auth_server="loft_mcp",
            )

    @patch("agent_leasing.api.util.execute_api_request", new_callable=AsyncMock)
    @patch("agent_leasing.api.util.get_auth_token", new_callable=AsyncMock)
    async def test_all_params_combined(self, mock_get_auth_token, mock_execute):
        mock_get_auth_token.return_value = "combined-token"
        mock_execute.return_value = {"created": True}

        result = await perform_api_call(
            host="https://api.example.com",
            endpoint="/v1/properties/{prop_id}/units",
            method="POST",
            auth_server="one_site_mcp",
            payload={"unit_number": "101"},
            path_params={"prop_id": "77"},
            query_params={"validate": "true"},
            timeout_seconds=30,
            extra_headers={"X-Idempotency-Key": "abc-123"},
        )

        assert result == {"created": True}

        args, kwargs = mock_execute.call_args
        assert args[0] == "https://api.example.com/v1/properties/77/units"
        assert args[1] == "POST"
        assert kwargs["timeout_seconds"] == 30

        rk = args[2]
        assert rk["headers"]["Authorization"] == "Bearer combined-token"
        assert rk["headers"]["X-Idempotency-Key"] == "abc-123"
        assert rk["json"] == {"unit_number": "101"}
        assert rk["params"] == {"validate": "true"}
