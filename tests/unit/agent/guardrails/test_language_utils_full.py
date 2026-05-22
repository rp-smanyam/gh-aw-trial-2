"""Extended tests for language_utils covering get_guardrail_message, translate_text, and localize_guardrail_response."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from agent_leasing.util import language_utils

# ---------------------------------------------------------------------------
# get_guardrail_message
# ---------------------------------------------------------------------------


class TestGetGuardrailMessage:
    """Tests for get_guardrail_message (lines 33-40)."""

    YAML_DATA = {
        "fair_housing_guardrail": {
            "en": "I cannot make judgments about groups of people.",
            "es": "No puedo hacer juicios sobre grupos de personas.",
        },
        "pii_guardrail": {
            "en": "Cannot process PII.",
        },
    }

    @pytest.fixture(autouse=True)
    def _clear_guardrail_message_cache(self):
        cache_clear = getattr(language_utils.get_guardrail_message, "cache_clear", None)
        if cache_clear:
            cache_clear()
        yield
        if cache_clear:
            cache_clear()

    @patch("builtins.open")
    @patch("agent_leasing.util.language_utils.yaml.safe_load")
    def test_returns_localized_message(self, mock_yaml, mock_open):
        mock_yaml.return_value = self.YAML_DATA
        result = language_utils.get_guardrail_message("fair_housing_guardrail", "es")
        assert result == "No puedo hacer juicios sobre grupos de personas."

    def test_get_guardrail_message_reads_yaml_at_most_once_for_repeated_calls(self):
        # The function's docstring already advertises caching to avoid repeated
        # disk reads. The implementation must back that promise — N identical
        # lookups should parse the YAML exactly once, not N times. See #1640 Task 3.
        with patch("agent_leasing.util.language_utils.yaml.safe_load") as mock_yaml:
            mock_yaml.return_value = {"some_guardrail": {"en": "Test message"}}
            for _ in range(5):
                language_utils.get_guardrail_message("some_guardrail", "en")
            assert mock_yaml.call_count == 1, (
                f"Expected YAML to be parsed once across 5 identical calls, got {mock_yaml.call_count}"
            )

    @patch("builtins.open")
    @patch("agent_leasing.util.language_utils.yaml.safe_load")
    def test_falls_back_to_default_language(self, mock_yaml, mock_open):
        mock_yaml.return_value = self.YAML_DATA
        result = language_utils.get_guardrail_message("pii_guardrail", "fr")
        assert result == "Cannot process PII."

    @patch("builtins.open")
    @patch("agent_leasing.util.language_utils.yaml.safe_load")
    def test_returns_none_for_unknown_guardrail(self, mock_yaml, mock_open):
        mock_yaml.return_value = self.YAML_DATA
        result = language_utils.get_guardrail_message("nonexistent_guardrail", "en")
        assert result is None

    @patch("builtins.open")
    @patch("agent_leasing.util.language_utils.yaml.safe_load")
    def test_returns_none_for_empty_yaml(self, mock_yaml, mock_open):
        mock_yaml.return_value = None
        result = language_utils.get_guardrail_message("fair_housing_guardrail", "en")
        assert result is None

    @patch("builtins.open")
    @patch("agent_leasing.util.language_utils.yaml.safe_load")
    def test_custom_default_language(self, mock_yaml, mock_open):
        mock_yaml.return_value = {
            "test_guardrail": {
                "es": "Mensaje en espanol.",
            },
        }
        result = language_utils.get_guardrail_message("test_guardrail", "de", default_language="es")
        assert result == "Mensaje en espanol."

    def test_prisma_airs_guardrail_key_resolves_against_real_yaml(self):
        # The prisma_airs_guardrail_agent passes guardrail_name="prisma_airs_guardrail"
        # (singular). The YAML must expose that exact key so non-English speakers
        # receive the static translated message instead of falling through to a
        # live translate_text() LLM call. See issue #1640 Task 1.
        result = language_utils.get_guardrail_message("prisma_airs_guardrail", "es")
        assert result is not None
        assert "ofensivas" in result or "ilegales" in result

    # The next two tests pin the YAML against the exact guardrail_name strings
    # passed by legal_advice_guardrail_agent and unauthorized_promises_guardrail_agent.
    # Without YAML entries, every non-English speaker tripping these guardrails
    # falls through to a live translate_text() LLM call. See issue #1640 Task 2.
    @pytest.mark.parametrize("language_code", ["en", "es", "fr", "de", "pt", "it", "ar", "zh", "ja"])
    def test_legal_advice_guardrail_has_yaml_entry_per_language(self, language_code):
        result = language_utils.get_guardrail_message("legal_advice_guardrail", language_code)
        assert result is not None and result.strip(), f"legal_advice_guardrail missing YAML entry for {language_code}"

    @pytest.mark.parametrize("language_code", ["en", "es", "fr", "de", "pt", "it", "ar", "zh", "ja"])
    def test_unauthorized_promises_guardrail_has_yaml_entry_per_language(self, language_code):
        result = language_utils.get_guardrail_message("unauthorized_promises_guardrail", language_code)
        assert result is not None and result.strip(), (
            f"unauthorized_promises_guardrail missing YAML entry for {language_code}"
        )


# ---------------------------------------------------------------------------
# translate_text
# ---------------------------------------------------------------------------


class TestTranslateText:
    """Tests for translate_text (lines 49-77)."""

    @pytest.mark.asyncio
    async def test_returns_original_for_english(self):
        result = await language_utils.translate_text("Hello there", "en")
        assert result == "Hello there"

    @pytest.mark.asyncio
    async def test_returns_original_for_english_uppercase(self):
        result = await language_utils.translate_text("Hello there", "EN")
        assert result == "Hello there"

    @pytest.mark.asyncio
    @patch.object(language_utils, "get_openai_client")
    async def test_translates_via_openai(self, mock_client):
        mock_choice = Mock()
        mock_choice.message.content = "Hola"
        mock_completion = Mock()
        mock_completion.choices = [mock_choice]
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_completion)

        result = await language_utils.translate_text("Hello", "es")
        assert result == "Hola"

        mock_client.return_value.chat.completions.create.assert_awaited_once()
        call_kwargs = mock_client.return_value.chat.completions.create.call_args[1]
        assert call_kwargs["temperature"] == 0
        assert "es" in call_kwargs["messages"][1]["content"]

    @pytest.mark.asyncio
    @patch.object(language_utils, "get_openai_client")
    async def test_falls_back_on_openai_exception(self, mock_client):
        mock_client.return_value.chat.completions.create = AsyncMock(side_effect=Exception("API error"))
        result = await language_utils.translate_text("Hello", "fr")
        assert result == "Hello"

    @pytest.mark.asyncio
    @patch.object(language_utils, "get_openai_client")
    async def test_falls_back_on_empty_translation(self, mock_client):
        mock_choice = Mock()
        mock_choice.message.content = "   "
        mock_completion = Mock()
        mock_completion.choices = [mock_choice]
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_completion)

        result = await language_utils.translate_text("Hello", "de")
        assert result == "Hello"


# ---------------------------------------------------------------------------
# localize_guardrail_response
# ---------------------------------------------------------------------------


class MockLanguageResult:
    def __init__(self, language_code: str):
        self.language_code = language_code


class TestLocalizeGuardrailResponse:
    """Tests for localize_guardrail_response (lines 96-98, 103-105, 111-113)."""

    @pytest.mark.asyncio
    async def test_returns_base_response_when_text_extraction_fails(self, monkeypatch):
        monkeypatch.setattr(
            language_utils,
            "extract_text_from_input",
            Mock(side_effect=Exception("parse error")),
        )
        result = await language_utils.localize_guardrail_response(
            "base response", "some content", content_type="input"
        )
        assert result == "base response"

    @pytest.mark.asyncio
    async def test_defaults_to_english_when_classify_fails(self, monkeypatch):
        monkeypatch.setattr(
            language_utils,
            "classify_language",
            AsyncMock(side_effect=Exception("classify error")),
        )
        result = await language_utils.localize_guardrail_response(
            "base response", "some content", content_type="input"
        )
        assert result == "base response"

    @pytest.mark.asyncio
    async def test_returns_yaml_message_for_known_guardrail_and_language(self, monkeypatch):
        monkeypatch.setattr(
            language_utils,
            "classify_language",
            AsyncMock(return_value=MockLanguageResult("es")),
        )
        monkeypatch.setattr(
            language_utils,
            "get_guardrail_message",
            Mock(return_value="Mensaje localizado"),
        )

        result = await language_utils.localize_guardrail_response(
            "base response",
            "Hola, necesito ayuda",
            guardrail_name="some_guardrail",
            content_type="input",
        )
        assert result == "Mensaje localizado"

    @pytest.mark.asyncio
    async def test_falls_through_to_translate_when_yaml_has_no_entry(self, monkeypatch):
        monkeypatch.setattr(
            language_utils,
            "classify_language",
            AsyncMock(return_value=MockLanguageResult("ja")),
        )
        monkeypatch.setattr(
            language_utils,
            "get_guardrail_message",
            Mock(return_value=None),
        )
        monkeypatch.setattr(
            language_utils,
            "translate_text",
            AsyncMock(return_value="translated-ja"),
        )

        result = await language_utils.localize_guardrail_response(
            "base response",
            "some japanese text",
            guardrail_name="some_guardrail",
            content_type="input",
        )
        assert result == "translated-ja"

    @pytest.mark.asyncio
    async def test_translates_when_no_guardrail_name(self, monkeypatch):
        monkeypatch.setattr(
            language_utils,
            "classify_language",
            AsyncMock(return_value=MockLanguageResult("fr")),
        )
        monkeypatch.setattr(
            language_utils,
            "translate_text",
            AsyncMock(return_value="translated-fr"),
        )

        result = await language_utils.localize_guardrail_response(
            "base response",
            "Bonjour",
            content_type="output",
        )
        assert result == "translated-fr"

    @pytest.mark.asyncio
    async def test_uses_extract_text_from_output_for_output_type(self, monkeypatch):
        mock_extract_output = Mock(return_value="extracted output text")
        monkeypatch.setattr(language_utils, "extract_text_from_output", mock_extract_output)
        monkeypatch.setattr(
            language_utils,
            "classify_language",
            AsyncMock(return_value=MockLanguageResult("en")),
        )

        result = await language_utils.localize_guardrail_response(
            "base response",
            "output content",
            content_type="output",
        )
        assert result == "base response"
        mock_extract_output.assert_called_once_with("output content")

    @pytest.mark.asyncio
    async def test_defaults_to_en_when_language_code_is_none(self, monkeypatch):
        monkeypatch.setattr(
            language_utils,
            "classify_language",
            AsyncMock(return_value=MockLanguageResult(None)),
        )

        result = await language_utils.localize_guardrail_response("base response", "some text", content_type="input")
        assert result == "base response"

    # The next four tests pin the language_code shortcut behavior. For OUTPUT
    # guardrails the agent already generated the response in the established
    # conversation language, so skipping classify_language drops a full LLM
    # round-trip per trip. INPUT guardrails always classify from user text —
    # the resident's typed language can differ from their stored conversation
    # language. See issue #1640 Task 4.
    @pytest.mark.asyncio
    async def test_output_language_code_kwarg_short_circuits_classify_language(self, monkeypatch):
        classify_mock = AsyncMock(return_value=MockLanguageResult("en"))
        monkeypatch.setattr(language_utils, "classify_language", classify_mock)
        monkeypatch.setattr(
            language_utils,
            "get_guardrail_message",
            Mock(return_value="Mensaje localizado"),
        )

        result = await language_utils.localize_guardrail_response(
            "base response",
            "some output",
            guardrail_name="some_guardrail",
            content_type="output",
            language_code="es",
        )

        assert result == "Mensaje localizado"
        classify_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_input_language_code_kwarg_does_not_short_circuit(self, monkeypatch):
        classify_mock = AsyncMock(return_value=MockLanguageResult("es"))
        monkeypatch.setattr(language_utils, "classify_language", classify_mock)
        monkeypatch.setattr(
            language_utils,
            "get_guardrail_message",
            Mock(return_value="Mensaje localizado"),
        )

        result = await language_utils.localize_guardrail_response(
            "base response",
            "alguna entrada",
            guardrail_name="some_guardrail",
            content_type="input",
            language_code="en",
        )

        assert result == "Mensaje localizado"
        classify_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_english_language_code_kwarg_returns_base_without_translation(self, monkeypatch):
        classify_mock = AsyncMock(return_value=MockLanguageResult("ja"))
        translate_mock = AsyncMock(return_value="should-not-be-called")
        monkeypatch.setattr(language_utils, "classify_language", classify_mock)
        monkeypatch.setattr(language_utils, "translate_text", translate_mock)

        result = await language_utils.localize_guardrail_response(
            "base response",
            "any text",
            content_type="output",
            language_code="en",
        )

        assert result == "base response"
        classify_mock.assert_not_called()
        translate_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_language_code_kwarg_falls_back_to_classify(self, monkeypatch):
        classify_mock = AsyncMock(return_value=MockLanguageResult("en"))
        monkeypatch.setattr(language_utils, "classify_language", classify_mock)

        result = await language_utils.localize_guardrail_response(
            "base response",
            "some text",
            content_type="output",
            language_code="",
        )

        assert result == "base response"
        classify_mock.assert_awaited_once()
