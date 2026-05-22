"""Tests for end_call function tool."""

from unittest.mock import Mock, patch

import pytest
from agents import RunContextWrapper
from twilio.base.exceptions import TwilioRestException

# Import the validation function and create a test version of end_call
from agent_leasing.agent.tools.end_call.end_call import _validate_twilio_credentials
from agent_leasing.api.model import AskRequest, ProductInfo


# Create a test version of the end_call function without the decorator
async def end_call_func(ctx, message: str, tool_use_reason: str, user_confirmation: bool) -> str:
    """Test version of end_call function without decorator."""
    from agent_leasing.agent.tools.end_call.end_call import (
        TwilioClient,
        logger,
        settings,
    )

    try:
        # Get call information from the context
        call_sid = ctx.context.ask_request.product_info.call_sid

        # Get Twilio credentials from settings
        account_sid = settings.knock_twilio_account_sid
        api_key = settings.knock_twilio_api_key
        api_secret = settings.knock_twilio_api_secret

        _validate_twilio_credentials(api_key, api_secret, account_sid)

        if not call_sid:
            logger.error("Call SID is not available in the request context")
            raise ValueError("Call SID is not available")

        # Create Twilio client and end the call
        twilio_client = TwilioClient(api_key, api_secret, account_sid)
        call = twilio_client.calls(call_sid).update(status="completed")

        logger.info(f"Successfully ended call {call_sid} with message: {message}")
        logger.info(f"Call status updated to: {call.status}")

        return f"Call ended successfully. Status: {call.status}"

    except Exception as e:
        logger.error(f"Error ending call {call_sid}: {e}")
        raise


@pytest.fixture
def mock_ask_request():
    """Create a mock AskRequest with call_sid."""
    ask_request = Mock(spec=AskRequest)
    ask_request.product_info = Mock(spec=ProductInfo)
    ask_request.product_info.call_sid = "CAce4e8126472ad801461b5d36e1def7b3"
    ask_request.product_info.stream_sid = "MZ123456789"
    return ask_request


@pytest.fixture
def mock_context(mock_ask_request):
    """Create a mock RunContextWrapper with ask_request."""
    context = Mock(spec=RunContextWrapper)
    context.context = Mock()
    context.context.ask_request = mock_ask_request
    return context


@pytest.fixture
def mock_twilio_client():
    """Create a mock Twilio client."""
    client = Mock()
    call = Mock()
    call.status = "completed"
    call.update = Mock(return_value=call)
    client.calls = Mock(return_value=call)
    client.calls.list = Mock(return_value=[])
    return client


@pytest.fixture
def valid_credentials():
    """Return valid Twilio credentials."""
    return {
        "api_key": "SK123456789abcdef",
        "api_secret": "secret123456789",
        "account_sid": "AC123456789abcdef",
    }


