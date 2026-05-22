"""Unit tests for emergency_service_transfer_advanced tool."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest

from agent_leasing.agent.tools.emergency_service_transfer.advanced import (
    emergency_service_transfer_advanced as esta_module,
)
from agent_leasing.agent.tools.emergency_service_transfer.advanced.emergency_service_transfer_advanced import (
    DISPATCH_ERROR_MESSAGE,
    DISPATCH_SUCCESS_MESSAGE,
    NEVER_CALL_MESSAGE,
    PLAY_VOICE_MESSAGE_FIRST,
    _make_api_call,
    dispatch_to_emergency_service_transfer,
    parse_and_validate_phone_number,
)


class TestParseAndValidatePhoneNumber:
    """Tests for parse_and_validate_phone_number function."""

    @pytest.mark.parametrize(
        "input_phone,expected",
        [
            ("+12025551234", "2025551234"),  # E164 format -> national number only
            ("(202) 555-1234", "2025551234"),  # US format with area code -> national number only
            ("202-555-1234", "2025551234"),  # US format without parens -> national number only
        ],
    )
    def test_parses_and_formats_valid_phone_numbers(self, input_phone, expected):
        """Test that valid phone numbers are parsed and formatted correctly."""
        result = parse_and_validate_phone_number(input_phone, backup_number="")
        assert result == expected

    @pytest.mark.parametrize(
        "invalid_phone",
        [
            "123",  # Too short
            "not-a-phone",  # Invalid characters
            "+12345678901234567890",  # Too long
            "",  # Empty string
            "555-123",  # Too short with formatting
            "555-1234",  # Local format (assumes US)
        ],
    )
    def test_raises_error_for_invalid_phone_numbers(self, invalid_phone):
        """Test that ValueError is raised for invalid phone number formats."""
        with pytest.raises(ValueError, match="Invalid resident phone number|Failed to parse resident phone number"):
            parse_and_validate_phone_number(invalid_phone, backup_number="")


class TestDispatchToEmergencyServiceTransfer:
    """Tests for dispatch_to_emergency_service_transfer function."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "property_id, formatted_phone, service_request_summary, service_request_id, api_response, expected_response",
        [
            (
                "27504",
                "2025551234",
                "Emergency: Water leak in kitchen",
                "SR-12345",
                {"status": 200},
                DISPATCH_SUCCESS_MESSAGE,
            ),
            (
                "27504",
                "2025551234",
                "Emergency: Power outage",
                "SR-67890",
                {"status": 200},
                DISPATCH_SUCCESS_MESSAGE,
            ),
            (
                "27504",
                "2025551234",
                "Emergency: HVAC failure",
                "SR-99999",
                {"status": 400, "error": "Invalid request"},
                DISPATCH_ERROR_MESSAGE,
            ),
        ],
    )
    async def test_dispatch_to_emergency_service_transfer(
        self,
        property_id,
        formatted_phone,
        service_request_summary,
        service_request_id,
        api_response,
        expected_response,
    ):
        """Test successful dispatch returns the expected success message."""
        # Arrange
        mock_response = api_response

        # Act & Assert
        with patch(
            "agent_leasing.agent.tools.emergency_service_transfer.advanced.emergency_service_transfer_advanced._make_api_call"
        ) as mock_api_call:
            mock_api_call.return_value = mock_response

            result = await dispatch_to_emergency_service_transfer(
                property_id, formatted_phone, service_request_summary, service_request_id
            )

            assert result == expected_response

            # Verify API call parameters
            mock_api_call.assert_called_once()
            call_args = mock_api_call.call_args
            assert call_args.kwargs["method"] == "POST"
            assert "ResAICreateEngineDispatch" in call_args.kwargs["url"]

            # Verify payload structure
            payload = call_args.kwargs["payload"]
            assert payload["ServiceRequestID"] == service_request_id
            assert payload["ResidentTelephone"] == formatted_phone  # National number with country code
            assert payload["Summary"] == service_request_summary


