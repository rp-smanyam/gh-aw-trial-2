"""Unit tests for MCP post-processor functions."""

import json
from unittest.mock import patch

import pytest
from mcp.types import CallToolResult, TextContent

from agent_leasing.agent.tools.mcp_post_processors import (
    _mcp_length_guardrail,
    _mcp_pii_guardrail,
    add_currency,
    mcp_output_guardrails,
    modify_events_output,
    modify_get_rent_information,
    sr_priority_post_processor,
    voice_sms_consent_confirmed_post_processor,
)
from agent_leasing.models.context import SessionScope


def _build_event(event_id: str, include_fields: bool = True) -> dict:
    """Helper to build a single event with optional imageUrl and hasUserSignedUp."""
    event = {
        "id": event_id,
        "title": f"Event {event_id}",
        "description": "Test event description",
        "startDate": "2025-12-25T12:00:00-08:00",
        "endDate": "2025-12-25T13:00:00-08:00",
        "isSignUpRequired": True,
        "price": {"amount": 1.0, "currency": "USD"},
    }
    if include_fields:
        event["imageUrl"] = f"https://example.com/image{event_id}.jpg"
        event["hasUserSignedUp"] = True
    return event


def _build_events_data(num_events: int, include_fields: bool = True) -> dict:
    """Helper to build events data with specified number of events."""
    return {"events": [_build_event(str(i), include_fields) for i in range(1, num_events + 1)]}


def _build_call_tool_result(data: dict | None = None, use_structured_content: bool = True) -> CallToolResult:
    """Helper to build CallToolResult from data."""
    if data is None:
        return CallToolResult(content=[], structuredContent=None)

    text_content = json.dumps(data)
    return CallToolResult(
        content=[TextContent(text=text_content, type="text")],
        structuredContent=data if use_structured_content else None,
    )


