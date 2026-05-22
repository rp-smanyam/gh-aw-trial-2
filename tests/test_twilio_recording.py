"""Unit tests for Twilio recording functionality in TwilioHandler."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from agent_leasing.twilio_handler import TwilioHandler


class TestTwilioRecording:
    """Tests for the _start_recording method in TwilioHandler."""

    @pytest.fixture
    def mock_websocket(self):
        """Create a mock WebSocket."""
        websocket = AsyncMock()
        return websocket

    @pytest.fixture
    def handler(self, mock_websocket):
        """Create a TwilioHandler instance with mocked dependencies."""
        return TwilioHandler(mock_websocket)

    @pytest.mark.parametrize(
        "should_record,expected_calls",
        [
            (True, 1),  # Recording should be started
            (False, 0),  # Recording should not be started
        ],
    )
    @patch("agent_leasing.twilio_handler.TwilioClient")
    async def test_start_recording_with_should_record_flag(
        self, mock_twilio_client_class, handler, should_record, expected_calls
    ):
        """Test that recording starts only when should_record flag is True."""
        # Arrange
        mock_client_instance = Mock()
        mock_recordings = Mock()
        mock_twilio_client_class.return_value = mock_client_instance
        mock_client_instance.calls.return_value.recordings = mock_recordings

        payload = {
            "product_info": {
                "should_record": should_record,
                "call_sid": "CA123",
            }
        }
        call_sid = "CA123456789"

        # Act
        await handler._start_recording(payload, call_sid)

        # Give asyncio.create_task time to execute if it was called
        await asyncio.sleep(0.1)

        # Assert
        if expected_calls > 0:
            mock_twilio_client_class.assert_called_once()
            mock_client_instance.calls.assert_called_once_with(call_sid)
        else:
            mock_twilio_client_class.assert_not_called()

    @patch("agent_leasing.twilio_handler.TwilioClient")
    async def test_start_recording_missing_should_record_key(self, mock_twilio_client_class, handler):
        """Test that recording is skipped when should_record key is missing."""
        # Arrange
        payload = {
            "product_info": {
                "call_sid": "CA123",
            }
        }
        call_sid = "CA123456789"

        # Act
        await handler._start_recording(payload, call_sid)

        # Assert
        mock_twilio_client_class.assert_not_called()

    @patch("agent_leasing.twilio_handler.TwilioClient")
    async def test_start_recording_missing_product_info(self, mock_twilio_client_class, handler):
        """Test that recording is skipped when product_info is missing."""
        # Arrange
        payload = {}
        call_sid = "CA123456789"

        # Act
        await handler._start_recording(payload, call_sid)

        # Assert
        mock_twilio_client_class.assert_not_called()

    @patch("agent_leasing.twilio_handler.TwilioClient")
    @patch("agent_leasing.twilio_handler.asyncio.create_task")
    @patch("agent_leasing.twilio_handler.asyncio.to_thread")
    @patch("agent_leasing.twilio_handler.settings")
    async def test_start_recording_uses_asyncio_to_thread(
        self, mock_settings, mock_to_thread, mock_create_task, mock_twilio_client_class, handler
    ):
        """Test that recording uses asyncio.to_thread to offload synchronous call."""
        # Arrange
        mock_settings.knock_internal_api_url = "https://api.example.com"
        mock_client_instance = Mock()
        mock_recordings = Mock()
        mock_twilio_client_class.return_value = mock_client_instance
        mock_client_instance.calls.return_value.recordings = mock_recordings
        mock_to_thread.return_value = AsyncMock()
        mock_create_task.return_value = AsyncMock()

        payload = {
            "product_info": {
                "should_record": True,
            }
        }
        call_sid = "CA123456789"

        # Act
        await handler._start_recording(payload, call_sid)

        # Assert
        mock_create_task.assert_called_once()
        # Verify asyncio.to_thread called with correct callable and parameters
        mock_to_thread.assert_called_once_with(
            mock_recordings.create,
            recording_status_callback="https://api.example.com/v1/relay/voice/handlers/hangup-with-recording",
            recording_channels="dual",
        )

    @patch("agent_leasing.twilio_handler.TwilioClient")
    @patch("agent_leasing.twilio_handler.asyncio.create_task")
    @patch("agent_leasing.twilio_handler.settings")
    async def test_start_recording_fallback_on_task_creation_failure(
        self, mock_settings, mock_create_task, mock_twilio_client_class, handler
    ):
        """Test that recording falls back to blocking call if task creation fails."""
        # Arrange
        mock_settings.knock_internal_api_url = "https://api.example.com"

        mock_client_instance = Mock()
        mock_recordings = Mock()
        mock_create_recording = Mock()
        mock_twilio_client_class.return_value = mock_client_instance
        mock_client_instance.calls.return_value.recordings = mock_recordings
        mock_recordings.create = mock_create_recording

        # Simulate task creation failure (raise exception immediately)
        mock_create_task.side_effect = Exception("Task creation failed")

        payload = {
            "product_info": {
                "should_record": True,
            }
        }
        call_sid = "CA123456789"

        # Act
        await handler._start_recording(payload, call_sid)

        # Assert
        mock_create_task.assert_called_once()
        # Fallback should be called with correct parameters
        mock_create_recording.assert_called_once_with(
            recording_status_callback="https://api.example.com/v1/relay/voice/handlers/hangup-with-recording",
            recording_channels="dual",
        )

    @pytest.mark.parametrize(
        "exception_type,exception_message",
        [
            (ConnectionError, "Connection to Twilio failed"),
            (TimeoutError, "Twilio API timeout"),
            (RuntimeError, "Unexpected runtime error"),
        ],
    )
    @patch("agent_leasing.twilio_handler.TwilioClient")
    @patch("agent_leasing.twilio_handler.logger")
    async def test_start_recording_handles_twilio_api_errors(
        self, mock_logger, mock_twilio_client_class, handler, exception_type, exception_message
    ):
        """Test that Twilio API errors are caught and logged."""
        # Arrange
        mock_client_instance = Mock()
        mock_twilio_client_class.return_value = mock_client_instance
        mock_client_instance.calls.side_effect = exception_type(exception_message)

        payload = {
            "product_info": {
                "should_record": True,
            }
        }
        call_sid = "CA123456789"

        # Act
        await handler._start_recording(payload, call_sid)

        # Assert
        mock_logger.error.assert_called_once()
        error_call_args = mock_logger.error.call_args[0][0]
        assert "Error starting Twilio recording" in error_call_args
        assert call_sid in error_call_args

    @patch("agent_leasing.twilio_handler.TwilioClient")
    @patch("agent_leasing.twilio_handler.logger")
    async def test_start_recording_logs_success(self, mock_logger, mock_twilio_client_class, handler):
        """Test that successful recording start is logged."""
        # Arrange
        mock_client_instance = Mock()
        mock_recordings = Mock()
        mock_twilio_client_class.return_value = mock_client_instance
        mock_client_instance.calls.return_value.recordings = mock_recordings

        payload = {
            "product_info": {
                "should_record": True,
            }
        }
        call_sid = "CA123456789"

        # Act
        await handler._start_recording(payload, call_sid)

        # Give asyncio.create_task time to execute
        await asyncio.sleep(0.1)

        # Assert
        mock_logger.info.assert_called()
        info_calls = [call[0][0] for call in mock_logger.info.call_args_list]
        assert any("Recording started for call" in call and call_sid in call for call in info_calls)

    @patch("agent_leasing.twilio_handler.TwilioClient")
    @patch("agent_leasing.twilio_handler.get_twilio_credentials")
    async def test_start_recording_uses_correct_credentials(self, mock_get_creds, mock_twilio_client_class, handler):
        """Test that recording uses correct Twilio credentials from get_twilio_credentials."""
        # Arrange
        mock_get_creds.return_value = ("SK_api_key", "api_secret_123", "AC123456")

        mock_client_instance = Mock()
        mock_recordings = Mock()
        mock_twilio_client_class.return_value = mock_client_instance
        mock_client_instance.calls.return_value.recordings = mock_recordings

        payload = {
            "product_info": {
                "should_record": True,
            }
        }
        call_sid = "CA123456789"

        # Act
        await handler._start_recording(payload, call_sid)

        # Assert
        mock_twilio_client_class.assert_called_once_with(
            "SK_api_key",
            "api_secret_123",
            "AC123456",
        )
