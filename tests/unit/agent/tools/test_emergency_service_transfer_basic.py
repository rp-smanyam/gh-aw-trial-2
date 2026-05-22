"""Unit tests for emergency_service_transfer_basic tool."""

from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest

from agent_leasing.agent.tools.emergency_service_transfer.basic import (
    emergency_service_transfer_basic as estb_module,
)
from agent_leasing.agent.tools.emergency_service_transfer.basic.emergency_service_transfer_basic import (
    CALL_RESIDENT_MESSAGE_TEMPLATE,
    EMERGENCY_NOT_FOUND_ERROR,
    EMERGENCY_NOT_FOUND_KB_ERROR,
    FAILED_TO_PARSE_ERROR_PREFIX,
    INVALID_EMERGENCY_NUMBER_ERROR_PREFIX,
    NEVER_CALL_MESSAGE,
    VOICE_REDIRECT_MESSAGE_TEMPLATE,
    _make_api_call,
    get_company_id,
    get_emergency_number,
    get_emergency_number_from_knowledge_base_response,
    get_property_id,
    redirect_to_number_via_twilio,
)

INVALID_EMERGENCY_NUMBER_ERROR_PATTERN = rf"{INVALID_EMERGENCY_NUMBER_ERROR_PREFIX}: .+"
FAILED_TO_PARSE_ERROR_PATTERN = rf"{FAILED_TO_PARSE_ERROR_PREFIX} '.+': .+"
INVALID_OR_PARSE_ERROR_PATTERN = rf"({INVALID_EMERGENCY_NUMBER_ERROR_PATTERN}|{FAILED_TO_PARSE_ERROR_PATTERN})"


def _build_kb_response(emergency_phone: str | None = None) -> dict:
    """Helper to build knowledge base response with optional emergency phone."""
    keys = [
        {
            "category": None,
            "name": "aipropertypronunciation",
            "value": "kasidi saauthth",
        },
        {"category": None, "name": "airesidentsreferredto", "value": "Member"},
    ]

    if emergency_phone is not None:
        keys.insert(1, {"category": None, "name": "emergphone", "value": emergency_phone})

    return {"keys": keys, "tables": [], "pickLists": []}


class TestGetEmergencyNumberFromKnowledgeBaseResponse:
    """Tests for parsing emergency number from knowledge base response."""

    @pytest.mark.parametrize(
        "emergency_phone,expected",
        [
            ("+12025551234", "+12025551234"),
            ("(202) 555-1234", "+12025551234"),  # Test formatting
        ],
    )
    def test_extracts_and_formats_emergency_number(self, emergency_phone, expected):
        """Test that emergency number is extracted and formatted correctly."""
        kb_response = _build_kb_response(emergency_phone)
        result = get_emergency_number_from_knowledge_base_response(kb_response)
        assert result == expected

    def test_raises_error_when_emergency_number_missing(self):
        """Test that ValueError is raised when emergency number key is not in response."""
        kb_response = _build_kb_response(emergency_phone=None)
        with pytest.raises(ValueError, match=EMERGENCY_NOT_FOUND_KB_ERROR):
            get_emergency_number_from_knowledge_base_response(kb_response)

    @pytest.mark.parametrize(
        "invalid_phone",
        [
            "123",  # Too short
            "not-a-phone-number",  # Invalid format
            "555-1234",  # Missing area code
            "+1234567890123456",  # Too long
        ],
    )
    def test_raises_error_for_invalid_phone_numbers(self, invalid_phone):
        """Test that ValueError is raised for invalid phone number formats."""
        kb_response = _build_kb_response(invalid_phone)
        with pytest.raises(
            ValueError,
            match=INVALID_OR_PARSE_ERROR_PATTERN,
        ):
            get_emergency_number_from_knowledge_base_response(kb_response)