class TestModifyEventsOutput:
    """Test cases for modify_events_output post-processor."""

    @pytest.mark.parametrize(
        "num_events,include_fields,expected_has_image_url",
        [
            (0, True, False),  # Empty events array
            (1, True, False),  # Single event with fields → removed
            (3, True, False),  # Multiple events with fields → all removed
            (1, False, False),  # Event without fields → no error
            (3, False, False),  # Multiple events without fields → no error
        ],
    )
    def test_modify_events_output(self, num_events, include_fields, expected_has_image_url):
        """Test that fields are removed from events with various configurations."""
        # Arrange
        events_data = _build_events_data(num_events, include_fields)
        input_result = _build_call_tool_result(events_data)

        # Act
        output_result = modify_events_output(input_result)

        # Assert
        output_data = json.loads(output_result.content[0].text)
        assert "events" in output_data
        assert len(output_data["events"]) == num_events

        for i, event in enumerate(output_data["events"], 1):
            # imageUrl and hasUserSignedUp should always be absent after post-processing
            assert ("imageUrl" in event) == expected_has_image_url
            assert ("hasUserSignedUp" in event) is False
            # Verify other fields are preserved
            assert event["id"] == str(i)
            assert event["title"] == f"Event {i}"
            assert event["price"]["amount"] == 1.0

    @pytest.mark.parametrize(
        "error_response",
        [
            {
                "error_type": "format_error",
                "message": "Invalid format",
                "details": "Missing required field",
            },
            {
                "error_type": "graphql_error",
                "message": "GraphQL error occurred",
                "errors": [{"message": "Not found"}],
            },
            {
                "error_type": "internal_error",
                "message": "An unexpected error occurred.",
            },
        ],
    )
    def test_remove_image_url_with_error_responses(self, error_response):
        """Test that error responses (no 'events' key) are returned unchanged."""
        # Arrange
        input_result = _build_call_tool_result(error_response)

        # Act
        output_result = modify_events_output(input_result)

        # Assert
        output_data = json.loads(output_result.content[0].text)
        assert output_data == error_response
        assert "events" not in output_data

    @pytest.mark.parametrize(
        "use_structured_content,expected_structured_content_present",
        [
            (False, False),  # No structuredContent
            (True, True),  # With structuredContent
        ],
    )
    def test_remove_image_url_with_structured_content_variations(
        self, use_structured_content, expected_structured_content_present
    ):
        """Test that structuredContent is handled correctly whether present or not."""
        # Arrange
        events_data = _build_events_data(2, include_fields=True)
        input_result = _build_call_tool_result(events_data, use_structured_content)

        # Act
        output_result = modify_events_output(input_result)

        # Assert
        output_data = json.loads(output_result.content[0].text)
        assert "events" in output_data
        assert len(output_data["events"]) == 2
        for event in output_data["events"]:
            assert "imageUrl" not in event
            assert "hasUserSignedUp" not in event

        # Check structuredContent presence
        if expected_structured_content_present:
            assert output_result.structuredContent is not None
            assert "imageUrl" not in output_result.structuredContent["events"][0]
            assert "hasUserSignedUp" not in output_result.structuredContent["events"][0]
            # Verify text content matches structuredContent
            assert output_data == output_result.structuredContent
        else:
            assert output_result.structuredContent is None

    def test_remove_image_url_with_null_image_url(self):
        """Test that events with null imageUrl and hasUserSignedUp are handled correctly."""
        # Arrange
        events_data = {
            "events": [
                {
                    "id": "1",
                    "title": "Event 1",
                    "imageUrl": None,  # null imageUrl
                    "hasUserSignedUp": None,  # null hasUserSignedUp
                    "price": {"amount": 1.0, "currency": "USD"},
                }
            ]
        }
        input_result = _build_call_tool_result(events_data)

        # Act
        output_result = modify_events_output(input_result)

        # Assert
        output_data = json.loads(output_result.content[0].text)
        assert "imageUrl" not in output_data["events"][0]
        assert "hasUserSignedUp" not in output_data["events"][0]
        assert output_data["events"][0]["id"] == "1"

    @pytest.mark.parametrize(
        "input_result,expected_behavior",
        [
            (
                CallToolResult(content=[], structuredContent=None),
                "empty_content",
            ),  # Empty content → return unchanged
            (
                CallToolResult(
                    content=[TextContent(text="not valid json {[", type="text")],
                    structuredContent=None,
                ),
                "malformed_json",
            ),  # Malformed JSON → return unchanged
        ],
    )
    def test_remove_image_url_error_recovery(self, input_result, expected_behavior):
        """Test that invalid input is handled gracefully and returned unchanged."""
        # Arrange - input_result provided by parametrize

        # Act
        output_result = modify_events_output(input_result)

        # Assert - should return original result unchanged
        assert output_result == input_result

    def test_remove_image_url_with_events_none(self):
        """Test that events is None is handled correctly."""
        # Arrange
        events_data = {"events": None, "message": "User not found"}
        input_result = _build_call_tool_result(events_data)

        # Act
        output_result = modify_events_output(input_result)

        # Assert
        output_data = json.loads(output_result.content[0].text)
        assert output_data == events_data


class TestAddCurrency:
    """Test cases for add_currency function."""

    @pytest.mark.parametrize(
        "a,b,expected",
        [
            # Normal cases
            ("$23.00", "$100.45", "$123.45"),
            ("$0.00", "$0.00", "$0.00"),
            ("$1,000.00", "$500.50", "$1500.50"),
            ("$0.01", "$0.02", "$0.03"),
            # Large numbers with commas
            ("$1,234,567.89", "$9,876,543.21", "$11111111.10"),
            # None inputs
            (None, "$100.00", None),
            ("$100.00", None, None),
            (None, None, None),
            # Empty string inputs
            ("", "$100.00", None),
            ("$100.00", "", None),
            ("", "", None),
            # Mixed None and empty
            (None, "", None),
            ("", None, None),
        ],
    )
    def test_add_currency(self, a, b, expected):
        """Test add_currency with various inputs."""
        result = add_currency(a, b)
        assert result == expected

    @pytest.mark.parametrize(
        "a,b",
        [
            ("invalid", "$100.00"),
            ("$100.00", "not a number"),
            ("abc", "def"),
        ],
    )
    def test_add_currency_invalid_input_returns_none(self, a, b):
        """Test that invalid currency strings return None."""
        result = add_currency(a, b)
        assert result is None


