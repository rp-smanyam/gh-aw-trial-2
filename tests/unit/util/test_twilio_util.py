from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from agent_leasing.util.twilio_util import get_twilio_credentials, validate_twilio_request


class TestValidateTwilioRequest:
    """Test validate_twilio_request function."""

    @patch("agent_leasing.util.twilio_util.RequestValidator")
    @patch("agent_leasing.util.twilio_util.settings")
    @pytest.mark.asyncio
    async def test_validate_twilio_request_valid_signature(self, mock_settings, mock_validator_class):
        """Test validate_twilio_request with a valid signature."""
        mock_settings.twilio_auth_token = "test_auth_token"

        mock_validator = MagicMock()
        mock_validator.validate.return_value = True
        mock_validator_class.return_value = mock_validator

        url = "https://example.com/webhook"
        form_data = {"From": "+1234567890", "To": "+0987654321"}
        signature = "valid_signature"

        # Should not raise an exception
        await validate_twilio_request(url, form_data, signature)

        # Verify RequestValidator was created with correct auth token
        mock_validator_class.assert_called_once_with("test_auth_token")

        # Verify validate was called with correct parameters
        mock_validator.validate.assert_called_once_with(url, form_data, signature)

    @patch("agent_leasing.util.twilio_util.RequestValidator")
    @patch("agent_leasing.util.twilio_util.settings")
    @pytest.mark.asyncio
    async def test_validate_twilio_request_invalid_signature(self, mock_settings, mock_validator_class):
        """Test validate_twilio_request with an invalid signature."""
        mock_settings.twilio_auth_token = "test_auth_token"

        mock_validator = MagicMock()
        mock_validator.validate.return_value = False
        mock_validator_class.return_value = mock_validator

        url = "https://example.com/webhook"
        form_data = {"From": "+1234567890", "To": "+0987654321"}
        signature = "invalid_signature"

        # Should raise HTTPException with 403 status code
        with pytest.raises(HTTPException) as exc_info:
            await validate_twilio_request(url, form_data, signature)

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Invalid Twilio Signature"

    @patch("agent_leasing.util.twilio_util.logger")
    @patch("agent_leasing.util.twilio_util.RequestValidator")
    @patch("agent_leasing.util.twilio_util.settings")
    @pytest.mark.asyncio
    async def test_validate_twilio_request_logs_error_on_invalid(
        self, mock_settings, mock_validator_class, mock_logger
    ):
        """Test that validation failure is logged."""
        mock_settings.twilio_auth_token = "test_auth_token"

        mock_validator = MagicMock()
        mock_validator.validate.return_value = False
        mock_validator_class.return_value = mock_validator

        url = "https://example.com/webhook"
        form_data = {"From": "+1234567890"}
        signature = "bad_signature"

        with pytest.raises(HTTPException):
            await validate_twilio_request(url, form_data, signature)

        # Verify error was logged
        mock_logger.error.assert_called_once_with(f"Twilio request validation failed for {url}")

    @patch("agent_leasing.util.twilio_util.RequestValidator")
    @patch("agent_leasing.util.twilio_util.settings")
    @pytest.mark.asyncio
    async def test_validate_twilio_request_with_empty_form(self, mock_settings, mock_validator_class):
        """Test validate_twilio_request with empty form data."""
        mock_settings.twilio_auth_token = "test_auth_token"

        mock_validator = MagicMock()
        mock_validator.validate.return_value = True
        mock_validator_class.return_value = mock_validator

        url = "https://example.com/webhook"
        form_data = {}
        signature = "signature"

        await validate_twilio_request(url, form_data, signature)

        mock_validator.validate.assert_called_once_with(url, form_data, signature)

    @patch("agent_leasing.util.twilio_util.RequestValidator")
    @patch("agent_leasing.util.twilio_util.settings")
    @pytest.mark.asyncio
    async def test_validate_twilio_request_with_none_form(self, mock_settings, mock_validator_class):
        """Test validate_twilio_request with None as form data."""
        mock_settings.twilio_auth_token = "test_auth_token"

        mock_validator = MagicMock()
        mock_validator.validate.return_value = True
        mock_validator_class.return_value = mock_validator

        url = "wss://example.com/websocket"
        form_data = None
        signature = "signature"

        await validate_twilio_request(url, form_data, signature)

        mock_validator.validate.assert_called_once_with(url, form_data, signature)

    @patch("agent_leasing.util.twilio_util.RequestValidator")
    @patch("agent_leasing.util.twilio_util.settings")
    @pytest.mark.asyncio
    async def test_validate_twilio_request_with_complex_url(self, mock_settings, mock_validator_class):
        """Test validate_twilio_request with complex URL including query parameters."""
        mock_settings.twilio_auth_token = "test_auth_token"

        mock_validator = MagicMock()
        mock_validator.validate.return_value = True
        mock_validator_class.return_value = mock_validator

        url = "https://example.com/webhook?param1=value1&param2=value2"
        form_data = {"CallSid": "CA123"}
        signature = "signature"

        await validate_twilio_request(url, form_data, signature)

        mock_validator.validate.assert_called_once_with(url, form_data, signature)

    @patch("agent_leasing.util.twilio_util.RequestValidator")
    @patch("agent_leasing.util.twilio_util.settings")
    @pytest.mark.asyncio
    async def test_validate_twilio_request_uses_correct_auth_token(self, mock_settings, mock_validator_class):
        """Test that validate_twilio_request uses the correct auth token from settings."""
        custom_auth_token = "custom_secret_token_12345"
        mock_settings.twilio_auth_token = custom_auth_token

        mock_validator = MagicMock()
        mock_validator.validate.return_value = True
        mock_validator_class.return_value = mock_validator

        await validate_twilio_request("https://test.com", {}, "sig")

        # Verify the correct auth token was used
        mock_validator_class.assert_called_once_with(custom_auth_token)


