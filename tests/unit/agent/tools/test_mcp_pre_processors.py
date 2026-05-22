"""Unit tests for MCP pre-processor functions."""

from unittest.mock import patch

import pytest

from agent_leasing.agent.tools.mcp_pre_processors import (
    _clamp_numeric_range,
    _mcp_length_guardrail_input,
    _mcp_numeric_guardrail,
    _truncate_string,
    mcp_input_guardrails,
)
from agent_leasing.settings import PHONE_NUMBER_MAX_FLOOR, Config


class TestMCPNumericGuardrail:
    """Test cases for _mcp_numeric_guardrail function."""

    # fmt: off
    @pytest.mark.parametrize(
        "input_arguments,expected_clamped,expected_output",
        [
            # No clamping needed - values within range
            ({"count": 100}, False, {"count": 100}),
            ({"amount": 0}, False, {"amount": 0}),
            ({"total": 1000}, False, {"total": 1000}),
            ({"value": 999_999_999}, False, {"value": 999_999_999}),
            # Clamping needed - values exceed max (phone-safe floor)
            ({"count": PHONE_NUMBER_MAX_FLOOR + 1}, True, {"count": PHONE_NUMBER_MAX_FLOOR}),
            ({"amount": 2 * PHONE_NUMBER_MAX_FLOOR}, True, {"amount": PHONE_NUMBER_MAX_FLOOR}),
            # Clamping needed - negative values (below min of 0)
            ({"count": -100}, True, {"count": 0}),
            ({"amount": -1_000_000}, True, {"amount": 0}),
            # String representations of numbers
            ({"count": "500"}, False, {"count": "500"}),
            ({"count": "2000000000"}, False, {"count": "2000000000"}),
            ({"count": "-500"}, True, {"count": "0"}),
            # Float values
            ({"price": 99.99}, False, {"price": 99.99}),
            ({"price": 1_500_000_000.5}, False, {"price": 1_500_000_000.5}),
            ({"price": "-50.5"}, True, {"price": "0"}),
            # Mixed types in nested structure
            (
                {"outer": {"count": 2_000_000_000, "name": "test"}},
                False,
                {"outer": {"count": 2_000_000_000, "name": "test"}},
            ),
            # List of values
            ({"values": [100, 500, 1000]}, False, {"values": [100, 500, 1000]}),
            ({"values": [100, 2_000_000_000, 1000]}, False, {"values": [100, 2_000_000_000, 1000]}),
            # Complex nested structure
            (
                {
                    "data": {
                        "counts": [100, 200, 3_000_000_000],
                        "metadata": {"total": 5_000_000_000, "name": "test"},
                    }
                },
                False,
                {
                    "data": {
                        "counts": [100, 200, 3_000_000_000],
                        "metadata": {"total": 5_000_000_000, "name": "test"},
                    }
                },
            ),
            # Non-numeric values pass through unchanged
            ({"name": "test", "status": "active"}, False, {"name": "test", "status": "active"}),
            # Empty arguments
            ({}, False, {}),
            # None arguments
            (None, False, None),
        ],
    )
    # fmt: on
    def test_numeric_guardrail_clamping(self, input_arguments, expected_clamped, expected_output):
        """Test that numeric values are properly clamped to configured ranges."""
        # Arrange - input arguments provided via parametrize

        # Act
        output_arguments, clamped = _mcp_numeric_guardrail(input_arguments)

        # Assert
        assert output_arguments == expected_output
        assert clamped == expected_clamped


class TestClampNumericRange:
    """Test cases for _clamp_numeric_range helper function."""

    # fmt: off
    @pytest.mark.parametrize(
        "input_value,expected_output",
        [
            # Integer values within range
            (0, 0),
            (100, 100),
            (1000, 1000),
            (999_999_999, 999_999_999),
            # Integer values exceeding max
            (PHONE_NUMBER_MAX_FLOOR + 1, PHONE_NUMBER_MAX_FLOOR),
            (2 * PHONE_NUMBER_MAX_FLOOR, PHONE_NUMBER_MAX_FLOOR),
            (50_000_000_000, PHONE_NUMBER_MAX_FLOOR),
            # Integer values below min
            (-1, 0),
            (-100, 0),
            (-1_000_000, 0),
            # Float values within range
            (0.0, 0.0),
            (99.99, 99.99),
            (1000.5, 1000.5),
            # Float values exceeding max
            (PHONE_NUMBER_MAX_FLOOR + 0.5, PHONE_NUMBER_MAX_FLOOR),
            (2.5 * PHONE_NUMBER_MAX_FLOOR, PHONE_NUMBER_MAX_FLOOR),
            # Float values below min
            (-0.1, 0),
            (-99.99, 0),
            # String representations of integers
            ("100", "100"),
            ("2000000000", "2000000000"),
            ("-500", "0"),
            # String representations of floats
            ("99.99", "99.99"),
            ("1500000000.5", "1500000000.5"),
            (str(PHONE_NUMBER_MAX_FLOOR + 0.5), str(PHONE_NUMBER_MAX_FLOOR)),
            ("-50.5", "0"),
            # Non-numeric strings pass through unchanged
            ("test", "test"),
            ("not a number", "not a number"),
            ("abc123", "abc123"),
        ],
    )
    # fmt: on
    def test_clamp_numeric_range_behavior(self, input_value, expected_output):
        """Test that individual values are clamped correctly."""
        # Arrange - input value provided via parametrize

        # Act
        with patch("agent_leasing.agent.tools.mcp_pre_processors.settings") as mock_settings:
            mock_settings.mcp_min_numeric_value = 0
            mock_settings.mcp_max_numeric_value = PHONE_NUMBER_MAX_FLOOR
            result = _clamp_numeric_range(input_value)

        # Assert
        assert result == expected_output


