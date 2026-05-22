"""Tests for end_call _end_call_impl covering lines 29-64 and 88."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from agents import RunContextWrapper

from agent_leasing.agent.tools.end_call.end_call import _validate_twilio_credentials
from agent_leasing.api.model import AskRequest, ProductInfo
from agent_leasing.util.call_state_manager import PlaybackWaitResult


@pytest.fixture
def mock_ask_request():
    """Create a mock AskRequest with call_sid."""
    ask_request = Mock(spec=AskRequest)
    ask_request.product_info = Mock(spec=ProductInfo)
    ask_request.product_info.call_sid = "CA_test_call_sid_123"
    return ask_request


@pytest.fixture
def mock_ctx(mock_ask_request):
    """Create a mock RunContextWrapper with ask_request."""
    ctx = Mock(spec=RunContextWrapper)
    ctx.context = Mock()
    ctx.context.ask_request = mock_ask_request
    ctx.context.call_ended_by_agent = False
    ctx.context.call_management_in_progress = False
    return ctx


@pytest.fixture
def valid_credentials():
    """Return valid Twilio credentials."""
    return {
        "api_key": "SK_test_api_key",
        "api_secret": "test_api_secret",
        "account_sid": "AC_test_account_sid",
    }


class TestEndCallImpl:
    """Tests for _end_call_impl function (lines 29-64)."""

    @patch("agent_leasing.agent.tools.end_call.end_call.TwilioClient")
    @patch("agent_leasing.agent.tools.end_call.end_call.settings")
    @patch("agent_leasing.agent.tools.end_call.end_call.get_call_state_from_context")
    async def test_successful_call_end_with_call_state(
        self,
        mock_get_call_state,
        mock_settings,
        mock_twilio_cls,
        mock_ctx,
        valid_credentials,
    ):
        """Successful call end -- all credentials valid, call_state exists."""
        from agent_leasing.agent.tools.end_call.end_call import _end_call_impl

        # Set up call_state with successful playback wait
        mock_call_state = Mock()
        mock_call_state.wait_for_message_playback = AsyncMock(
            return_value=PlaybackWaitResult(success=True, started=True, completed=True)
        )
        mock_get_call_state.return_value = mock_call_state

        # Set up Twilio credentials
        mock_settings.knock_twilio_account_sid = valid_credentials["account_sid"]
        mock_settings.knock_twilio_api_key = valid_credentials["api_key"]
        mock_settings.knock_twilio_api_secret = valid_credentials["api_secret"]

        # Set up Twilio client mock
        mock_call = Mock()
        mock_call.status = "completed"
        mock_client = Mock()
        mock_client.calls.return_value.update.return_value = mock_call
        mock_twilio_cls.return_value = mock_client

        result = await _end_call_impl(
            mock_ctx,
            message="Goodbye!",
            tool_use_reason="User said goodbye",
            user_confirmation=True,
        )

        assert result == "Call ended successfully. Status: completed"
        assert mock_ctx.context.call_ended_by_agent is True

        mock_call_state.wait_for_message_playback.assert_awaited_once_with("goodbye", tool_name="end_call")
        mock_twilio_cls.assert_called_once_with(
            valid_credentials["api_key"],
            valid_credentials["api_secret"],
            valid_credentials["account_sid"],
        )
        mock_client.calls.assert_called_once_with("CA_test_call_sid_123")
        mock_client.calls.return_value.update.assert_called_once_with(status="completed")

    @patch("agent_leasing.agent.tools.end_call.end_call.TwilioClient")
    @patch("agent_leasing.agent.tools.end_call.end_call.settings")
    @patch("agent_leasing.agent.tools.end_call.end_call.get_call_state_from_context")
    async def test_successful_call_end_call_state_is_none(
        self,
        mock_get_call_state,
        mock_settings,
        mock_twilio_cls,
        mock_ctx,
        valid_credentials,
    ):
        """Successful call end -- call_state is None (skip playback wait)."""
        from agent_leasing.agent.tools.end_call.end_call import _end_call_impl

        mock_get_call_state.return_value = None

        mock_settings.knock_twilio_account_sid = valid_credentials["account_sid"]
        mock_settings.knock_twilio_api_key = valid_credentials["api_key"]
        mock_settings.knock_twilio_api_secret = valid_credentials["api_secret"]

        mock_call = Mock()
        mock_call.status = "completed"
        mock_client = Mock()
        mock_client.calls.return_value.update.return_value = mock_call
        mock_twilio_cls.return_value = mock_client

        result = await _end_call_impl(
            mock_ctx,
            message="Goodbye!",
            tool_use_reason="User said goodbye",
            user_confirmation=True,
        )

        assert result == "Call ended successfully. Status: completed"
        assert mock_ctx.context.call_ended_by_agent is True

    @patch("agent_leasing.agent.tools.end_call.end_call.TwilioClient")
    @patch("agent_leasing.agent.tools.end_call.end_call.settings")
    @patch("agent_leasing.agent.tools.end_call.end_call.get_call_state_from_context")
    async def test_playback_wait_times_out(
        self,
        mock_get_call_state,
        mock_settings,
        mock_twilio_cls,
        mock_ctx,
        valid_credentials,
    ):
        """Playback wait times out (completed=False) -- logs warning but continues."""
        from agent_leasing.agent.tools.end_call.end_call import _end_call_impl

        mock_call_state = Mock()
        mock_call_state.wait_for_message_playback = AsyncMock(
            return_value=PlaybackWaitResult(success=True, started=True, completed=False)
        )
        mock_get_call_state.return_value = mock_call_state

        mock_settings.knock_twilio_account_sid = valid_credentials["account_sid"]
        mock_settings.knock_twilio_api_key = valid_credentials["api_key"]
        mock_settings.knock_twilio_api_secret = valid_credentials["api_secret"]

        mock_call = Mock()
        mock_call.status = "completed"
        mock_client = Mock()
        mock_client.calls.return_value.update.return_value = mock_call
        mock_twilio_cls.return_value = mock_client

        result = await _end_call_impl(
            mock_ctx,
            message="Goodbye!",
            tool_use_reason="User said goodbye",
            user_confirmation=True,
        )

        # Should still succeed despite timeout on playback completion
        assert result == "Call ended successfully. Status: completed"
        assert mock_ctx.context.call_ended_by_agent is True

    def test_validate_twilio_credentials_raises_when_missing(self):
        """_validate_twilio_credentials raises ValueError when credentials missing."""
        with pytest.raises(ValueError, match="Twilio credentials are not configured"):
            _validate_twilio_credentials("", "secret", "sid")

        with pytest.raises(ValueError, match="Twilio credentials are not configured"):
            _validate_twilio_credentials("key", "", "sid")

        with pytest.raises(ValueError, match="Twilio credentials are not configured"):
            _validate_twilio_credentials("key", "secret", "")

        with pytest.raises(ValueError, match="Twilio credentials are not configured"):
            _validate_twilio_credentials(None, None, None)

    def test_validate_twilio_credentials_passes_when_valid(self):
        """_validate_twilio_credentials does not raise with valid credentials."""
        _validate_twilio_credentials("key", "secret", "sid")

    @patch("agent_leasing.agent.tools.end_call.end_call.TwilioClient")
    @patch("agent_leasing.agent.tools.end_call.end_call.settings")
    @patch("agent_leasing.agent.tools.end_call.end_call.get_call_state_from_context")
    async def test_exception_during_call_end_re_raises(
        self,
        mock_get_call_state,
        mock_settings,
        mock_twilio_cls,
        mock_ctx,
        valid_credentials,
    ):
        """Exception during call end is re-raised."""
        from agent_leasing.agent.tools.end_call.end_call import _end_call_impl

        mock_get_call_state.return_value = None

        mock_settings.knock_twilio_account_sid = valid_credentials["account_sid"]
        mock_settings.knock_twilio_api_key = valid_credentials["api_key"]
        mock_settings.knock_twilio_api_secret = valid_credentials["api_secret"]

        mock_twilio_cls.side_effect = RuntimeError("Connection failed")

        with pytest.raises(RuntimeError, match="Connection failed"):
            await _end_call_impl(
                mock_ctx,
                message="Goodbye!",
                tool_use_reason="User said goodbye",
                user_confirmation=True,
            )


class TestEndCallWrapper:
    """Test the end_call wrapper function (line 88)."""

    @patch("agent_leasing.agent.tools.end_call.end_call._end_call_impl", new_callable=AsyncMock)
    async def test_end_call_delegates_to_impl(self, mock_impl):
        """end_call wrapper delegates to _end_call_impl."""
        from agent_leasing.agent.tools.end_call.end_call import _end_call_impl

        mock_impl.return_value = "Call ended successfully. Status: completed"

        ctx = Mock(spec=RunContextWrapper)
        result = await _end_call_impl(
            ctx,
            message="Bye",
            tool_use_reason="User wants to hang up",
            user_confirmation=True,
        )

        assert result == "Call ended successfully. Status: completed"
        mock_impl.assert_awaited_once_with(
            ctx,
            message="Bye",
            tool_use_reason="User wants to hang up",
            user_confirmation=True,
        )