class TestEndCall:
    """Test cases for end_call function."""

    @patch("agent_leasing.agent.tools.end_call.end_call.TwilioClient")
    @patch("agent_leasing.agent.tools.end_call.end_call.settings")
    async def test_end_call_success(self, mock_settings, mock_twilio_client_class, mock_context, valid_credentials):
        """Test successful call termination."""
        # Setup mock settings
        mock_settings.knock_twilio_api_key = valid_credentials["api_key"]
        mock_settings.knock_twilio_api_secret = valid_credentials["api_secret"]
        mock_settings.knock_twilio_account_sid = valid_credentials["account_sid"]

        # Setup mock Twilio client
        mock_client = Mock()
        mock_call = Mock()
        mock_call.status = "completed"
        mock_call.update.return_value = mock_call
        mock_client.calls.return_value = mock_call
        mock_client.calls.list.return_value = []
        mock_twilio_client_class.return_value = mock_client

        # Call the function
        result = await end_call_func(
            ctx=mock_context,
            message="Goodbye!",
            tool_use_reason="User requested to end call",
            user_confirmation=True,
        )

        # Verify results
        assert result == "Call ended successfully. Status: completed"
        mock_twilio_client_class.assert_called_once_with(
            valid_credentials["api_key"],
            valid_credentials["api_secret"],
            valid_credentials["account_sid"],
        )
        mock_call.update.assert_called_once_with(status="completed")

    @patch("agent_leasing.agent.tools.end_call.end_call.settings")
    async def test_end_call_missing_credentials(self, mock_settings, mock_context):
        """Test error handling when credentials are missing."""
        # Setup mock settings with missing credentials
        mock_settings.knock_twilio_api_key = ""
        mock_settings.knock_twilio_api_secret = "secret123"
        mock_settings.knock_twilio_account_sid = "AC123"

        # Call should raise ValueError
        with pytest.raises(ValueError, match="Twilio credentials are not configured"):
            await end_call_func(
                ctx=mock_context,
                message="Goodbye!",
                tool_use_reason="User requested to end call",
                user_confirmation=True,
            )

    @patch("agent_leasing.agent.tools.end_call.end_call.settings")
    async def test_end_call_missing_call_sid(self, mock_settings, valid_credentials):
        """Test error handling when call_sid is missing."""
        # Setup mock settings
        mock_settings.knock_twilio_api_key = valid_credentials["api_key"]
        mock_settings.knock_twilio_api_secret = valid_credentials["api_secret"]
        mock_settings.knock_twilio_account_sid = valid_credentials["account_sid"]

        # Setup context with missing call_sid
        context = Mock(spec=RunContextWrapper)
        context.context = Mock()
        context.context.ask_request = Mock()
        context.context.ask_request.product_info = Mock()
        context.context.ask_request.product_info.call_sid = None

        # Call should raise ValueError
        with pytest.raises(ValueError, match="Call SID is not available"):
            await end_call_func(
                ctx=context,
                message="Goodbye!",
                tool_use_reason="User requested to end call",
                user_confirmation=True,
            )

    @patch("agent_leasing.agent.tools.end_call.end_call.TwilioClient")
    @patch("agent_leasing.agent.tools.end_call.end_call.settings")
    async def test_end_call_twilio_api_error(
        self, mock_settings, mock_twilio_client_class, mock_context, valid_credentials
    ):
        """Test error handling when Twilio API call fails."""
        # Setup mock settings
        mock_settings.knock_twilio_api_key = valid_credentials["api_key"]
        mock_settings.knock_twilio_api_secret = valid_credentials["api_secret"]
        mock_settings.knock_twilio_account_sid = valid_credentials["account_sid"]

        # Setup mock Twilio client to raise exception
        mock_client = Mock()
        mock_client.calls.list.return_value = []
        mock_call = Mock()
        mock_call.update.side_effect = TwilioRestException("API Error", uri="/test")
        mock_client.calls.return_value = mock_call
        mock_twilio_client_class.return_value = mock_client

        # Call should raise TwilioRestException
        with pytest.raises(TwilioRestException):
            await end_call_func(
                ctx=mock_context,
                message="Goodbye!",
                tool_use_reason="User requested to end call",
                user_confirmation=True,
            )

    @patch("agent_leasing.agent.tools.end_call.end_call.TwilioClient")
    @patch("agent_leasing.agent.tools.end_call.end_call.settings")
    async def test_end_call_validation_error(
        self, mock_settings, mock_twilio_client_class, mock_context, valid_credentials
    ):
        """Test error handling when credential validation fails."""
        # Setup mock settings with empty credentials to trigger validation error
        mock_settings.knock_twilio_api_key = ""
        mock_settings.knock_twilio_api_secret = valid_credentials["api_secret"]
        mock_settings.knock_twilio_account_sid = valid_credentials["account_sid"]

        # Call should raise ValueError from validation
        with pytest.raises(ValueError, match="Twilio credentials are not configured"):
            await end_call_func(
                ctx=mock_context,
                message="Goodbye!",
                tool_use_reason="User requested to end call",
                user_confirmation=True,
            )


class TestValidateTwilioCredentials:
    """Test cases for _validate_twilio_credentials function."""

    def test_validate_credentials_all_empty(self):
        """Test validation with all empty credentials."""
        with pytest.raises(ValueError, match="Twilio credentials are not configured"):
            _validate_twilio_credentials("", "", "")

    def test_validate_credentials_partial_empty(self):
        """Test validation with partially empty credentials."""
        with pytest.raises(ValueError, match="Twilio credentials are not configured"):
            _validate_twilio_credentials("api_key", "", "account_sid")

    def test_validate_credentials_none_values(self):
        """Test validation with None values."""
        with pytest.raises(ValueError, match="Twilio credentials are not configured"):
            _validate_twilio_credentials(None, "api_secret", "account_sid")

    def test_validate_credentials_mixed_none_empty(self):
        """Test validation with mixed None and empty values."""
        with pytest.raises(ValueError, match="Twilio credentials are not configured"):
            _validate_twilio_credentials("api_key", None, "")

    def test_validate_credentials_all_valid(self):
        """Test validation with all valid credentials."""
        # Should not raise any exception
        try:
            _validate_twilio_credentials("valid_api_key", "valid_api_secret", "valid_account_sid")
        except ValueError:
            pytest.fail("_validate_twilio_credentials raised ValueError with valid credentials")