class TestMCPLengthGuardrailInput:
    """Test cases for _mcp_length_guardrail_input function."""

    # fmt: off
    @pytest.mark.parametrize(
        "input_arguments,expected_too_long,expected_output",
        [
            # No truncation needed - strings within limit
            ({"message": "Short message"}, False, {"message": "Short message"}),
            ({"name": "John", "status": "active"}, False, {"name": "John", "status": "active"}),
            ({"text": "a" * 500}, False, {"text": "a" * 500}),
            ({"description": "b" * 1000}, False, {"description": "b" * 1000}),
            # Truncation needed - strings exceed limit
            ({"message": "x" * 2000}, True, {"message": "x" * 1000}),
            ({"description": "y" * 5000}, True, {"description": "y" * 1000}),
            # Mixed - some strings need truncation
            (
                {"short": "test", "long": "z" * 2000},
                True,
                {"short": "test", "long": "z" * 1000},
            ),
            # Nested structures
            (
                {"outer": {"message": "a" * 2000, "name": "test"}},
                True,
                {"outer": {"message": "a" * 1000, "name": "test"}},
            ),
            # Lists of strings
            ({"messages": ["short", "also short"]}, False, {"messages": ["short", "also short"]}),
            (
                {"messages": ["short", "x" * 2000, "also short"]},
                True,
                {"messages": ["short", "x" * 1000, "also short"]},
            ),
            # Complex nested structure
            (
                {
                    "data": {
                        "items": ["normal", "b" * 3000],
                        "metadata": {"description": "c" * 4000, "count": 5},
                    }
                },
                True,
                {
                    "data": {
                        "items": ["normal", "b" * 1000],
                        "metadata": {"description": "c" * 1000, "count": 5},
                    }
                },
            ),
            # Non-string values pass through unchanged
            ({"count": 100, "active": True, "price": 99.99}, False, {"count": 100, "active": True, "price": 99.99}),
            # Empty arguments
            ({}, False, {}),
            # None arguments
            (None, False, None),
        ],
    )
    # fmt: on
    def test_length_guardrail_truncation(self, input_arguments, expected_too_long, expected_output):
        """Test that long strings are properly truncated to configured limit."""
        # Arrange - input arguments provided via parametrize

        # Act
        with patch("agent_leasing.agent.tools.mcp_pre_processors.settings") as mock_settings:
            mock_settings.mcp_max_input_length = 1000
            output_arguments, too_long = _mcp_length_guardrail_input(input_arguments)

        # Assert
        assert output_arguments == expected_output
        assert too_long == expected_too_long


class TestTruncateString:
    """Test cases for _truncate_string helper function."""

    # fmt: off
    @pytest.mark.parametrize(
        "input_value,max_length,expected_output",
        [
            ("short", 1000, "short"),
            ("a" * 500, 1000, "a" * 500),
            ("b" * 1000, 1000, "b" * 1000),
            ("c" * 2000, 1000, "c" * 1000),
            ("d" * 5000, 1000, "d" * 1000),
            ("", 1000, ""),
            ("test message", 100, "test message"),
            ("x" * 150, 100, "x" * 100),
        ],
    )
    # fmt: on
    def test_truncate_string_behavior(self, input_value, max_length, expected_output):
        """Test that individual strings are truncated correctly."""
        # Arrange - input value provided via parametrize

        # Act
        with patch("agent_leasing.agent.tools.mcp_pre_processors.settings") as mock_settings:
            mock_settings.mcp_max_input_length = max_length
            result = _truncate_string(input_value)

        # Assert
        assert result == expected_output
        assert len(result) <= max_length


