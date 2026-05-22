"""
Test text extraction utilities for guardrails.

These tests verify that text can be extracted from diverse input formats
(strings, sequences, dicts, objects) for guardrail validation.
"""

import pytest

from agent_leasing.agent.guardrails.text_utils import (
    extract_text_from_input,
    extract_text_from_output,
)


class MockObject:
    """Mock object for testing object attribute extraction."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


# Shared test cases for both extract_text_from_input and extract_text_from_output
# NOTE: list-based cases without role metadata live in the output-only and
# input-specific sections below because extract_text_from_input only extracts
# the last user message (returns "" when no user role is found).
EXTRACT_TEXT_TEST_CASES = [
    # String inputs
    ("simple string", "simple string"),
    ("", ""),
    ("  spaces  ", "  spaces  "),
    # Empty list
    ([], ""),
    # Dict inputs
    ({"text": "from text key"}, "from text key"),
    ({"content": "from content key"}, "from content key"),
    ({"response": "from response key"}, "from response key"),
    ({"other_key": "value"}, "{'other_key': 'value'}"),
    # Object inputs
    (MockObject(text="from text attr"), "from text attr"),
    (MockObject(content="from content attr"), "from content attr"),
    (MockObject(response="from response attr"), "from response attr"),
    (
        MockObject(suggested_response="suggested", detailed_information="detailed"),
        "suggested detailed",
    ),
    (MockObject(suggested_response="suggested"), "suggested"),
    (MockObject(detailed_information="detailed"), "detailed"),
    # Edge cases
    (123, "123"),
    (None, "None"),
    (True, "True"),
]

# Role-less list cases that only apply to extract_text_from_output
# (extract_text_from_input returns "" for lists without a user role)
ADDITIONAL_EXTRACT_OUTPUT_TEST_CASES = [
    # Sequence inputs - strings
    (["hello", "world"], "hello world"),
    (["single"], "single"),
    # Sequence inputs - dicts
    ([{"text": "first"}, {"text": "second"}], "first second"),
    ([{"content": "first"}, {"response": "second"}], "first second"),
    # Sequence inputs - objects
    ([MockObject(text="first"), MockObject(text="second")], "first second"),
    ([MockObject(response="first"), MockObject(content="second")], "first second"),
    # Sequence inputs - mixed types
    (["string", {"text": "dict"}, MockObject(text="object")], "string dict object"),
]

# extract_text_from_input only extracts the last user message so that
# guardrails don't re-moderate the entire conversation history.
# Role-less lists return "" because no user role is found.
ADDITIONAL_EXTRACT_INPUT_TEST_CASES = [
    # Role-less lists - no user role found → empty string
    (["hello", "world"], ""),
    (["single"], ""),
    ([{"text": "first"}, {"text": "second"}], ""),
    ([{"content": "first"}, {"response": "second"}], ""),
    ([MockObject(text="first"), MockObject(text="second")], ""),
    ([MockObject(response="first"), MockObject(content="second")], ""),
    (["string", {"text": "dict"}, MockObject(text="object")], ""),
    # Sequence inputs - with roles: extracts last user message
    ([MockObject(text="first", role="assistant"), MockObject(text="second", role="user")], "second"),
    ([MockObject(response="first", role="user"), MockObject(content="second", role="assistant")], "first"),
    # No user messages - returns empty (skip moderating assistant text)
    ([MockObject(text="first", role="assistant")], ""),
    ([MockObject(response="first", role="user")], "first"),
    ([{"text": "first", "role": "assistant"}, {"text": "second", "role": "user"}], "second"),
    ([{"response": "first", "role": "user"}, {"content": "second", "role": "assistant"}], "first"),
    # No user messages - returns empty (skip moderating assistant text)
    ([{"text": "first", "role": "assistant"}], ""),
    ([{"response": "first", "role": "user"}], "first"),
    # Multiple consecutive user messages - extracts only the last one
    ([MockObject(text="first", role="user"), MockObject(text="second", role="user")], "second"),
    ([{"text": "first", "role": "user"}, {"text": "second", "role": "user"}], "second"),
    (
        [
            MockObject(text="old", role="user"),
            MockObject(text="middle", role="assistant"),
            MockObject(text="latest", role="user"),
        ],
        "latest",
    ),
]


class TestExtractTextFromInput:
    """Test extract_text_from_input with various input formats."""

    @pytest.mark.parametrize("input_value,expected", EXTRACT_TEXT_TEST_CASES + ADDITIONAL_EXTRACT_INPUT_TEST_CASES)
    def test_extract_text_from_input(self, input_value, expected):
        """Test text extraction from various input formats."""
        result = extract_text_from_input(input_value)
        assert result == expected


class TestExtractTextFromOutput:
    """Test extract_text_from_output with various output formats."""

    @pytest.mark.parametrize("output_value,expected", EXTRACT_TEXT_TEST_CASES + ADDITIONAL_EXTRACT_OUTPUT_TEST_CASES)
    def test_extract_text_from_output(self, output_value, expected):
        """Test text extraction from various output formats."""
        result = extract_text_from_output(output_value)
        assert result == expected