def _build_company_translation_response(set_id: str, include_set: bool = True) -> dict:
    """Helper to build Books API translation response."""
    instances = [
        {"source": "HAAS", "companyInstanceSourceId": "8135-ha"},
        {"source": "OS", "companyInstanceSourceId": "4341841"},
        {"source": "VES", "companyInstanceSourceId": "906"},
    ]

    if include_set:
        instances.insert(1, {"source": "SET", "companyInstanceSourceId": set_id})

    return {
        "data": {
            "type": "companyinstanceids",
            "attributes": {
                "companyInstanceSourceId": set_id,
                "source": "SET",
                "translatedCompanyInstances": instances,
            },
        }
    }


class TestGetCompanyId:
    """Tests for get_company_id function."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "include_set,expected_id,expected_error",
        [
            (True, "6061de1c-e077-4ff7-99bd-bfd50d7fb134", None),  # Success case
            (False, None, "SET ID not found in translation response"),  # Missing SET ID
        ],
    )
    async def test_get_company_id(self, include_set, expected_id, expected_error):
        """Test get_company_id extraction and error handling."""
        uc_company_id = "test-uc-company-123"

        mock_response = _build_company_translation_response(expected_id or "some-id", include_set)

        mock_make_api_call_str = "agent_leasing.agent.tools.emergency_service_transfer.basic.emergency_service_transfer_basic._make_api_call"
        mock_auth_token_str = "agent_leasing.agent.tools.emergency_service_transfer.basic.emergency_service_transfer_basic.get_books_auth_token"

        with (
            patch(mock_make_api_call_str, new_callable=AsyncMock) as mock_api_call,
            patch(mock_auth_token_str, new_callable=AsyncMock) as mock_auth_token,
        ):
            mock_api_call.return_value = mock_response
            mock_auth_token.return_value = "fake-token-123"

            if expected_error:
                with pytest.raises(ValueError, match=expected_error):
                    await get_company_id(uc_company_id)
            else:
                result = await get_company_id(uc_company_id)
                assert result == expected_id
                mock_api_call.assert_called_once()
                call_args = mock_api_call.call_args
                assert "companyinstance/test-uc-company-123/OS" in call_args.kwargs["url"]
                assert call_args.kwargs["method"] == "GET"


def _build_property_translation_response(set_id: str, include_set: bool = True) -> dict:
    """Helper to build Books API property translation response."""
    instances = [
        {"source": "HAAS", "propertyInstanceSourceId": "8135-ha"},
        {"source": "OS", "propertyInstanceSourceId": "4341849"},
        {"source": "VES", "propertyInstanceSourceId": "906"},
    ]

    if include_set:
        instances.insert(1, {"source": "SET", "propertyInstanceSourceId": set_id})

    return {
        "data": {
            "type": "propertyinstanceids",
            "attributes": {
                "propertyInstanceSourceId": set_id,
                "source": "SET",
                "translatedPropertyInstances": instances,
            },
        }
    }


class TestGetPropertyId:
    """Tests for get_property_id function."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "include_set,expected_id,expected_error",
        [
            (True, "91d754cb-549e-473a-a420-6804f870c54a", None),  # Success case
            (False, None, "SET ID not found in translation response"),  # Missing SET ID
        ],
    )
    async def test_get_property_id(self, include_set, expected_id, expected_error):
        """Test get_property_id extraction and error handling."""
        uc_property_id = "test-uc-property-456"

        mock_response = _build_property_translation_response(expected_id or "some-id", include_set)

        mock_make_api_call_str = "agent_leasing.agent.tools.emergency_service_transfer.basic.emergency_service_transfer_basic._make_api_call"
        mock_auth_token_str = "agent_leasing.agent.tools.emergency_service_transfer.basic.emergency_service_transfer_basic.get_books_auth_token"

        with (
            patch(mock_make_api_call_str, new_callable=AsyncMock) as mock_api_call,
            patch(mock_auth_token_str, new_callable=AsyncMock) as mock_auth_token,
        ):
            mock_api_call.return_value = mock_response
            mock_auth_token.return_value = "fake-token-123"

            if expected_error:
                with pytest.raises(ValueError, match=expected_error):
                    await get_property_id(uc_property_id)
            else:
                result = await get_property_id(uc_property_id)
                assert result == expected_id
                mock_api_call.assert_called_once()
                call_args = mock_api_call.call_args
                assert "propertyinstance/test-uc-property-456/OS" in call_args.kwargs["url"]
                assert call_args.kwargs["method"] == "GET"


