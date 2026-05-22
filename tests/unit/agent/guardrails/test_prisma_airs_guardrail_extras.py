"""Extra unit tests for prisma_airs_guardrail_agent — covers uncovered lines
133-135, 151, 165, 168-173, 268, 278, 299-319.

Complements the existing test_prisma_airs_guardrail.py without duplicating its coverage.
"""

from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest
from agents import GuardrailFunctionOutput

from agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent import (
    _extract_log_text_from_payload,
    _make_api_call,
    _parse_inputs_to_payload,
    _parse_response_to_output,
    prisma_airs_input_guardrail,
    prisma_airs_output_guardrail,
)

_MODULE = "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent"


# ---------------------------------------------------------------------------
# _parse_inputs_to_payload — invalid content_type (lines 133-135)
# ---------------------------------------------------------------------------


class TestParseInputsToPayloadInvalidContentType:
    """Line 134-135: raise ValueError for invalid content_type."""

    def test_raises_value_error_for_invalid_content_type(self):
        mock_context = Mock()
        with pytest.raises(ValueError, match="Invalid content type: unknown"):
            _parse_inputs_to_payload("some text", "unknown", mock_context)


# ---------------------------------------------------------------------------
# _is_payload_empty — contents[0] not a dict (line 151)
# Already partially covered by existing tests, but line 151 specifically
# needs a non-dict first entry.
# ---------------------------------------------------------------------------


class TestIsPayloadEmptyNonDictEntry:
    """Line 150-151: contents[0] is not a dict."""

    def test_returns_true_when_first_entry_not_dict(self):
        from agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent import (
            _is_payload_empty,
        )

        assert _is_payload_empty({"contents": ["just a string"]}) is True
        assert _is_payload_empty({"contents": [42]}) is True
        assert _is_payload_empty({"contents": [None]}) is True


# ---------------------------------------------------------------------------
# _extract_log_text_from_payload (lines 164-173)
# ---------------------------------------------------------------------------


class TestExtractLogTextFromPayload:
    """Test log text extraction for various payload shapes."""

    def test_returns_no_content_when_prompt_and_response_empty(self):
        """Line 164-165: both prompt and response empty."""
        payload = {"contents": [{"prompt": "", "response": ""}]}
        assert _extract_log_text_from_payload(payload) == "no content to evaluate"

    def test_returns_prompt_when_only_prompt(self):
        """Line 166-167: prompt present, response absent."""
        payload = {"contents": [{"prompt": "Hello world", "response": ""}]}
        assert _extract_log_text_from_payload(payload) == "Hello world"

    def test_returns_response_when_only_response(self):
        """Line 168-169: response present, prompt absent."""
        payload = {"contents": [{"prompt": "", "response": "Some response text"}]}
        assert _extract_log_text_from_payload(payload) == "Some response text"

    def test_returns_both_when_prompt_and_response(self):
        """Line 170-171: both prompt and response present."""
        payload = {"contents": [{"prompt": "Ask something", "response": "Here is an answer"}]}
        result = _extract_log_text_from_payload(payload)
        assert "Prompt:" in result
        assert "Response:" in result

    def test_returns_str_of_entry_when_not_dict(self):
        """Line 172: last_entry is not a dict."""
        payload = {"contents": ["raw string entry"]}
        assert _extract_log_text_from_payload(payload) == "raw string entry"

    def test_returns_str_of_contents_when_not_list(self):
        """Line 173: contents is not a list."""
        payload = {"contents": "not a list"}
        assert _extract_log_text_from_payload(payload) == "not a list"

    def test_returns_str_of_contents_when_empty_list(self):
        """contents is an empty list — falls through to str(contents)."""
        payload = {"contents": []}
        assert _extract_log_text_from_payload(payload) == "[]"

    def test_truncates_long_prompt(self):
        """Prompt text is truncated to 100 chars."""
        long_prompt = "A" * 200
        payload = {"contents": [{"prompt": long_prompt}]}
        result = _extract_log_text_from_payload(payload)
        assert len(result) <= 100


# ---------------------------------------------------------------------------
# _parse_response_to_output — flagged categories from both prompt and response
# ---------------------------------------------------------------------------


class TestParseResponseToOutput:
    """Supplement existing tests: ensure flagged categories are merged from prompt + response detections."""

    def test_merges_flagged_categories_from_both_detections(self):
        prisma_response = {
            "action": "block",
            "prompt_detected": {"injection": True, "pii": False},
            "response_detected": {"malware": True, "injection": True},
        }
        is_harmful, action, categories = _parse_response_to_output(prisma_response)
        assert is_harmful is True
        assert action == "block"
        assert set(categories) == {"injection", "malware"}

    def test_empty_detections_returns_no_categories(self):
        prisma_response = {"action": "allow", "prompt_detected": {}, "response_detected": {}}
        is_harmful, action, categories = _parse_response_to_output(prisma_response)
        assert is_harmful is False
        assert categories == []

    def test_none_detections_returns_no_categories(self):
        prisma_response = {"action": "allow", "prompt_detected": None, "response_detected": None}
        is_harmful, _, categories = _parse_response_to_output(prisma_response)
        assert is_harmful is False
        assert categories == []


