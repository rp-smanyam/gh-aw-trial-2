from unittest.mock import patch

import pytest

from agent_leasing.agent import (
    competitor_blocking_guardrail,
    fair_housing_output_guardrail,
    legal_advice_output_guardrail,
    pii_input_guardrail,
    pii_output_guardrail,
    prompt_injection_input_guardrail,
    security_input_guardrail,
    security_output_guardrail,
    unauthorized_promises_output_guardrail,
)
from agent_leasing.agent.util import (
    get_enabled_input_guardrails,
    get_enabled_output_guardrails,
)
from agent_leasing.settings import Config


class TestGuardrailConfigValidation:
    """Test guardrail configuration validation in settings."""

    def test_valid_input_guardrails(self):
        """Test that valid input guardrail names are accepted."""
        config = Config(
            enabled_input_guardrails=["security", "pii", "prompt_injection"],
            enabled_output_guardrails=["security"],
        )
        # Should not raise ValueError
        assert config.enabled_input_guardrails == [
            "security",
            "pii",
            "prompt_injection",
        ]

    def test_valid_output_guardrails(self):
        """Test that valid output guardrail names are accepted."""
        config = Config(
            enabled_input_guardrails=["security"],
            enabled_output_guardrails=[
                "security",
                "pii",
                "fair_housing",
                "competitor_blocking",
                "unauthorized_promises",
                "legal_advice",
            ],
        )
        # Should not raise ValueError
        assert config.enabled_output_guardrails == [
            "security",
            "pii",
            "fair_housing",
            "competitor_blocking",
            "unauthorized_promises",
            "legal_advice",
        ]

    def test_all_valid_input_guardrails(self):
        """Test that all valid input guardrails can be enabled."""
        config = Config(
            enabled_input_guardrails=[
                "security",
                "pii",
                "prompt_injection",
            ],
            enabled_output_guardrails=["security"],
        )
        assert len(config.enabled_input_guardrails) == 3

    def test_invalid_input_guardrail_raises_error(self):
        """Test that invalid input guardrail name raises ValueError."""
        with pytest.raises(ValueError, match="Invalid input guardrail names"):
            Config(
                enabled_input_guardrails=["security", "invalid_guardrail"],
                enabled_output_guardrails=["security"],
            )

    def test_invalid_output_guardrail_raises_error(self):
        """Test that invalid output guardrail name raises ValueError."""
        with pytest.raises(ValueError, match="Invalid output guardrail names"):
            Config(
                enabled_input_guardrails=["security"],
                enabled_output_guardrails=["security", "typo_guardrail"],
            )

    def test_empty_guardrail_lists_are_valid(self):
        """Test that empty guardrail lists are accepted (all guardrails disabled)."""
        config = Config(
            enabled_input_guardrails=[],
            enabled_output_guardrails=[],
        )
        assert config.enabled_input_guardrails == []
        assert config.enabled_output_guardrails == []


class TestGuardrailHelperFunctions:
    """Test helper functions that build guardrail lists from configuration."""

    @patch("agent_leasing.settings.settings")
    def test_get_enabled_input_guardrails_default(self, mock_settings):
        """Test that helper returns correct input guardrails for default config."""
        mock_settings.enabled_input_guardrails = ["security", "pii", "prompt_injection"]

        guardrails = get_enabled_input_guardrails()

        assert len(guardrails) == 3
        assert security_input_guardrail in guardrails
        assert pii_input_guardrail in guardrails
        assert prompt_injection_input_guardrail in guardrails

    @patch("agent_leasing.settings.settings")
    def test_get_enabled_output_guardrails_default(self, mock_settings):
        """Test that helper returns correct output guardrails for default config."""
        mock_settings.enabled_output_guardrails = [
            "security",
            "pii",
            "fair_housing",
            "competitor_blocking",
            "unauthorized_promises",
            "legal_advice",
        ]

        guardrails = get_enabled_output_guardrails()

        assert len(guardrails) == 6
        assert security_output_guardrail in guardrails
        assert pii_output_guardrail in guardrails
        assert fair_housing_output_guardrail in guardrails
        assert competitor_blocking_guardrail in guardrails
        assert unauthorized_promises_output_guardrail in guardrails
        assert legal_advice_output_guardrail in guardrails

    @patch("agent_leasing.settings.settings")
    def test_get_enabled_input_guardrails_subset(self, mock_settings):
        """Test that helper returns only enabled input guardrails."""
        mock_settings.enabled_input_guardrails = ["security"]

        guardrails = get_enabled_input_guardrails()

        assert len(guardrails) == 1
        assert security_input_guardrail in guardrails
        assert pii_input_guardrail not in guardrails

    @patch("agent_leasing.settings.settings")
    def test_get_enabled_output_guardrails_subset(self, mock_settings):
        """Test that helper returns only enabled output guardrails."""
        mock_settings.enabled_output_guardrails = ["pii", "fair_housing"]

        guardrails = get_enabled_output_guardrails()

        assert len(guardrails) == 2
        assert pii_output_guardrail in guardrails
        assert fair_housing_output_guardrail in guardrails
        assert security_output_guardrail not in guardrails
        assert competitor_blocking_guardrail not in guardrails

    @patch("agent_leasing.settings.settings")
    def test_get_enabled_input_guardrails_empty(self, mock_settings):
        """Test that helper returns empty list when no guardrails enabled."""
        mock_settings.enabled_input_guardrails = []

        guardrails = get_enabled_input_guardrails()

        assert len(guardrails) == 0

    @patch("agent_leasing.settings.settings")
    def test_get_enabled_output_guardrails_empty(self, mock_settings):
        """Test that helper returns empty list when no guardrails enabled."""
        mock_settings.enabled_output_guardrails = []

        guardrails = get_enabled_output_guardrails()

        assert len(guardrails) == 0

    @patch("agent_leasing.settings.settings")
    def test_get_enabled_input_guardrails_all(self, mock_settings):
        """Test that helper can return all input guardrails."""
        mock_settings.enabled_input_guardrails = [
            "security",
            "pii",
            "prompt_injection",
        ]

        guardrails = get_enabled_input_guardrails()

        assert len(guardrails) == 3
        assert security_input_guardrail in guardrails
        assert pii_input_guardrail in guardrails
        assert prompt_injection_input_guardrail in guardrails


@pytest.mark.parametrize(
    ("input_config", "expected_count"),
    [
        (["security", "pii", "prompt_injection"], 3),
        (["security"], 1),
        (["pii", "prompt_injection"], 2),
        ([], 0),
        (["security", "pii", "prompt_injection"], 3),
    ],
)
def test_input_guardrails_parameterized(input_config, expected_count):
    """Parameterized test for various input guardrail configurations."""
    with patch("agent_leasing.settings.settings") as mock_settings:
        mock_settings.enabled_input_guardrails = input_config
        guardrails = get_enabled_input_guardrails()
        assert len(guardrails) == expected_count


@pytest.mark.parametrize(
    ("output_config", "expected_count"),
    [
        (["security", "pii", "fair_housing", "competitor_blocking"], 4),
        (["security"], 1),
        (["pii", "fair_housing"], 2),
        ([], 0),
        (["competitor_blocking"], 1),
        (["unauthorized_promises"], 1),
        (["legal_advice"], 1),
    ],
)
def test_output_guardrails_parameterized(output_config, expected_count):
    """Parameterized test for various output guardrail configurations."""
    with patch("agent_leasing.settings.settings") as mock_settings:
        mock_settings.enabled_output_guardrails = output_config
        guardrails = get_enabled_output_guardrails()
        assert len(guardrails) == expected_count