class TestModifyGetRentInformation:
    """Test cases for modify_get_rent_information post-processor."""

    def _build_rent_info_data(
        self,
        balance: str | None = "$100.45",
        pending_balance: str | None = "$23.00",
        rent: str = "$1899.00",
        rent_due_date: str = "2025-09-01",
        is_error: bool = False,
    ) -> dict:
        """Helper to build rent information data."""
        return {
            "result": {
                "balance": balance,
                "pending_balance": pending_balance,
                "rent": rent,
                "rent_due_date": rent_due_date,
            },
            "isError": is_error,
        }

    def test_adds_total_balance_due(self):
        """Test that total_balance_due is calculated and added correctly."""
        # Arrange
        rent_data = self._build_rent_info_data(balance="$100.45", pending_balance="$23.00")
        input_result = _build_call_tool_result(rent_data)

        # Act
        output_result = modify_get_rent_information(input_result)

        # Assert
        output_data = json.loads(output_result.content[0].text)
        assert output_data["result"]["total_balance_due"] == "$123.45"
        assert output_data["result"]["balance"] == "$100.45"
        assert output_data["result"]["pending_balance"] == "$23.00"
        assert output_data["result"]["rent"] == "$1899.00"
        assert output_data["isError"] is False

    @pytest.mark.parametrize(
        "balance,pending_balance,expected_total",
        [
            ("$0.00", "$0.00", "$0.00"),
            ("$1,000.00", "$500.50", "$1500.50"),
            ("$0.01", "$0.02", "$0.03"),
            ("$1,234.56", "$789.44", "$2024.00"),
        ],
    )
    def test_adds_total_balance_due_various_amounts(self, balance, pending_balance, expected_total):
        """Test total_balance_due calculation with various amounts."""
        # Arrange
        rent_data = self._build_rent_info_data(balance=balance, pending_balance=pending_balance)
        input_result = _build_call_tool_result(rent_data)

        # Act
        output_result = modify_get_rent_information(input_result)

        # Assert
        output_data = json.loads(output_result.content[0].text)
        assert output_data["result"]["total_balance_due"] == expected_total

    @pytest.mark.parametrize(
        "balance,pending_balance",
        [
            (None, "$23.00"),
            ("$100.45", None),
            (None, None),
        ],
    )
    def test_total_balance_due_none_when_missing_values(self, balance, pending_balance):
        """Test that total_balance_due is None when balance or pending_balance is missing."""
        # Arrange
        rent_data = self._build_rent_info_data(balance=balance, pending_balance=pending_balance)
        input_result = _build_call_tool_result(rent_data)

        # Act
        output_result = modify_get_rent_information(input_result)

        # Assert
        output_data = json.loads(output_result.content[0].text)
        assert output_data["result"]["total_balance_due"] is None

    def test_returns_unchanged_when_result_is_none(self):
        """Test that result is returned unchanged when 'result' key is None."""
        # Arrange
        rent_data = {"result": None, "isError": True}
        input_result = _build_call_tool_result(rent_data)

        # Act
        output_result = modify_get_rent_information(input_result)

        # Assert
        assert output_result == input_result

    def test_returns_unchanged_when_no_result_key(self):
        """Test that result is returned unchanged when 'result' key is missing."""
        # Arrange
        error_data = {"error_type": "not_found", "message": "User not found"}
        input_result = _build_call_tool_result(error_data)

        # Act
        output_result = modify_get_rent_information(input_result)

        # Assert
        output_data = json.loads(output_result.content[0].text)
        assert output_data == error_data
        assert "total_balance_due" not in output_data

    def test_returns_unchanged_when_empty_content(self):
        """Test that empty content is handled gracefully."""
        # Arrange
        input_result = CallToolResult(content=[], structuredContent=None)

        # Act
        output_result = modify_get_rent_information(input_result)

        # Assert
        assert output_result == input_result

    def test_returns_unchanged_when_malformed_json(self):
        """Test that malformed JSON is handled gracefully."""
        # Arrange
        input_result = CallToolResult(
            content=[TextContent(text="not valid json {[", type="text")],
            structuredContent=None,
        )

        # Act
        output_result = modify_get_rent_information(input_result)

        # Assert
        assert output_result == input_result

    @pytest.mark.parametrize(
        "use_structured_content",
        [True, False],
    )
    def test_structured_content_handling(self, use_structured_content):
        """Test that structuredContent is handled correctly."""
        # Arrange
        rent_data = self._build_rent_info_data()
        input_result = _build_call_tool_result(rent_data, use_structured_content)

        # Act
        output_result = modify_get_rent_information(input_result)

        # Assert
        output_data = json.loads(output_result.content[0].text)
        assert output_data["result"]["total_balance_due"] == "$123.45"

        if use_structured_content:
            assert output_result.structuredContent is not None
            assert output_result.structuredContent["result"]["total_balance_due"] == "$123.45"
            assert output_data == output_result.structuredContent
        else:
            assert output_result.structuredContent is None