class TestMCPInputGuardrails:
    """Integration tests for `mcp_input_guardrails`."""

    # fmt: off
    @pytest.mark.parametrize(
        "input_arguments,expected_output",
        [
            # Normal arguments - no guardrails triggered
            (
                {"name": "John Doe", "count": 5, "active": True},
                {"name": "John Doe", "count": 5, "active": True},
            ),
            # Numeric clamping only
            (
                {"count": 2_000_000_000, "status": "active"},
                {"count": 2_000_000_000, "status": "active"},
            ),
            # String truncation only
            (
                {"message": "x" * 2000, "count": 100},
                {"message": "x" * 1000, "count": 100},
            ),
            # Both numeric clamping and string truncation
            (
                {"count": PHONE_NUMBER_MAX_FLOOR + 5_000_000_000, "description": "y" * 3000, "name": "test"},
                {"count": PHONE_NUMBER_MAX_FLOOR, "description": "y" * 1000, "name": "test"},
            ),
            # Complex nested structure with multiple guardrails
            (
                {
                    "data": {
                        "counts": [100, 3_000_000_000, 500],
                        "messages": ["short", "z" * 2000],
                        "metadata": {"total": -1000, "description": "a" * 4000, "big": PHONE_NUMBER_MAX_FLOOR + 1},
                    }
                },
                {
                    "data": {
                        "counts": [100, 3_000_000_000, 500],
                        "messages": ["short", "z" * 1000],
                        "metadata": {"total": 0, "description": "a" * 1000, "big": PHONE_NUMBER_MAX_FLOOR},
                    }
                },
            ),
            # String numbers should be clamped
            (
                {"count": str(PHONE_NUMBER_MAX_FLOOR + 5000), "age": "25"},
                {"count": str(PHONE_NUMBER_MAX_FLOOR), "age": "25"},
            ),
            # Negative numbers clamped to minimum
            (
                {"balance": -500, "count": "-1000"},
                {"balance": 0, "count": "0"},
            ),
            # Empty arguments
            ({}, {}),
            # None arguments
            (None, None),
        ],
    )
    # fmt: on
    def test_guardrails_apply_numeric_and_length_limits(self, input_arguments, expected_output):
        """Validate guardrail pipeline against representative argument inputs."""
        # Arrange - input arguments provided via parametrize

        # Act
        with patch("agent_leasing.agent.tools.mcp_pre_processors.settings") as mock_settings:
            mock_settings.mcp_min_numeric_value = 0
            mock_settings.mcp_max_numeric_value = PHONE_NUMBER_MAX_FLOOR
            mock_settings.mcp_max_input_length = 1000
            output_arguments = mcp_input_guardrails(input_arguments)

        # Assert
        assert output_arguments == expected_output

    # fmt: off
    @pytest.mark.parametrize(
        "input_arguments,max_input_length,min_numeric,max_numeric",
        [
            # Different max input lengths
            ({"message": "x" * 500}, 200, 0, PHONE_NUMBER_MAX_FLOOR),
            ({"description": "y" * 1000}, 500, 0, PHONE_NUMBER_MAX_FLOOR),
            # Different numeric ranges
            ({"count": 1000}, 1000, 0, PHONE_NUMBER_MAX_FLOOR),
            ({"amount": -100}, 1000, -1000, PHONE_NUMBER_MAX_FLOOR),
            ({"value": 10_000}, 1000, 0, PHONE_NUMBER_MAX_FLOOR),
        ],
    )
    # fmt: on
    def test_guardrails_respect_settings_configuration(
        self, input_arguments, max_input_length, min_numeric, max_numeric
    ):
        """Test that guardrails respect different settings configurations."""
        # Arrange - input arguments and settings provided via parametrize

        # Act
        with patch("agent_leasing.agent.tools.mcp_pre_processors.settings") as mock_settings:
            mock_settings.mcp_min_numeric_value = min_numeric
            mock_settings.mcp_max_numeric_value = max_numeric
            mock_settings.mcp_max_input_length = max_input_length
            output_arguments = mcp_input_guardrails(input_arguments)

        # Assert - verify output respects the configured limits
        # Check string lengths don't exceed max_input_length
        for key, value in output_arguments.items():
            if isinstance(value, str):
                assert len(value) <= max_input_length

            # Check numeric values are within range
            if isinstance(value, (int, float)):
                assert min_numeric <= value <= max_numeric


class TestSettingsValidation:
    """Ensure settings validation enforces phone-safe numeric floor."""

    def test_mcp_max_numeric_value_floored(self):
        cfg = Config.model_validate({"mcp_max_numeric_value": 1_000_000_000})
        assert cfg.mcp_max_numeric_value == PHONE_NUMBER_MAX_FLOOR