class TestGetEmergencyNumber:
    """Tests for get_emergency_number function."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "emergency_phone,expected,expected_error",
        [
            ("+12025551234", "+12025551234", None),  # Valid E164 format
            ("(202) 555-1234", "+12025551234", None),  # Valid US format
            (None, None, EMERGENCY_NOT_FOUND_KB_ERROR),  # Missing emergency number
            ("invalid", None, FAILED_TO_PARSE_ERROR_PATTERN),  # Invalid phone number
        ],
    )
    async def test_get_emergency_number(self, emergency_phone, expected, expected_error):
        """Test get_emergency_number extraction and error handling."""
        company_id = "test-company-123"
        property_id = "test-property-456"

        mock_response = _build_kb_response(emergency_phone)

        mock_make_api_call_str = "agent_leasing.agent.tools.emergency_service_transfer.basic.emergency_service_transfer_basic._make_api_call"
        mock_auth_token_str = "agent_leasing.agent.tools.emergency_service_transfer.basic.emergency_service_transfer_basic.get_books_auth_token"

        with (
            patch(mock_make_api_call_str, new_callable=AsyncMock) as mock_api_call,
            patch(mock_auth_token_str, new_callable=AsyncMock) as mock_auth_token,
        ):
            mock_api_call.return_value = mock_response
            mock_auth_token.return_value = "fake-token-123"

            if expected_error:
                with pytest.raises(ValueError, match=expected_error):
                    await get_emergency_number(company_id, property_id)
            else:
                result = await get_emergency_number(company_id, property_id)
                assert result == expected
                mock_api_call.assert_called_once()
                call_args = mock_api_call.call_args
                assert f"companies/{company_id}/properties/{property_id}" in call_args.kwargs["url"]
                assert call_args.kwargs["method"] == "POST"


class TestRedirectToNumberViaTwilio:
    """Tests for redirect_to_number_via_twilio function."""

    @pytest.mark.asyncio
    async def test_redirects_call_to_emergency_number(self):
        """Test that redirect_to_number_via_twilio updates Twilio call with correct TwiML."""
        # Arrange
        call_sid = "test-call-sid-123"
        emergency_number = "+12025551234"

        mock_call = Mock()
        mock_call.status = "in-progress"
        mock_twilio_client = Mock()
        mock_twilio_client.calls.return_value.update.return_value = mock_call

        # Act
        mock_get_creds_str = "agent_leasing.agent.tools.emergency_service_transfer.basic.emergency_service_transfer_basic.get_twilio_credentials"
        mock_client_class_str = (
            "agent_leasing.agent.tools.emergency_service_transfer.basic.emergency_service_transfer_basic.TwilioClient"
        )

        with patch(mock_get_creds_str) as mock_get_creds:
            with patch(mock_client_class_str) as mock_client_class:
                mock_get_creds.return_value = (
                    "api_key",
                    "api_secret",
                    "account_sid",
                )
                mock_client_class.return_value = mock_twilio_client

                await redirect_to_number_via_twilio(call_sid, emergency_number)

        # Assert
        mock_get_creds.assert_called_once()
        mock_client_class.assert_called_once_with("api_key", "api_secret", "account_sid")
        mock_twilio_client.calls.assert_called_once_with("test-call-sid-123")

        # Verify TwiML contains the emergency number
        call_args = mock_twilio_client.calls.return_value.update.call_args
        twiml = call_args.kwargs["twiml"]
        assert emergency_number in twiml
        assert "<Dial>" in twiml
        assert "<Pause" in twiml


class TestEmergencyServiceTransferBasicIntegration:
    """Integration tests for emergency_service_transfer_basic tool via FunctionTool interface."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "already_created, channel, payload_emergency_number, api_emergency_number, expected_in_result",
        [
            # Request not created → should return NEVER_CALL
            (False, "VOICE", None, None, NEVER_CALL_MESSAGE),
            (False, "SMS", None, None, NEVER_CALL_MESSAGE),
            # Voice channel, should transfer (payload number)
            (True, "VOICE", "+12025551234", None, VOICE_REDIRECT_MESSAGE_TEMPLATE),
            # Non-voice channels
            (True, "SMS", "+12025551234", None, CALL_RESIDENT_MESSAGE_TEMPLATE),
            (True, "EMAIL", "+12025551234", None, CALL_RESIDENT_MESSAGE_TEMPLATE),
            (True, "CHAT", "+12025551234", None, CALL_RESIDENT_MESSAGE_TEMPLATE),
            # Voice channel, should transfer (API number)
            (True, "VOICE", None, "+12025551234", VOICE_REDIRECT_MESSAGE_TEMPLATE),
            # Non-voice channels (API number)
            (True, "SMS", None, "+12025551234", CALL_RESIDENT_MESSAGE_TEMPLATE),
            (True, "EMAIL", None, "+12025551234", CALL_RESIDENT_MESSAGE_TEMPLATE),
            (True, "CHAT", None, "+12025551234", CALL_RESIDENT_MESSAGE_TEMPLATE),
            # No emergency number from payload or API → should return error
            (True, "VOICE", None, None, EMERGENCY_NOT_FOUND_ERROR),
            (True, "SMS", None, None, EMERGENCY_NOT_FOUND_ERROR),
            (True, "EMAIL", None, None, EMERGENCY_NOT_FOUND_ERROR),
            (True, "CHAT", None, None, EMERGENCY_NOT_FOUND_ERROR),
            # Both payload and API number
            (True, "VOICE", "+12025551234", "+12025551234", VOICE_REDIRECT_MESSAGE_TEMPLATE),
            (True, "SMS", "+12025551234", "+12025551234", CALL_RESIDENT_MESSAGE_TEMPLATE),
            (True, "EMAIL", "+12025551234", "+12025551234", CALL_RESIDENT_MESSAGE_TEMPLATE),
            (True, "CHAT", "+12025551234", "+12025551234", CALL_RESIDENT_MESSAGE_TEMPLATE),
        ],
    )
    async def test_emergency_service_transfer_basic_via_tool_interface(
        self,
        already_created,
        channel,
        payload_emergency_number,
        api_emergency_number,
        expected_in_result,
    ):
        """Test emergency_service_transfer_basic through the FunctionTool.on_invoke_tool interface."""
        import json

        from agents import tool_context

        # Create a properly structured mock
        # The FunctionTool passes ToolContext to the function, but the function signature
        # expects RunContextWrapper. ToolContext.run_context is the RunContextWrapper.
        # However, the decorator passes ToolContext directly when takes_context=True.
        # So we need to make ToolContext look like RunContextWrapper with a .context attribute
        mock_context = Mock()
        # Set product for channel detection
        mock_context.ask_request.product = f"resident_one_{channel.lower()}"
        mock_context.ask_request.product_info.uc_company_id.id = "test-company"
        mock_context.ask_request.product_info.uc_property_id.id = "test-property"
        mock_context.ask_request.product_info.call_sid = "test-call-sid"
        mock_context.ask_request.product_info.emerg_phone = payload_emergency_number
        mock_context.disabled_modules = []
        mock_context.call_management_in_progress = False

        # Mock call_state_manager for voice channel playback waiting
        from agent_leasing.util.call_state_manager import PlaybackWaitResult

        mock_call_state = Mock()
        mock_call_state.wait_for_message_playback = AsyncMock(
            return_value=PlaybackWaitResult(success=True, started=True, completed=True)
        )
        mock_context.call_state_manager = mock_call_state

        mock_tool_ctx = Mock(spec=tool_context.ToolContext)
        mock_tool_ctx.context = mock_context
        mock_tool_ctx.tool_name = "emergency_service_transfer_basic"

        # Prepare JSON input for the tool
        json_input = json.dumps(
            {
                "already_created_emergency_service_request": already_created,
                "service_request_summary": "Resident emergency",
            }
        )

        # Mock the sub-functions

        mock_get_ids_str = (
            "agent_leasing.agent.tools.emergency_service_transfer.basic.emergency_service_transfer_basic.get_books_ids"
        )
        mock_get_emergency_number_str = "agent_leasing.agent.tools.emergency_service_transfer.basic.emergency_service_transfer_basic.get_emergency_number"
        mock_redirect_to_number_via_twilio_str = "agent_leasing.agent.tools.emergency_service_transfer.basic.emergency_service_transfer_basic.redirect_to_number_via_twilio"

        with patch(mock_get_ids_str, new_callable=AsyncMock) as mock_get_ids:
            with patch(mock_get_emergency_number_str, new_callable=AsyncMock) as mock_get_number:
                with patch(mock_redirect_to_number_via_twilio_str, new_callable=AsyncMock) as mock_redirect:
                    mock_get_ids.return_value = ("company-123", "property-456")
                    mock_get_number.return_value = api_emergency_number

                    result = await estb_module.emergency_service_transfer_basic.on_invoke_tool(
                        mock_tool_ctx, json_input
                    )
                    effective_number = payload_emergency_number or api_emergency_number
                    assert expected_in_result.format(emergency_number=effective_number) in result

                    # we skip the calls in the function, if the booleans are false
                    # don't assert in those cases
                    if already_created:
                        # get_books_ids is deferred — only called when payload emerg_phone is invalid
                        if payload_emergency_number:
                            mock_get_ids.assert_not_called()
                            mock_get_number.assert_not_called()
                        else:
                            mock_get_ids.assert_called_once()
                            mock_get_number.assert_called_once_with("company-123", "property-456")

                        if channel == "VOICE" and effective_number:
                            mock_redirect.assert_called_once()
                        else:
                            mock_redirect.assert_not_called()