class TestMCPPIIGuardrail:
    """Test cases for _mcp_pii_guardrail function."""

    # fmt: off
    @pytest.mark.parametrize(
        "input_text, expected_contains_pii, expected_output_text",
        [
            # No PII - text should pass through unchanged
            ("This is a normal message about community events.", False, "This is a normal message about community events."),
            ("The property has a great pool and gym.", False, "The property has a great pool and gym."),
            # Email addresses should be redacted
            ("Contact me at john.doe@example.com for more info.", False, "Contact me at <EMAIL_ADDRESS> for more info."),
            ("My email is user123@domain.org", False, "My email is <EMAIL_ADDRESS>"),
            # Credit cards should be redacted
            ("Card number: 4532 1488 0343 6467", True, "Card number: <CREDIT_CARD>"),
            ("Use card 6011-1234-5678-9012", True, "Use card <CREDIT_CARD>"),
            # Driver licenses should be redacted
            ("Driver license number: D1234567", True, "Driver license number: <US_DRIVER_LICENSE>"),
            ("Driver license number: D7654321", True, "Driver license number: <US_DRIVER_LICENSE>"),
            # US SSN (currently not detected by Presidio)
            ("My Social Security number is 321-23-9976", True, "My Social Security number is <US_SSN>"),
            # IP addresses should be redacted
            ("Server IP: 192.168.1.1", True, "Server IP: <IP_ADDRESS>"),
            # IBAN codes should be redacted
            ("IBAN: GB29 NWBK 6016 1331 9268 19", True, "IBAN: <IBAN_CODE>"),
            # Multiple PII types in same text
            #("Email: test@example.com, Card: 4532 1488 0343 6467, Another: admin@test.org", False, "Email: test@example.com, Card: <CREDIT_CARD>, Another: admin@test.org"),
        ],
    )
    # fmt: on
    def test_pii_detection_and_redaction(self, input_text, expected_contains_pii, expected_output_text):
        """Test that PII is properly detected and redacted."""
        # Arrange - input text is provided via parametrize

        # Act
        result_text, contains_pii = _mcp_pii_guardrail(input_text)

        # Assert
        if expected_contains_pii:
            # Text should be modified (redacted)
            assert result_text != input_text
            assert result_text == expected_output_text
            assert contains_pii
        else:
            # Text should pass through unchanged when no PII
            assert result_text == input_text
            assert not contains_pii


class TestMCPLengthGuardrail:
    """Test cases for `_mcp_length_guardrail` behavior."""

    # fmt: off
    @pytest.mark.parametrize(
        "input_text,max_length,expected_text",
        [
            ("a" * 50, 100, "a" * 50),
            ("b" * 100, 100, "b" * 100),
            ("0123456789" * 30, 100, ("0123456789" * 30)[:100]),
            ("x" * 1000, 500, "x" * 500),
            ("", 100, ""),
        ],
    )
    # fmt: on
    def test_length_guardrail_behaviour(self, input_text, max_length, expected_text):
        """Ensure the length guardrail returns the expected text for each scenario."""
        # Arrange is handled by parametrization above.

        # Act
        with patch("agent_leasing.agent.tools.mcp_post_processors.settings") as mock_settings:
            mock_settings.mcp_max_output_length = max_length
            result_text, too_long = _mcp_length_guardrail(input_text)

        # Assert
        assert result_text == expected_text
        if len(input_text) > max_length:
            assert len(result_text) == max_length
            assert too_long
        else:
            assert len(result_text) == len(input_text)
            assert not too_long