class TestEmergencyServiceTransferAdvancedIntegration:
    """Integration tests for emergency_service_transfer_advanced tool via FunctionTool interface."""

    def _build_test_context(self, channel, already_created, already_played, phone_number="2025551234"):
        # Map channel to product name
        CHANNEL_TO_PRODUCT = {
            "VOICE": "resident_one_voice",
            "SMS": "resident_one_sms",
            "EMAIL": "resident_one_email",
            "CHAT": "resident_one_chat",
        }

        from agents import tool_context

        mock_context = Mock()
        mock_context.ask_request.product = CHANNEL_TO_PRODUCT.get(channel, "resident_one_chat")
        # Configure product_info attributes to return strings, not Mocks
        mock_context.ask_request.product_info.lo_property_id = "27504"
        mock_context.ask_request.product_info.caller = "+18663592204"
        mock_context.ask_request.product_info.resident_phone = "+18663592204"
        # Explicitly set langsmith_run_tree to None to prevent Mock serialization errors
        mock_context.langsmith_run_tree = None
        mock_context.disabled_modules = []
        mock_context.esr_phone_retry_attempted = False

        mock_tool_ctx = Mock(spec=tool_context.ToolContext)
        mock_tool_ctx.context = mock_context
        mock_tool_ctx.langsmith_run_tree = None
        mock_tool_ctx.tool_name = "emergency_service_transfer_advanced"

        json_input = json.dumps(
            {
                "called_create_service_request": already_created,
                "already_played_voice_channel_transfer_message": already_played,
                "resident_phone": phone_number,
                "service_request_summary": "Test emergency",
                "service_request_id": "SR-123",
            }
        )

        return mock_context, mock_tool_ctx, json_input

    # fmt: off
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "channel,already_created, already_played, api_response, phone_number, expected_in_result",
        [
            ("VOICE", True, True, {"status": 200}, "2025551234", DISPATCH_SUCCESS_MESSAGE),
            ("VOICE", False, True, {"status": 200}, "2025551234", NEVER_CALL_MESSAGE),
            ("VOICE", True, False, {"status": 200}, "2025551234", PLAY_VOICE_MESSAGE_FIRST),
            ("VOICE", False, False, {"status": 200}, "2025551234", NEVER_CALL_MESSAGE),
            ("VOICE", True, True, {"status": 400, "error": "Invalid request"}, "2025551234", DISPATCH_ERROR_MESSAGE),
            ("SMS", True, False, {"status": 200}, "2025551234", DISPATCH_SUCCESS_MESSAGE),
            ("SMS", False, False, {"status": 200}, "2025551234", NEVER_CALL_MESSAGE),
            ("SMS", True, True, {"status": 400, "error": "Invalid request"}, "2025551234", DISPATCH_ERROR_MESSAGE),
            ("EMAIL", True, False, {"status": 200}, "2025551234", DISPATCH_SUCCESS_MESSAGE),
            ("EMAIL", False, False, {"status": 200}, "2025551234", NEVER_CALL_MESSAGE),
            ("EMAIL", True, True, {"status": 400, "error": "Invalid request"}, "2025551234", DISPATCH_ERROR_MESSAGE),
            ("CHAT", True, False, {"status": 200}, "2025551234", DISPATCH_SUCCESS_MESSAGE),
            ("CHAT", False, False, {"status": 200}, "2025551234", NEVER_CALL_MESSAGE),
            ("CHAT", True, True, {"status": 400, "error": "Invalid request"}, "2025551234", DISPATCH_ERROR_MESSAGE),
            ("VOICE", True, True, {"status": 200}, "invalid-phone", "The phone number provided was not valid"),
            ("SMS", True, True, {"status": 200}, "123", "The phone number provided was not valid"),
            ("EMAIL", True, True, {"status": 200}, "not-a-phone", "The phone number provided was not valid"),
            ("CHAT", True, True, {"status": 200}, "abc", "The phone number provided was not valid"),
            ("VOICE", True, True, RuntimeError("Network timeout"), "2025551234", "Failed to dispatch emergency technician: Network timeout. Please escalate to a human teammate."),
            ("SMS", True, True, aiohttp.ClientError("Connection failed"), "2025551234", "Failed to dispatch emergency technician: Connection failed. Please escalate to a human teammate."),
            ("EMAIL", True, True, asyncio.TimeoutError(), "2025551234", "Failed to dispatch emergency technician: . Please escalate to a human teammate."),
            ("CHAT", True, True, {"status": 500, "error": "Internal server error"}, "2025551234", DISPATCH_ERROR_MESSAGE),
            ("VOICE", True, True, {"status": 404, "error": "Not found"}, "2025551234", DISPATCH_ERROR_MESSAGE),
        ],
    )
    # fmt: on
    async def test_emergency_service_transfer_advanced_via_tool_interface(
        self,
        channel,
        already_created,
        already_played,
        api_response,
        phone_number,
        expected_in_result,
    ):
        """Test that tool returns appropriate message based on emergency request creation status and channel."""

        # Arrange
        mock_context, mock_tool_ctx, json_input = self._build_test_context(
            channel, already_created, already_played, phone_number
        )

        # Mock API call for cases where dispatch should happen
        with patch(
            "agent_leasing.agent.tools.emergency_service_transfer.advanced.emergency_service_transfer_advanced._make_api_call"
        ) as mock_api_call:
            if isinstance(api_response, Exception):
                mock_api_call.side_effect = api_response
            else:
                mock_api_call.return_value = api_response

            # Act
            result = await esta_module.emergency_service_transfer_advanced.on_invoke_tool(mock_tool_ctx, json_input)

            # Assert
            assert expected_in_result in result

    @pytest.mark.asyncio
    async def test_emergency_service_transfer_advanced_missing_lo_property_id(self):
        mock_context, mock_tool_ctx, json_input = self._build_test_context("SMS", True, False)
        mock_context.ask_request.product_info.lo_property_id = None

        with patch(
            "agent_leasing.agent.tools.emergency_service_transfer.advanced.emergency_service_transfer_advanced._make_api_call"
        ) as mock_api_call:
            mock_api_call.return_value = {"status": 200}

            result = await esta_module.emergency_service_transfer_advanced.on_invoke_tool(mock_tool_ctx, json_input)

        assert "required to dispatch emergency service" in result
        assert "lo_property_id=None" in result

    @pytest.mark.asyncio
    async def test_emergency_service_transfer_advanced_missing_emergency_dispatch_url(self):
        mock_context, mock_tool_ctx, json_input = self._build_test_context("SMS", True, False)

        with (
            patch(
                "agent_leasing.agent.tools.emergency_service_transfer.advanced.emergency_service_transfer_advanced._make_api_call"
            ) as mock_api_call,
            patch(
                "agent_leasing.agent.tools.emergency_service_transfer.advanced.emergency_service_transfer_advanced.settings.emergency_dispatch_url",
                "",
            ),
        ):
            mock_api_call.return_value = {"status": 200}

            result = await esta_module.emergency_service_transfer_advanced.on_invoke_tool(mock_tool_ctx, json_input)

        assert "required to dispatch emergency service" in result
        assert "emergency_dispatch_url=''" in result

    @pytest.mark.asyncio
    async def test_invalid_phone_resets_call_ended_by_agent(self):
        """When phone validation fails the first time, call_ended_by_agent should be False to allow retry."""
        mock_context, mock_tool_ctx, json_input = self._build_test_context(
            "VOICE", True, True, phone_number="invalid-phone"
        )

        with patch(
            "agent_leasing.agent.tools.emergency_service_transfer.advanced"
            ".emergency_service_transfer_advanced._make_api_call"
        ) as mock_api_call:
            mock_api_call.return_value = {"status": 200}

            result = await esta_module.emergency_service_transfer_advanced.on_invoke_tool(mock_tool_ctx, json_input)

            assert "not valid" in result
            assert mock_context.call_ended_by_agent is False
            assert mock_context.esr_phone_retry_attempted is True

    @pytest.mark.asyncio
    async def test_second_invalid_phone_escalates(self):
        """When phone validation fails a second time, should escalate instead of retrying."""
        mock_context, mock_tool_ctx, json_input = self._build_test_context(
            "VOICE", True, True, phone_number="invalid-phone"
        )
        mock_context.esr_phone_retry_attempted = True  # simulate first failure already happened

        with patch(
            "agent_leasing.agent.tools.emergency_service_transfer.advanced"
            ".emergency_service_transfer_advanced._make_api_call"
        ) as mock_api_call:
            mock_api_call.return_value = {"status": 200}

            result = await esta_module.emergency_service_transfer_advanced.on_invoke_tool(mock_tool_ctx, json_input)

            assert "failed twice" in result
            assert "escalate" in result

    @pytest.mark.asyncio
    async def test_failed_dispatch_records_handoff_result(self):
        mock_context, mock_tool_ctx, json_input = self._build_test_context(
            "VOICE", True, True, phone_number="2025551234"
        )

        with patch(
            "agent_leasing.agent.tools.emergency_service_transfer.advanced"
            ".emergency_service_transfer_advanced._make_api_call"
        ) as mock_api_call:
            mock_api_call.side_effect = RuntimeError("Network timeout")

            await esta_module.emergency_service_transfer_advanced.on_invoke_tool(mock_tool_ctx, json_input)

        assert mock_context.handoff_result is not None
        assert mock_context.handoff_result.tool == "emergency_service_transfer_advanced"
        assert mock_context.handoff_result.reason == "EMERGENCY"
        assert mock_context.handoff_result.routing_confirmed is False


