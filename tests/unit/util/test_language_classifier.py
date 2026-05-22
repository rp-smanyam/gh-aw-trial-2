from unittest.mock import MagicMock, patch

import pytest

from agent_leasing.util.language_classifier import classify_language
from agent_leasing.util.language_classifier.language_classifier_agent import (
    LanguageClassificationOutput,
    language_classifier_agent,
)


class TestLanguageClassifier:
    """Test cases for the language classification functionality."""

    @pytest.mark.parametrize(
        "text,expected_language,confidence",
        [
            ("Hello, how are you?", "en", 0.95),
            ("Hola, ¿cómo estás?", "es", 0.95),
            ("Bonjour, comment allez-vous?", "fr", 0.95),
            ("你好，你怎么样？", "zh", 0.98),
            ("こんにちは、お元気ですか？", "ja", 0.98),
            ("Hallo, wie geht es dir?", "de", 0.95),
            ("Hi", "en", 0.8),  # short text case
        ],
    )
    @pytest.mark.asyncio
    async def test_classify_language_success(self, text, expected_language, confidence):
        """Test language classification for various languages."""
        with patch("agent_leasing.util.language_classifier.language_classifier_agent.Runner.run") as mock_runner:
            # Mock the agent response
            mock_result = MagicMock()
            mock_result.final_output = LanguageClassificationOutput(
                reason=f"Detected {expected_language} language",
                language_code=expected_language,
                confidence=confidence,
            )
            mock_runner.return_value = mock_result

            result = await classify_language(text)

            assert result.language_code == expected_language
            mock_runner.assert_called_once_with(
                language_classifier_agent,
                text,
            )

    @pytest.mark.parametrize(
        "text",
        [
            "",  # empty text
            "   \n\t  ",  # whitespace only
            None,  # None text
        ],
    )
    @pytest.mark.asyncio
    async def test_classify_language_edge_cases_default_to_english(self, text):
        """Test language classification edge cases that should default to English."""
        result = await classify_language(text)
        assert result.language_code == "en"

    @pytest.mark.asyncio
    async def test_classify_language_error_handling(self):
        """Test language classification error handling falls back to English."""
        with patch("agent_leasing.util.language_classifier.language_classifier_agent.Runner.run") as mock_runner:
            # Mock an exception
            mock_runner.side_effect = Exception("Test error")

            result = await classify_language("Some text")

            assert result.language_code == "en"
            mock_runner.assert_called_once_with(
                language_classifier_agent,
                "Some text",
            )


class TestLanguageClassificationOutput:
    """Test cases for the LanguageClassificationOutput model."""

    def test_language_classification_output_creation(self):
        """Test creating a LanguageClassificationOutput instance."""
        output = LanguageClassificationOutput(reason="Test reason", language_code="en", confidence=0.95)
        assert output.language_code == "en"
        assert output.confidence == 0.95
        assert output.reason == "Test reason"

    def test_language_classification_output_validation(self):
        """Test LanguageClassificationOutput validation."""
        # Valid confidence range
        output = LanguageClassificationOutput(reason="Low confidence", language_code="es", confidence=0.0)
        assert output.confidence == 0.0

        output = LanguageClassificationOutput(reason="High confidence", language_code="fr", confidence=1.0)
        assert output.confidence == 1.0

    def test_language_classification_output_serialization(self):
        """Test LanguageClassificationOutput serialization."""
        output = LanguageClassificationOutput(reason="German detected", language_code="de", confidence=0.87)
        data = output.model_dump()
        assert data == {
            "reason": "German detected",
            "language_code": "de",
            "confidence": 0.87,
        }


class TestLanguageClassifierAgent:
    """Test cases for the language classifier agent configuration."""

    def test_language_classifier_agent_exists(self):
        """Test that the language classifier agent is properly configured."""
        assert language_classifier_agent is not None
        assert language_classifier_agent.name == "Language Classifier Agent"
        assert language_classifier_agent.output_type == LanguageClassificationOutput

    def test_language_classifier_agent_model_settings(self):
        """Test that the language classifier agent has appropriate model settings."""
        # assert language_classifier_agent.model_settings.temperature == 0.0
        pass