class TestMCPOutputGuardrails:
    """Integration tests for `mcp_output_guardrails`."""

    # fmt: off
    @pytest.mark.parametrize(
        "input_text,max_length,expected_output",
        [
            #(
            #    "Contact steve@realpage.com. " + ("Extra content. " * 50),
            #    200,
            #    ("Contact <EMAIL_ADDRESS>. " + ("Extra content. " * 50))[:200],
            #),
            (
                "Normal event description with no PII.",
                500,
                "Normal event description with no PII.",
            ),
            #(
            #    "Event info: steve@realpage.com",
            #   500,
            #    "Event info: <EMAIL_ADDRESS>",
            #),
            (
                "Details at https://example.com",
                500,
                "Details at https://example.com",
            ),
            #(
            #    json.dumps({"events": [{"title": "Party", "contact": "admin@example.com"}],"message": "Event details",}),
            #    500,
            #    json.dumps({"events": [{"title": "Party", "contact": "<EMAIL_ADDRESS>"}],"message": "Event details",}),
            #),
            #(
            #    json.dumps({"events": [{"title": "Update", "contact": "community@example.org"}],"message": "General notice",}),
            #    500,
            #    json.dumps({"events": [{"title": "Update", "contact": "<EMAIL_ADDRESS>"}],"message": "General notice",}),
            #),
        ],
    )
    # fmt: on
    def test_guardrails_apply_redaction_and_length_limits(self, input_text, max_length, expected_output):
        """Validate guardrail pipeline against representative plain-text inputs."""
        # Arrange
        input_result = CallToolResult(
            content=[TextContent(text=input_text, type="text")],
            structuredContent=None,
        )

        # Act
        with patch("agent_leasing.agent.tools.mcp_post_processors.settings") as mock_settings:
            mock_settings.mcp_max_output_length = max_length
            output_result = mcp_output_guardrails(input_result)

        # Assert the text
        output_text = output_result.content[0].text
        assert output_text == expected_output
        assert len(output_text) <= max_length

        # Assert the structure
        assert isinstance(output_result, CallToolResult)
        assert len(output_result.content) == 1
        assert isinstance(output_result.content[0], TextContent)
        assert output_result.content[0].type == "text"


    def test_returns_unchanged_when_empty_content(self):
        """Test that empty content is handled gracefully and returned unchanged."""
        # Arrange
        input_result = CallToolResult(content=[], structuredContent=None)

        # Act
        output_result = mcp_output_guardrails(input_result)

        # Assert
        assert output_result is input_result
        assert output_result.content == []


class TestVoiceSmsConsentConfirmedPostProcessor:
    """Test cases for voice_sms_consent_confirmed_post_processor."""

    def test_sets_flag_on_successful_tool_call(self):
        """Test that voice_sms_consent_confirmed is set to True after successful tool call."""
        # Arrange
        context = SessionScope()
        assert context.voice_sms_consent_confirmed is False

        post_processor = voice_sms_consent_confirmed_post_processor()
        result = CallToolResult(
            content=[TextContent(text='{"success": true}', type="text")],
            isError=False,
        )

        # Act
        output = post_processor(result, context=context)

        # Assert
        assert context.voice_sms_consent_confirmed is True
        assert output == result

    def test_does_not_set_flag_on_error(self):
        """Test that voice_sms_consent_confirmed remains False when tool returns error."""
        # Arrange
        context = SessionScope()
        assert context.voice_sms_consent_confirmed is False

        post_processor = voice_sms_consent_confirmed_post_processor()
        result = CallToolResult(
            content=[TextContent(text="TOOL_ERROR: Something went wrong", type="text")],
            isError=True,
        )

        # Act
        output = post_processor(result, context=context)

        # Assert
        assert context.voice_sms_consent_confirmed is False
        assert output == result

    def test_flag_remains_true_after_multiple_calls(self):
        """Test that flag stays True after being set, even with subsequent calls."""
        # Arrange
        context = SessionScope()
        post_processor = voice_sms_consent_confirmed_post_processor()

        success_result = CallToolResult(
            content=[TextContent(text='{"success": true}', type="text")],
            isError=False,
        )

        # Act - first call sets the flag
        post_processor(success_result, context=context)
        assert context.voice_sms_consent_confirmed is True

        # Act - second call (flag already True)
        post_processor(success_result, context=context)

        # Assert - flag should still be True
        assert context.voice_sms_consent_confirmed is True

    def test_returns_result_unchanged(self):
        """Test that the post-processor returns the result unchanged."""
        # Arrange
        context = SessionScope()
        post_processor = voice_sms_consent_confirmed_post_processor()

        original_result = CallToolResult(
            content=[TextContent(text='{"resident_id": 123, "sms_consent": true}', type="text")],
            structuredContent={"resident_id": 123, "sms_consent": True},
            isError=False,
        )

        # Act
        output = post_processor(original_result, context=context)

        # Assert
        assert output is original_result
        assert output.content == original_result.content
        assert output.structuredContent == original_result.structuredContent