class TestMakeApiCallRetry:
    """Tests for _make_api_call retry logic on transient connection errors."""

    _MODULE = "agent_leasing.agent.tools.emergency_service_transfer.http_util"

    @pytest.mark.asyncio
    async def test_retries_on_client_connector_error_and_succeeds(self):
        """First attempt fails with ClientConnectorError, second succeeds."""
        expected = {"data": "ok"}

        with patch(f"{self._MODULE}._make_api_call_once", new_callable=AsyncMock) as mock_once:
            mock_once.side_effect = [
                aiohttp.ClientConnectorError(connection_key=Mock(), os_error=OSError("reset")),
                expected,
            ]
            with patch(f"{self._MODULE}.asyncio.sleep", new_callable=AsyncMock):
                result = await _make_api_call(
                    url="https://example.com", payload={}, headers={}, api_name="Test", method="GET"
                )

        assert result == expected
        assert mock_once.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_retry_exhaustion(self):
        """Both attempts fail — should raise the last exception."""
        with patch(f"{self._MODULE}._make_api_call_once", new_callable=AsyncMock) as mock_once:
            mock_once.side_effect = [
                aiohttp.ServerDisconnectedError("gone"),
                aiohttp.ServerDisconnectedError("still gone"),
            ]
            with patch(f"{self._MODULE}.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(aiohttp.ServerDisconnectedError):
                    await _make_api_call(
                        url="https://example.com", payload={}, headers={}, api_name="Test", method="GET"
                    )

        assert mock_once.call_count == 2

    @pytest.mark.asyncio
    async def test_does_not_retry_on_runtime_error(self):
        """Non-transient errors (e.g. RuntimeError from 4xx) should not be retried."""
        with patch(f"{self._MODULE}._make_api_call_once", new_callable=AsyncMock) as mock_once:
            mock_once.side_effect = RuntimeError("API returned status 400")
            with pytest.raises(RuntimeError, match="400"):
                await _make_api_call(url="https://example.com", payload={}, headers={}, api_name="Test", method="GET")

        assert mock_once.call_count == 1
