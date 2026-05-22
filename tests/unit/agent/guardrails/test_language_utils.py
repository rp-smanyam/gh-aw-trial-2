import pytest

from agent_leasing.util import language_utils


class MockLanguageResult:
    def __init__(self, language_code: str):
        self.language_code = language_code


@pytest.mark.asyncio
async def test_localize_guardrail_response_returns_english_without_translation(monkeypatch):
    async def mock_classify_language(_):
        return MockLanguageResult("en")

    translate_called = False

    async def mock_translate_text(text, language_code):
        nonlocal translate_called
        translate_called = True
        return "translated"

    monkeypatch.setattr(language_utils, "classify_language", mock_classify_language)
    monkeypatch.setattr(language_utils, "translate_text", mock_translate_text)

    result = await language_utils.localize_guardrail_response("Hello", "Hello", content_type="input")

    assert result == "Hello"
    assert translate_called is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text, user_text, language_code, expected",
    [
        ("Safety first", "Hola", "es", "Safety first-es"),
        ("Blocked for safety", "Hola", "es", "Blocked for safety-es"),
        ("Sécurité d'abord", "Bonjour", "fr", "Sécurité d'abord-fr"),
        ("Seguridad primero", "Hola", "es", "Seguridad primero-es"),
        ("Safety first", "Hello", "en", "Safety first"),
    ],
)
async def test_localize_guardrail_response_param(monkeypatch, text, user_text, language_code, expected):
    async def mock_classify_language(_):
        return MockLanguageResult(language_code)

    async def mock_translate_text(text, language_code):
        return text if language_code == "en" else f"{text}-{language_code}"

    monkeypatch.setattr(language_utils, "classify_language", mock_classify_language)
    monkeypatch.setattr(language_utils, "translate_text", mock_translate_text)

    result = await language_utils.localize_guardrail_response(text, user_text, content_type="output")

    assert result == expected