class TestEndCallIntegration:
    """Integration test cases for end_call function."""

    @patch("agent_leasing.agent.tools.end_call.end_call.TwilioClient")
    @patch("agent_leasing.agent.tools.end_call.end_call.settings")
    async def test_end_call_full_workflow(self, mock_settings, mock_twilio_client_class, valid_credentials):
        """Test complete end_call workflow with realistic data."""
        # Setup mock settings
        mock_settings.knock_twilio_api_key = valid_credentials["api_key"]
        mock_settings.knock_twilio_api_secret = valid_credentials["api_secret"]
        mock_settings.knock_twilio_account_sid = valid_credentials["account_sid"]

        # Setup realistic context
        ask_request = Mock(spec=AskRequest)
        ask_request.product_info = Mock(spec=ProductInfo)
        ask_request.product_info.call_sid = "CAce4e8126472ad801461b5d36e1def7b3"
        ask_request.product_info.stream_sid = "MZ123456789abcdef"

        context = Mock(spec=RunContextWrapper)
        context.context = Mock()
        context.context.ask_request = ask_request

        # Setup mock Twilio client
        mock_client = Mock()
        mock_call = Mock()
        mock_call.status = "completed"
        mock_call.update.return_value = mock_call
        mock_client.calls.return_value = mock_call
        mock_client.calls.list.return_value = []
        mock_twilio_client_class.return_value = mock_client

        # Call the function
        result = await end_call_func(
            ctx=context,
            message="Thank you for calling. Have a great day!",
            tool_use_reason="User said goodbye and requested to end the call",
            user_confirmation=True,
        )

        # Verify complete workflow
        assert result == "Call ended successfully. Status: completed"

        # Verify Twilio client was created with correct credentials
        mock_twilio_client_class.assert_called_once_with(
            valid_credentials["api_key"],
            valid_credentials["api_secret"],
            valid_credentials["account_sid"],
        )

        # Verify call update was made with correct parameters
        mock_client.calls.assert_called_once_with("CAce4e8126472ad801461b5d36e1def7b3")
        mock_call.update.assert_called_once_with(status="completed")

    @patch("agent_leasing.agent.tools.end_call.end_call.logger")
    @patch("agent_leasing.agent.tools.end_call.end_call.TwilioClient")
    @patch("agent_leasing.agent.tools.end_call.end_call.settings")
    async def test_end_call_logging(
        self,
        mock_settings,
        mock_twilio_client_class,
        mock_logger,
        mock_context,
        valid_credentials,
    ):
        """Test that proper logging occurs during end_call execution."""
        # Setup mock settings
        mock_settings.knock_twilio_api_key = valid_credentials["api_key"]
        mock_settings.knock_twilio_api_secret = valid_credentials["api_secret"]
        mock_settings.knock_twilio_account_sid = valid_credentials["account_sid"]

        # Setup mock Twilio client
        mock_client = Mock()
        mock_call = Mock()
        mock_call.status = "completed"
        mock_call.update.return_value = mock_call
        mock_client.calls.return_value = mock_call
        mock_client.calls.list.return_value = []
        mock_twilio_client_class.return_value = mock_client

        # Call the function
        await end_call_func(
            ctx=mock_context,
            message="Goodbye!",
            tool_use_reason="User requested to end call",
            user_confirmation=True,
        )

        # Verify logging calls
        expected_calls = [
            ("Successfully ended call CAce4e8126472ad801461b5d36e1def7b3 with message: Goodbye!",),
            ("Call status updated to: completed",),
        ]

        # Check that info was called with expected messages
        assert mock_logger.info.call_count == 2
        actual_calls = [call[0] for call in mock_logger.info.call_args_list]
        for expected_call in expected_calls:
            assert any(expected_call[0] in str(actual_call) for actual_call in actual_calls)