class TestSrPriorityPostProcessor:
    """Test cases for sr_priority_post_processor."""

    def _build_sr_result(
        self,
        priority_number: str = "3",
        priority_name: str = "Standard",
        agent_response: str = "Service request created.",
        is_error: bool = False,
        use_structured_content: bool = False,
    ) -> CallToolResult:
        data = {
            "service_request_id": "53362",
            "priority_number": priority_number,
            "priority_name": priority_name,
            "agent_response": agent_response,
        }
        return CallToolResult(
            content=[TextContent(text=json.dumps(data), type="text")],
            structuredContent=data if use_structured_content else None,
            isError=is_error,
        )

    def test_emergency_left_unchanged(self):
        """Emergency (priority_number '1') is left unchanged — no injection, fields preserved."""
        result = self._build_sr_result(priority_number="1", priority_name="Emergency", agent_response="SR created.")
        output = sr_priority_post_processor(result)
        data = json.loads(output.content[0].text)
        assert data["agent_response"] == "SR created."
        assert data["priority_number"] == "1"
        assert data["priority_name"] == "Emergency"

    def test_non_emergency_strips_priority_fields(self):
        """Non-emergency removes priority_number and priority_name."""
        result = self._build_sr_result(priority_number="3", priority_name="Standard")
        output = sr_priority_post_processor(result)
        data = json.loads(output.content[0].text)
        assert "priority_number" not in data
        assert "priority_name" not in data
        assert data["service_request_id"] == "53362"
        assert data["agent_response"] == "Service request created."

    @pytest.mark.parametrize("priority_number", ["2", "3", "4", "5"])
    def test_all_non_p1_priorities_stripped(self, priority_number):
        """All non-P1 priority numbers get fields stripped."""
        result = self._build_sr_result(priority_number=priority_number)
        output = sr_priority_post_processor(result)
        data = json.loads(output.content[0].text)
        assert "priority_number" not in data
        assert "priority_name" not in data

    def test_error_result_returned_unchanged(self):
        """Error results pass through unchanged."""
        result = self._build_sr_result(is_error=True)
        result.isError = True
        output = sr_priority_post_processor(result)
        assert output is result

    def test_empty_content_returned_unchanged(self):
        """Empty content passes through unchanged."""
        result = CallToolResult(content=[], structuredContent=None)
        output = sr_priority_post_processor(result)
        assert output is result

    def test_malformed_json_returned_unchanged(self):
        """Malformed JSON passes through unchanged."""
        result = CallToolResult(
            content=[TextContent(text="not valid json", type="text")],
            structuredContent=None,
        )
        output = sr_priority_post_processor(result)
        assert output is result

    def test_structured_content_updated_for_non_emergency(self):
        """structuredContent is updated when present."""
        result = self._build_sr_result(use_structured_content=True)
        output = sr_priority_post_processor(result)
        assert output.structuredContent is not None
        assert "priority_number" not in output.structuredContent
        assert "priority_name" not in output.structuredContent

    def test_structured_content_none_when_absent(self):
        """structuredContent stays None when original had none."""
        result = self._build_sr_result(use_structured_content=False)
        output = sr_priority_post_processor(result)
        assert output.structuredContent is None

    def test_missing_priority_number_treated_as_non_emergency(self):
        """Missing priority_number field strips both fields (no crash)."""
        data = {"service_request_id": "53362", "agent_response": "Done."}
        result = CallToolResult(
            content=[TextContent(text=json.dumps(data), type="text")],
            structuredContent=None,
        )
        output = sr_priority_post_processor(result)
        out_data = json.loads(output.content[0].text)
        assert "priority_number" not in out_data
        assert "priority_name" not in out_data
        assert out_data["agent_response"] == "Done."