# ---------------------------------------------------------------------------
# Decorator wrappers (lines 268, 278)
# ---------------------------------------------------------------------------


class TestPrismaAirsInputGuardrail:
    """Test prisma_airs_input_guardrail delegates to _check_content_safety_prisma_airs."""

    @pytest.mark.asyncio
    async def test_input_guardrail_delegates(self):
        ctx = Mock()
        ctx.context = Mock()
        agent = Mock()
        input_content = "user input"

        with patch(f"{_MODULE}._check_content_safety_prisma_airs", new_callable=AsyncMock) as mock_check:
            expected = GuardrailFunctionOutput(output_info=input_content, tripwire_triggered=False)
            mock_check.return_value = expected

            result = await prisma_airs_input_guardrail.guardrail_function(ctx, agent, input_content)

        mock_check.assert_called_once_with(input_content, "input", ctx.context)
        assert result == expected


class TestPrismaAirsOutputGuardrail:
    """Test prisma_airs_output_guardrail delegates to _check_content_safety_prisma_airs."""

    @pytest.mark.asyncio
    async def test_output_guardrail_delegates(self):
        ctx = Mock()
        ctx.context = Mock()
        agent = Mock()
        output_content = "agent output"

        with patch(f"{_MODULE}._check_content_safety_prisma_airs", new_callable=AsyncMock) as mock_check:
            expected = GuardrailFunctionOutput(output_info=output_content, tripwire_triggered=False)
            mock_check.return_value = expected

            result = await prisma_airs_output_guardrail.guardrail_function(ctx, agent, output_content)

        mock_check.assert_called_once_with(output_content, "output", ctx.context)
        assert result == expected


# ---------------------------------------------------------------------------
# _make_api_call (lines 299-319)
# ---------------------------------------------------------------------------


class TestMakeApiCall:
    """Test the _make_api_call helper for success, HTTP errors, and non-JSON responses."""

    @pytest.mark.asyncio
    async def test_successful_json_response(self):
        """Successful call returns parsed JSON."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value='{"action": "allow"}')
        mock_response.json = AsyncMock(return_value={"action": "allow"})

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.request = Mock(return_value=mock_session_ctx)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await _make_api_call(
                url="https://api.example.com/check",
                payload={"contents": [{"prompt": "test"}]},
                headers={"x-pan-token": "key"},
                api_name="Test API",
            )

        assert result == {"action": "allow"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [401, 403])
    async def test_auth_error_raises_runtime_error_with_auth_message(self, status_code):
        """HTTP 401/403 raises RuntimeError with invalid API key message."""
        mock_response = AsyncMock()
        mock_response.status = status_code
        mock_response.text = AsyncMock(return_value="Unauthorized")

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.request = Mock(return_value=mock_session_ctx)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(RuntimeError, match="Invalid API key or OAuth token"):
                await _make_api_call(
                    url="https://api.example.com/check",
                    payload={},
                    headers={},
                    api_name="Test API",
                )

    @pytest.mark.asyncio
    async def test_http_error_raises_runtime_error(self):
        """HTTP 4xx/5xx raises RuntimeError."""
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.request = Mock(return_value=mock_session_ctx)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(RuntimeError, match="Test API API returned status 500"):
                await _make_api_call(
                    url="https://api.example.com/check",
                    payload={},
                    headers={},
                    api_name="Test API",
                )

    @pytest.mark.asyncio
    async def test_content_type_error_falls_back_to_manual_parse(self):
        """When response.json() raises ContentTypeError, falls back to json.loads."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value='{"action": "allow"}')
        mock_response.json = AsyncMock(side_effect=aiohttp.ContentTypeError(Mock(), Mock()))

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.request = Mock(return_value=mock_session_ctx)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await _make_api_call(
                url="https://api.example.com/check",
                payload={},
                headers={},
                api_name="Test API",
            )

        assert result == {"action": "allow"}

    @pytest.mark.asyncio
    async def test_content_type_error_with_invalid_body_raises(self):
        """When response.json() raises ContentTypeError and body is not valid JSON, raises RuntimeError."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value="not json at all")
        mock_response.json = AsyncMock(side_effect=aiohttp.ContentTypeError(Mock(), Mock()))

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.request = Mock(return_value=mock_session_ctx)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(RuntimeError, match="non-JSON response"):
                await _make_api_call(
                    url="https://api.example.com/check",
                    payload={},
                    headers={},
                    api_name="Test API",
                )

    @pytest.mark.asyncio
    async def test_content_type_error_with_empty_body_returns_empty_dict(self):
        """When body is empty and json() raises ContentTypeError, json.loads('{}') returns {}."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value="")
        mock_response.json = AsyncMock(side_effect=aiohttp.ContentTypeError(Mock(), Mock()))

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.request = Mock(return_value=mock_session_ctx)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await _make_api_call(
                url="https://api.example.com/check",
                payload={},
                headers={},
                api_name="Test API",
            )

        assert result == {}