class TestGetTwilioCredentials:
    """Test get_twilio_credentials function."""

    @patch("agent_leasing.util.twilio_util.settings")
    def test_get_twilio_credentials_success(self, mock_settings):
        """Test get_twilio_credentials with all credentials present."""
        mock_settings.knock_twilio_account_sid = "test_account_sid"
        mock_settings.knock_twilio_api_key = "test_api_key"
        mock_settings.knock_twilio_api_secret = "test_api_secret"

        api_key, api_secret, account_sid = get_twilio_credentials()

        assert api_key == "test_api_key"
        assert api_secret == "test_api_secret"
        assert account_sid == "test_account_sid"

    @patch("agent_leasing.util.twilio_util.settings")
    def test_get_twilio_credentials_missing_api_key(self, mock_settings):
        """Test get_twilio_credentials raises error when api_key is missing."""
        mock_settings.knock_twilio_account_sid = "test_account_sid"
        mock_settings.knock_twilio_api_key = None
        mock_settings.knock_twilio_api_secret = "test_api_secret"

        with pytest.raises(ValueError) as exc_info:
            get_twilio_credentials()

        assert "Twilio credentials are not configured" in str(exc_info.value)
        assert "api_key='None'" in str(exc_info.value)

    @patch("agent_leasing.util.twilio_util.settings")
    def test_get_twilio_credentials_missing_api_secret(self, mock_settings):
        """Test get_twilio_credentials raises error when api_secret is missing."""
        mock_settings.knock_twilio_account_sid = "test_account_sid"
        mock_settings.knock_twilio_api_key = "test_api_key"
        mock_settings.knock_twilio_api_secret = None

        with pytest.raises(ValueError) as exc_info:
            get_twilio_credentials()

        assert "Twilio credentials are not configured" in str(exc_info.value)
        assert "api_secret='None'" in str(exc_info.value)

    @patch("agent_leasing.util.twilio_util.settings")
    def test_get_twilio_credentials_missing_account_sid(self, mock_settings):
        """Test get_twilio_credentials raises error when account_sid is missing."""
        mock_settings.knock_twilio_account_sid = None
        mock_settings.knock_twilio_api_key = "test_api_key"
        mock_settings.knock_twilio_api_secret = "test_api_secret"

        with pytest.raises(ValueError) as exc_info:
            get_twilio_credentials()

        assert "Twilio credentials are not configured" in str(exc_info.value)
        assert "account_sid='None'" in str(exc_info.value)

    @patch("agent_leasing.util.twilio_util.settings")
    def test_get_twilio_credentials_all_missing(self, mock_settings):
        """Test get_twilio_credentials raises error when all credentials are missing."""
        mock_settings.knock_twilio_account_sid = None
        mock_settings.knock_twilio_api_key = None
        mock_settings.knock_twilio_api_secret = None

        with pytest.raises(ValueError) as exc_info:
            get_twilio_credentials()

        error_message = str(exc_info.value)
        assert "Twilio credentials are not configured" in error_message
        assert "api_key='None'" in error_message
        assert "api_secret='None'" in error_message
        assert "account_sid='None'" in error_message

    @patch("agent_leasing.util.twilio_util.settings")
    def test_get_twilio_credentials_empty_strings(self, mock_settings):
        """Test get_twilio_credentials raises error when credentials are empty strings."""
        mock_settings.knock_twilio_account_sid = ""
        mock_settings.knock_twilio_api_key = ""
        mock_settings.knock_twilio_api_secret = ""

        with pytest.raises(ValueError) as exc_info:
            get_twilio_credentials()

        assert "Twilio credentials are not configured" in str(exc_info.value)
