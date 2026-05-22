from unittest.mock import MagicMock, patch

import pytest

from agent_leasing.util.frustration_classifier import (
    DEFAULT_FRUSTRATION_CLASSIFICATION_OUTPUT,
    FrustrationClassificationOutput,
    classify_frustration,
)
from agent_leasing.util.frustration_classifier.frustration_classifier_agent import (
    frustration_classifier_agent,
)


class TestClassifyFrustration:
    @pytest.mark.parametrize("text", ["", "   \n\t  ", None])
    @pytest.mark.asyncio
    async def test_empty_input_returns_default(self, text):
        result = await classify_frustration(text)
        assert result is DEFAULT_FRUSTRATION_CLASSIFICATION_OUTPUT
        assert result.is_frustrated is False

    @pytest.mark.asyncio
    async def test_runner_returns_classifier_output(self):
        with patch("agent_leasing.util.frustration_classifier.frustration_classifier_agent.Runner.run") as mock_runner:
            mock_result = MagicMock()
            mock_result.final_output = FrustrationClassificationOutput(
                reason="Demanded a manager and used hostile tone.",
                confidence=0.92,
                is_frustrated=True,
            )
            mock_runner.return_value = mock_result

            result = await classify_frustration("Resident: get me a manager")

            assert result.is_frustrated is True
            assert result.confidence == 0.92
            mock_runner.assert_called_once_with(
                frustration_classifier_agent,
                "Resident: get me a manager",
            )

    @pytest.mark.asyncio
    async def test_runner_failure_falls_back_to_default(self):
        with patch("agent_leasing.util.frustration_classifier.frustration_classifier_agent.Runner.run") as mock_runner:
            mock_runner.side_effect = Exception("boom")
            result = await classify_frustration("transcript")
            assert result is DEFAULT_FRUSTRATION_CLASSIFICATION_OUTPUT


class TestFrustrationClassificationOutput:
    def test_can_construct_with_required_fields(self):
        out = FrustrationClassificationOutput(
            reason="Hostile tone toward assistant.",
            confidence=0.8,
            is_frustrated=True,
            trigger_message="get me a manager",
        )
        assert out.reason == "Hostile tone toward assistant."
        assert out.confidence == 0.8
        assert out.is_frustrated is True
        assert out.trigger_message == "get me a manager"

    def test_trigger_message_defaults_to_empty(self):
        out = FrustrationClassificationOutput(
            reason="Polite exchange.",
            confidence=0.9,
            is_frustrated=False,
        )
        assert out.trigger_message == ""

    def test_default_output_is_not_frustrated(self):
        assert DEFAULT_FRUSTRATION_CLASSIFICATION_OUTPUT.is_frustrated is False
        assert DEFAULT_FRUSTRATION_CLASSIFICATION_OUTPUT.confidence == 0.0
        assert DEFAULT_FRUSTRATION_CLASSIFICATION_OUTPUT.trigger_message == ""


class TestClassifierAgentConfiguration:
    def test_agent_exists_and_uses_classification_output(self):
        assert frustration_classifier_agent is not None
        assert frustration_classifier_agent.name == "Frustration Classifier Agent"
        assert frustration_classifier_agent.output_type == FrustrationClassificationOutput
