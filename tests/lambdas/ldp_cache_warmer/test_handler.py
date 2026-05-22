import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import lambdas.ldp_cache_warmer.handler as handler
from lambdas.ldp_cache_warmer.handler import (
    CACHE_KEY_PREFIX,
    _get_secrets,
    _parse_ldp_response,
    _warm_batch,
    fetch_ldp_auth_token,
    fetch_property_ids,
    producer_handler,
    warm_property,
    worker_handler,
)

_DUMMY_REQUEST = httpx.Request("GET", "https://test.example.com")


def _response(status_code: int, json_data: dict) -> httpx.Response:
    """Create an httpx.Response with a dummy request attached (required for raise_for_status)."""
    return httpx.Response(status_code, json=json_data, request=_DUMMY_REQUEST)


# -- _get_secrets -------------------------------------------------------------


class TestGetSecrets:
    def test_env_var_fallback_without_session_token(self):
        """When AWS_SESSION_TOKEN is absent, reads secrets from env vars."""
        env = {
            "AI_CONFIG_HOST": "https://config.example.com",
            "AI_CONFIG_TOKEN": "cfg_tok",
            "LDP_RP_API_URL": "https://ldp.example.com",
            "LDP_LOGIN_TOKEN_ENDPOINT": "https://auth.example.com/token",
            "LDP_LOGIN_CLIENT_ID": "client_id",
            "LDP_LOGIN_CLIENT_SECRET": "client_secret",
            "LDP_CACHE_TTL": "2h",
            "LDP_CACHE_EARLY_TTL": "1h30m",
            "CHUNK_SIZE": "50",
            "WORKER_CONCURRENCY": "10",
            "LDP_WARM_MAX_RETRIES": "2",
            "LDP_WARM_RETRY_BACKOFF_SECONDS": "1.5",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("AWS_SESSION_TOKEN", None)
            result = _get_secrets()

        assert result == env

    def test_sm_extension_call_with_session_token(self):
        """When AWS_SESSION_TOKEN is present, fetches from SM extension."""
        secret_data = {
            "AI_CONFIG_HOST": "https://config.example.com",
            "AI_CONFIG_TOKEN": "cfg_tok",
            "LDP_RP_API_URL": "https://ldp.example.com",
            "LDP_LOGIN_TOKEN_ENDPOINT": "https://auth.example.com/token",
            "LDP_LOGIN_CLIENT_ID": "client_id",
            "LDP_LOGIN_CLIENT_SECRET": "client_secret",
        }
        sm_response_body = json.dumps({"SecretString": json.dumps(secret_data)}).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = sm_response_body
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        env = {
            "AWS_SESSION_TOKEN": "session_tok",
            "SECRETS_MANAGER_SECRET_ID": "agent-leasing-abc123",
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch("lambdas.ldp_cache_warmer.handler.urllib.request.urlopen", return_value=mock_resp) as mock_urlopen,
        ):
            result = _get_secrets()

        assert result == secret_data
        mock_urlopen.assert_called_once()
        # Verify the URL contains the encoded secret ID
        req_obj = mock_urlopen.call_args[0][0]
        assert "agent-leasing-abc123" in req_obj.full_url

    def test_sm_extension_error_propagates(self):
        """Errors from the SM extension propagate to the caller."""
        env = {
            "AWS_SESSION_TOKEN": "session_tok",
            "SECRETS_MANAGER_SECRET_ID": "agent-leasing-abc123",
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch(
                "lambdas.ldp_cache_warmer.handler.urllib.request.urlopen",
                side_effect=urllib_error("Connection refused"),
            ),
            pytest.raises(Exception, match="Connection refused"),
        ):
            _get_secrets()


def urllib_error(msg: str) -> Exception:
    """Create a URLError-like exception for testing."""
    return OSError(msg)


# -- fetch_property_ids -------------------------------------------------------


class TestFetchPropertyIds:
    @pytest.mark.asyncio
    async def test_success(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_response(200, {"property_data": [100, 200, 300]}))

        result = await fetch_property_ids(client)

        assert result == ["100", "200", "300"]
        client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_response(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_response(200, {"property_data": []}))

        result = await fetch_property_ids(client)

        assert result == []

    @pytest.mark.asyncio
    async def test_missing_key(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_response(200, {}))

        result = await fetch_property_ids(client)

        assert result == []

    @pytest.mark.asyncio
    async def test_api_error(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_response(500, {"error": "internal"}))

        with pytest.raises(httpx.HTTPStatusError):
            await fetch_property_ids(client)


# -- fetch_ldp_auth_token ----------------------------------------------------


class TestFetchLdpAuthToken:
    @pytest.mark.asyncio
    async def test_success(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=_response(200, {"access_token": "tok_123", "expires_in": 3600}))

        with patch("lambdas.ldp_cache_warmer.handler.LDP_LOGIN_TOKEN_ENDPOINT", "https://auth.example.com/token"):
            token = await fetch_ldp_auth_token(client)

        assert token == "tok_123"

    @pytest.mark.asyncio
    async def test_no_access_token(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=_response(200, {"error": "invalid_client"}))

        with patch("lambdas.ldp_cache_warmer.handler.LDP_LOGIN_TOKEN_ENDPOINT", "https://auth.example.com/token"):
            with pytest.raises(RuntimeError, match="No access_token"):
                await fetch_ldp_auth_token(client)

    @pytest.mark.asyncio
    async def test_auth_server_error(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=_response(500, {"error": "server_error"}))

        with patch("lambdas.ldp_cache_warmer.handler.LDP_LOGIN_TOKEN_ENDPOINT", "https://auth.example.com/token"):
            with pytest.raises(httpx.HTTPStatusError):
                await fetch_ldp_auth_token(client)


# -- _parse_ldp_response -----------------------------------------------------


class TestParseLdpResponse:
    def test_valid_response(self):
        response = {
            "records": [
                {
                    "extras": {
                        "loftLiving": {
                            "modules": ["MR", "PAYMENT_CENTER"],
                            "permissionToEnter": True,
                        }
                    },
                    "resident_summary": "Test summary",
                }
            ]
        }

        result = _parse_ldp_response(response)

        assert result == {
            "enabled_modules": ["MR", "PAYMENT_CENTER"],
            "pte_setting": True,
            "resident_summary": "Test summary",
        }

    def test_empty_records(self):
        result = _parse_ldp_response({"records": []})

        assert result == {"enabled_modules": None, "pte_setting": False, "resident_summary": None}

    def test_no_records_key(self):
        result = _parse_ldp_response({})

        assert result == {"enabled_modules": None, "pte_setting": False, "resident_summary": None}

    def test_missing_loft_living(self):
        response = {"records": [{"extras": {}, "resident_summary": "sum"}]}

        result = _parse_ldp_response(response)

        assert result["enabled_modules"] is None
        assert result["pte_setting"] is False
        assert result["resident_summary"] == "sum"

    def test_pte_defaults_to_false(self):
        response = {"records": [{"extras": {"loftLiving": {"modules": ["MR"]}}}]}

        result = _parse_ldp_response(response)

        assert result["pte_setting"] is False


# -- warm_property ------------------------------------------------------------

_LDP_SUCCESS_BODY = {
    "records": [
        {
            "extras": {"loftLiving": {"modules": ["MR"], "permissionToEnter": False}},
            "resident_summary": "test",
        }
    ]
}


class TestWarmProperty:
    @pytest.mark.asyncio
    async def test_success(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=_response(200, _LDP_SUCCESS_BODY))
        semaphore = asyncio.Semaphore(10)

        with patch("lambdas.ldp_cache_warmer.handler.LDP_RP_API_URL", "https://ldp.example.com"):
            with patch("lambdas.ldp_cache_warmer.handler.cache") as mock_cache:
                mock_cache.set = AsyncMock()
                result = await warm_property(client, "tok_123", "42", semaphore)

        assert result is True
        mock_cache.set.assert_called_once()
        call_args = mock_cache.set.call_args
        # Key must include early:v2: prefix to match cashews @cache.early format
        assert call_args[0][0] == "early:v2:ldp_property_data:42"
        # Value must be [early_expire_at, result] to match cashews @cache.early format
        cached_value = call_args[0][1]
        assert isinstance(cached_value, list)
        assert len(cached_value) == 2
        assert isinstance(cached_value[0], datetime)
        assert cached_value[0].tzinfo == timezone.utc
        assert cached_value[1]["enabled_modules"] == ["MR"]
        assert call_args[1]["expire"] == handler.LDP_CACHE_TTL

    @pytest.mark.asyncio
    async def test_no_modules_returns_false(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=_response(200, {"records": [{"extras": {}}]}))
        semaphore = asyncio.Semaphore(10)

        with patch("lambdas.ldp_cache_warmer.handler.LDP_RP_API_URL", "https://ldp.example.com"):
            with patch("lambdas.ldp_cache_warmer.handler.cache"):
                result = await warm_property(client, "tok_123", "42", semaphore)

        assert result is False

    @pytest.mark.asyncio
    async def test_http_error_returns_false(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=_response(400, {"error": "fail"}))
        semaphore = asyncio.Semaphore(10)

        with patch("lambdas.ldp_cache_warmer.handler.LDP_RP_API_URL", "https://ldp.example.com"):
            with patch("lambdas.ldp_cache_warmer.handler.cache"):
                result = await warm_property(client, "tok_123", "42", semaphore)

        assert result is False
        assert client.post.await_count == 1

    @pytest.mark.asyncio
    async def test_http_500_retries_then_returns_false(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(
            side_effect=[
                _response(500, {"error": "fail"}),
                _response(500, {"error": "fail"}),
            ]
        )
        semaphore = asyncio.Semaphore(10)

        with (
            patch("lambdas.ldp_cache_warmer.handler.LDP_RP_API_URL", "https://ldp.example.com"),
            patch("lambdas.ldp_cache_warmer.handler.LDP_WARM_MAX_RETRIES", 1),
            patch("lambdas.ldp_cache_warmer.handler.LDP_WARM_RETRY_BACKOFF_SECONDS", 0.25),
            patch("lambdas.ldp_cache_warmer.handler.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("lambdas.ldp_cache_warmer.handler.cache"),
        ):
            result = await warm_property(client, "tok_123", "42", semaphore)

        assert result is False
        assert client.post.await_count == 2
        mock_sleep.assert_awaited_once_with(0.25)

    @pytest.mark.asyncio
    async def test_timeout_retries_then_succeeds(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(
            side_effect=[
                httpx.ReadTimeout("timed out"),
                _response(200, _LDP_SUCCESS_BODY),
            ]
        )
        semaphore = asyncio.Semaphore(10)

        with (
            patch("lambdas.ldp_cache_warmer.handler.LDP_RP_API_URL", "https://ldp.example.com"),
            patch("lambdas.ldp_cache_warmer.handler.LDP_WARM_MAX_RETRIES", 1),
            patch("lambdas.ldp_cache_warmer.handler.LDP_WARM_RETRY_BACKOFF_SECONDS", 0.25),
            patch("lambdas.ldp_cache_warmer.handler.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("lambdas.ldp_cache_warmer.handler.cache") as mock_cache,
        ):
            mock_cache.set = AsyncMock()
            result = await warm_property(client, "tok_123", "42", semaphore)

        assert result is True
        assert client.post.await_count == 2
        mock_sleep.assert_awaited_once_with(0.25)

    @pytest.mark.asyncio
    async def test_timeout_returns_false_after_retry_exhausted(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(side_effect=[httpx.ReadTimeout("timed out"), httpx.ReadTimeout("still timed out")])
        semaphore = asyncio.Semaphore(10)

        with (
            patch("lambdas.ldp_cache_warmer.handler.LDP_RP_API_URL", "https://ldp.example.com"),
            patch("lambdas.ldp_cache_warmer.handler.LDP_WARM_MAX_RETRIES", 1),
            patch("lambdas.ldp_cache_warmer.handler.LDP_WARM_RETRY_BACKOFF_SECONDS", 0.25),
            patch("lambdas.ldp_cache_warmer.handler.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("lambdas.ldp_cache_warmer.handler.cache"),
        ):
            result = await warm_property(client, "tok_123", "42", semaphore)

        assert result is False
        assert client.post.await_count == 2
        mock_sleep.assert_awaited_once_with(0.25)


# -- _warm_batch --------------------------------------------------------------


class TestWarmBatch:
    @pytest.mark.asyncio
    async def test_all_succeed(self):
        with (
            patch("lambdas.ldp_cache_warmer.handler.WORKER_CONCURRENCY", 10),
            patch(
                "lambdas.ldp_cache_warmer.handler.fetch_ldp_auth_token",
                new_callable=AsyncMock,
                return_value="tok_123",
            ),
            patch("lambdas.ldp_cache_warmer.handler.warm_property", new_callable=AsyncMock, return_value=True),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await _warm_batch(["100", "200", "300"])

        assert result == {"succeeded": 3, "failed": 0}

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        with (
            patch("lambdas.ldp_cache_warmer.handler.WORKER_CONCURRENCY", 10),
            patch(
                "lambdas.ldp_cache_warmer.handler.fetch_ldp_auth_token",
                new_callable=AsyncMock,
                return_value="tok_123",
            ),
            patch(
                "lambdas.ldp_cache_warmer.handler.warm_property",
                new_callable=AsyncMock,
                side_effect=[True, False, True],
            ),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await _warm_batch(["100", "200", "300"])

        assert result == {"succeeded": 2, "failed": 1}


# -- producer_handler ---------------------------------------------------------


def _mock_boto3():
    """Create a mock boto3 module with SQS client."""
    mock_sqs = MagicMock()
    mock_module = MagicMock()
    mock_module.client.return_value = mock_sqs
    return mock_module, mock_sqs


class TestProducerHandler:
    def test_sends_chunks_to_sqs(self):
        mock_boto3_mod, mock_sqs = _mock_boto3()
        property_ids = [str(i) for i in range(250)]

        with (
            patch.dict(sys.modules, {"boto3": mock_boto3_mod}),
            patch("lambdas.ldp_cache_warmer.handler._get_secrets", return_value={}),
            patch("lambdas.ldp_cache_warmer.handler._apply_secrets"),
            patch(
                "lambdas.ldp_cache_warmer.handler._fetch_property_ids",
                new_callable=AsyncMock,
                return_value=property_ids,
            ),
            patch.dict(os.environ, {"SQS_QUEUE_URL": "https://sqs.example.com/queue"}),
            patch("lambdas.ldp_cache_warmer.handler.CHUNK_SIZE", 100),
        ):
            result = producer_handler({}, None)

        body = json.loads(result["body"])
        assert body["sent"] == 250
        assert body["chunks"] == 3
        # 3 entries fit in a single send_message_batch call (< 10)
        assert mock_sqs.send_message_batch.call_count == 1
        # Verify message bodies contain chunked property IDs
        call_entries = mock_sqs.send_message_batch.call_args[1]["Entries"]
        chunk_sizes = [len(json.loads(e["MessageBody"])["property_ids"]) for e in call_entries]
        assert chunk_sizes == [100, 100, 50]

    def test_empty_property_list(self):
        with (
            patch("lambdas.ldp_cache_warmer.handler._get_secrets", return_value={}),
            patch("lambdas.ldp_cache_warmer.handler._apply_secrets"),
            patch(
                "lambdas.ldp_cache_warmer.handler._fetch_property_ids",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch.dict(os.environ, {"SQS_QUEUE_URL": "https://sqs.example.com/queue"}),
        ):
            result = producer_handler({}, None)

        body = json.loads(result["body"])
        assert body["sent"] == 0
        assert body["chunks"] == 0

    def test_missing_sqs_queue_url(self):
        """Raises early if SQS_QUEUE_URL is not set, before fetching properties."""
        with (
            patch("lambdas.ldp_cache_warmer.handler._get_secrets", return_value={}),
            patch("lambdas.ldp_cache_warmer.handler._apply_secrets"),
            patch.dict(os.environ, {}, clear=True),
            pytest.raises(RuntimeError, match="SQS_QUEUE_URL is required"),
        ):
            producer_handler({}, None)

    def test_sqs_batch_failure_raises(self):
        """Partial SQS send failures raise so Lambda marks invocation as failed."""
        mock_boto3_mod, mock_sqs = _mock_boto3()
        mock_sqs.send_message_batch.return_value = {
            "Successful": [{"Id": "0"}],
            "Failed": [{"Id": "1", "Code": "InternalError", "Message": "oops", "SenderFault": False}],
        }
        property_ids = [str(i) for i in range(200)]

        with (
            patch.dict(sys.modules, {"boto3": mock_boto3_mod}),
            patch("lambdas.ldp_cache_warmer.handler._get_secrets", return_value={}),
            patch("lambdas.ldp_cache_warmer.handler._apply_secrets"),
            patch(
                "lambdas.ldp_cache_warmer.handler._fetch_property_ids",
                new_callable=AsyncMock,
                return_value=property_ids,
            ),
            patch.dict(os.environ, {"SQS_QUEUE_URL": "https://sqs.example.com/queue"}),
            patch("lambdas.ldp_cache_warmer.handler.CHUNK_SIZE", 100),
            pytest.raises(RuntimeError, match="SQS chunks failed to send"),
        ):
            producer_handler({}, None)

    def test_multiple_sqs_batches_for_many_chunks(self):
        """11 chunks require 2 send_message_batch calls (10 + 1)."""
        mock_boto3_mod, mock_sqs = _mock_boto3()
        # 11 chunks of 10 = 110 properties
        property_ids = [str(i) for i in range(110)]

        with (
            patch.dict(sys.modules, {"boto3": mock_boto3_mod}),
            patch("lambdas.ldp_cache_warmer.handler._get_secrets", return_value={}),
            patch("lambdas.ldp_cache_warmer.handler._apply_secrets"),
            patch(
                "lambdas.ldp_cache_warmer.handler._fetch_property_ids",
                new_callable=AsyncMock,
                return_value=property_ids,
            ),
            patch.dict(os.environ, {"SQS_QUEUE_URL": "https://sqs.example.com/queue"}),
            patch("lambdas.ldp_cache_warmer.handler.CHUNK_SIZE", 10),
        ):
            result = producer_handler({}, None)

        body = json.loads(result["body"])
        assert body["sent"] == 110
        assert body["chunks"] == 11
        # 11 chunks: batch 1 has 10 entries, batch 2 has 1 entry
        assert mock_sqs.send_message_batch.call_count == 2


# -- worker_handler -----------------------------------------------------------


class TestWorkerHandler:
    def test_all_succeed(self):
        sqs_event = {
            "Records": [
                {
                    "messageId": "msg-1",
                    "body": json.dumps({"property_ids": ["100", "200"], "chunk_index": 0}),
                }
            ]
        }

        with (
            patch("lambdas.ldp_cache_warmer.handler._get_secrets", return_value={}),
            patch("lambdas.ldp_cache_warmer.handler._apply_secrets"),
            patch("lambdas.ldp_cache_warmer.handler._setup_cache"),
            patch(
                "lambdas.ldp_cache_warmer.handler._warm_batch",
                new_callable=AsyncMock,
                return_value={"succeeded": 2, "failed": 0},
            ),
        ):
            result = worker_handler(sqs_event, None)

        assert result == {"batchItemFailures": []}

    def test_exception_reports_batch_failure(self):
        """If _warm_batch raises, the SQS message is reported as failed for retry."""
        sqs_event = {
            "Records": [
                {
                    "messageId": "msg-1",
                    "body": json.dumps({"property_ids": ["100"], "chunk_index": 0}),
                }
            ]
        }

        with (
            patch("lambdas.ldp_cache_warmer.handler._get_secrets", return_value={}),
            patch("lambdas.ldp_cache_warmer.handler._apply_secrets"),
            patch("lambdas.ldp_cache_warmer.handler._setup_cache"),
            patch(
                "lambdas.ldp_cache_warmer.handler._warm_batch",
                new_callable=AsyncMock,
                side_effect=RuntimeError("LDP auth failed"),
            ),
        ):
            result = worker_handler(sqs_event, None)

        assert result == {"batchItemFailures": [{"itemIdentifier": "msg-1"}]}

    def test_partial_property_failures_not_retried(self):
        """Individual property failures within a chunk don't trigger SQS retry."""
        sqs_event = {
            "Records": [
                {
                    "messageId": "msg-1",
                    "body": json.dumps({"property_ids": ["100", "200", "300"], "chunk_index": 0}),
                }
            ]
        }

        with (
            patch("lambdas.ldp_cache_warmer.handler._get_secrets", return_value={}),
            patch("lambdas.ldp_cache_warmer.handler._apply_secrets"),
            patch("lambdas.ldp_cache_warmer.handler._setup_cache"),
            patch(
                "lambdas.ldp_cache_warmer.handler._warm_batch",
                new_callable=AsyncMock,
                return_value={"succeeded": 2, "failed": 1},
            ),
        ):
            result = worker_handler(sqs_event, None)

        # Partial failures are logged, not retried — next cycle re-warms them
        assert result == {"batchItemFailures": []}


# -- cache key consistency ---------------------------------------------------


class TestCacheKeyConsistency:
    def test_cache_key_matches_service_decorator(self):
        """Handler cache key prefix must match what cashews @cache.early generates in ldp.py."""
        from cashews import get_cache_key_template

        from agent_leasing.clients.ldp import fetch_ldp_property_data

        template = get_cache_key_template(
            fetch_ldp_property_data,
            key="ldp_property_data:{property_id}",
            prefix="early:v2",
        )
        assert template == f"{CACHE_KEY_PREFIX}:{{property_id}}"


# -- _apply_secrets TTL override ---------------------------------------------


class TestApplySecretsTTL:
    @pytest.fixture(autouse=True)
    def _restore_defaults(self):
        """Restore module-level globals after each test."""
        yield
        handler.LDP_CACHE_TTL = "2h"
        handler.LDP_CACHE_EARLY_TTL = "1h30m"
        handler.CHUNK_SIZE = 100
        handler.WORKER_CONCURRENCY = 20
        handler.LDP_WARM_MAX_RETRIES = 1
        handler.LDP_WARM_RETRY_BACKOFF_SECONDS = 1.0

    def test_overrides_ttl_from_secrets(self):
        """_apply_secrets sets LDP_CACHE_TTL and LDP_CACHE_EARLY_TTL from SM."""
        secrets = {
            "AI_CONFIG_HOST": "",
            "AI_CONFIG_TOKEN": "",
            "LDP_RP_API_URL": "",
            "LDP_LOGIN_TOKEN_ENDPOINT": "",
            "LDP_LOGIN_CLIENT_ID": "",
            "LDP_LOGIN_CLIENT_SECRET": "",
            "LDP_CACHE_TTL": "4h",
            "LDP_CACHE_EARLY_TTL": "3h",
        }
        handler._apply_secrets(secrets)

        assert handler.LDP_CACHE_TTL == "4h"
        assert handler.LDP_CACHE_EARLY_TTL == "3h"

    def test_defaults_when_keys_missing(self):
        """_apply_secrets uses defaults when TTL keys are absent from SM."""
        secrets = {
            "AI_CONFIG_HOST": "",
            "AI_CONFIG_TOKEN": "",
            "LDP_RP_API_URL": "",
            "LDP_LOGIN_TOKEN_ENDPOINT": "",
            "LDP_LOGIN_CLIENT_ID": "",
            "LDP_LOGIN_CLIENT_SECRET": "",
        }
        handler._apply_secrets(secrets)

        assert handler.LDP_CACHE_TTL == "2h"
        assert handler.LDP_CACHE_EARLY_TTL == "1h30m"
        assert handler.CHUNK_SIZE == 100
        assert handler.WORKER_CONCURRENCY == 20

    def test_overrides_tuning_knobs_from_secrets(self):
        """_apply_secrets sets worker tuning knobs from SM."""
        secrets = {
            "AI_CONFIG_HOST": "",
            "AI_CONFIG_TOKEN": "",
            "LDP_RP_API_URL": "",
            "LDP_LOGIN_TOKEN_ENDPOINT": "",
            "LDP_LOGIN_CLIENT_ID": "",
            "LDP_LOGIN_CLIENT_SECRET": "",
            "CHUNK_SIZE": "50",
            "WORKER_CONCURRENCY": "10",
            "LDP_WARM_MAX_RETRIES": "2",
            "LDP_WARM_RETRY_BACKOFF_SECONDS": "1.5",
        }
        handler._apply_secrets(secrets)

        assert handler.CHUNK_SIZE == 50
        assert handler.WORKER_CONCURRENCY == 10
        assert handler.LDP_WARM_MAX_RETRIES == 2
        assert handler.LDP_WARM_RETRY_BACKOFF_SECONDS == 1.5