class TestMakeApiCallRetry:
    """Tests for _make_api_call retry logic on transient connection errors."""

    _MODULE = "agent_leasing.agent.tools.emergency_service_transfer.http_util"

    @pytest.mark.asyncio
    async def test_retries_on_client_connector_error_and_succeeds(self):
        """First attempt fails with ClientConnectorError, second succeeds."""
        expected = {"status": 200}

        with patch(f"{self._MODULE}._make_api_call_once", new_callable=AsyncMock) as mock_once:
            mock_once.side_effect = [
                aiohttp.ClientConnectorError(connection_key=Mock(), os_error=OSError("reset")),
                expected,
            ]
            with patch(f"{self._MODULE}.asyncio.sleep", new_callable=AsyncMock):
                result = await _make_api_call(
                    url="https://example.com", payload={}, headers={}, api_name="Test", method="POST"
                )

        assert result == expected
        assert mock_once.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_retry_exhaustion(self):
        """Both attempts fail — should raise the last exception."""
        with patch(f"{self._MODULE}._make_api_call_once", new_callable=AsyncMock) as mock_once:
            mock_once.side_effect = [
                ConnectionResetError("reset by peer"),
                ConnectionResetError("reset again"),
            ]
            with patch(f"{self._MODULE}.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(ConnectionResetError):
                    await _make_api_call(
                        url="https://example.com", payload={}, headers={}, api_name="Test", method="POST"
                    )

        assert mock_once.call_count == 2

    @pytest.mark.asyncio
    async def test_does_not_retry_on_runtime_error(self):
        """Non-transient errors (e.g. RuntimeError from 4xx) should not be retried."""
        with patch(f"{self._MODULE}._make_api_call_once", new_callable=AsyncMock) as mock_once:
            mock_once.side_effect = RuntimeError("API returned status 400")
            with pytest.raises(RuntimeError, match="400"):
                await _make_api_call(url="https://example.com", payload={}, headers={}, api_name="Test", method="POST")

        assert mock_once.call_count == 1
