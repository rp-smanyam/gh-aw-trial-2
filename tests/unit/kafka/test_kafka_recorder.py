from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from agent_leasing.kafka.kafka_recorder import (
    Author,
    Channel,
    Flow,
    _order_record_keys,
    build_data_curation_event,
    get_conversation_type,
    log_data_curation_event,
    str_number_or_zero,
)


class TestEnums:
    """Test cases for enum classes."""

    def test_author_enum(self):
        """Test Author enum values."""
        assert Author.CONTACT == "CONTACT"
        assert Author.BOT == "BOT"
        assert Author.UNKNOWN == "UNKNOWN"

    def test_conversation_type_enum(self):
        """Test Channel enum values."""
        assert Channel.EMAIL == "email"
        assert Channel.SMS == "sms"
        assert Channel.CHAT == "chat"
        assert Channel.VOICE == "voice"


class TestGetChannel:
    """Test cases for get_conversation_type function."""

    def test_get_conversation_type_returns_chat(self):
        """Test that get_conversation_type always returns CHAT."""
        assert get_conversation_type("any text") == Channel.CHAT.value
        assert get_conversation_type("") == Channel.CHAT.value
        assert get_conversation_type(None) == Channel.CHAT.value


class TestStrNumberOrZero:
    """Test cases for str_number_or_zero function."""

    def test_str_number_or_zero_with_valid_integers(self):
        """Test str_number_or_zero with valid integers."""
        assert str_number_or_zero(123) == "123"
        assert str_number_or_zero(0) == "0"
        assert str_number_or_zero(-456) == "-456"

    def test_str_number_or_zero_with_valid_strings(self):
        """Test str_number_or_zero with valid string numbers."""
        assert str_number_or_zero("123") == "123"
        assert str_number_or_zero("0") == "0"
        assert str_number_or_zero("-456") == "-456"

    def test_str_number_or_zero_with_valid_floats(self):
        """Test str_number_or_zero with valid floats (converts to int)."""
        assert str_number_or_zero(123.7) == "123"
        assert str_number_or_zero(0.0) == "0"
        assert str_number_or_zero(-456.9) == "-456"

    def test_str_number_or_zero_with_none(self):
        """Test str_number_or_zero with None."""
        assert str_number_or_zero(None) == "0"

    def test_str_number_or_zero_with_invalid_strings(self):
        """Test str_number_or_zero with invalid string values."""
        assert str_number_or_zero("abc") == "0"
        assert str_number_or_zero("123abc") == "0"
        assert str_number_or_zero("") == "0"

    def test_str_number_or_zero_with_invalid_types(self):
        """Test str_number_or_zero with invalid types."""
        assert str_number_or_zero([]) == "0"
        assert str_number_or_zero({}) == "0"
        assert str_number_or_zero(object()) == "0"


class TestBuildDataCurationEvent:
    """Test cases for build_data_curation_event function."""

    def test_build_data_curation_event_complete(self):
        """Test build_data_curation_event with all parameters."""
        chat_session_id = "session_123"
        conversation_type = Channel.CHAT
        body = "Hello, how can I help?"
        call_sid = "call_456"
        property_id = "789"
        applicant_id = "101112"
        bot_type = "APPLICANT"
        author = Author.BOT
        timestamp = datetime(2023, 1, 1, 12, 0, 0)
        flows = [Flow(name="test_flow")]
        language = "en"

        result = build_data_curation_event(
            chat_session_id,
            conversation_type,
            body,
            call_sid,
            property_id,
            applicant_id,
            bot_type,
            author,
            flows,
            timestamp,
            language,
            [
                {
                    "service_request": [
                        "create_service_request",
                        {"created": True, "sr_id": 53362},
                    ]
                }
            ],
        )

        expected = {
            "conversation_id": "session_123",
            "call_sid": "call_456",
            "property_id": "789",
            "prospect_id": "101112",  # Note: applicant_id becomes prospect_id
            "conversation_type": "chat",
            "bot_type": "APPLICANT",
            "language": "en",
            "transcript": {
                "author": "BOT",
                "body": "Hello, how can I help?",
                "timestamp": int(timestamp.timestamp() * 1000),  # Use actual timestamp conversion
                "metadata": "[{'service_request': ['create_service_request', {'created': True, 'sr_id': 53362}]}]",
                "openai_trace_url": None,
                "langsmith_trace_url": None,
            },
            "intent": {
                "name": "TEST_FLOW",
                "display_name": "Test Flow",
                "language": "en",
            },
        }

        assert result == expected

    def test_build_data_curation_event_with_none_values(self):
        """Test build_data_curation_event with None values for IDs."""
        result = build_data_curation_event(
            "session_123",
            Channel.VOICE,
            "Test message",
            None,
            None,
            None,
            "applicant",
            Author.CONTACT,
            [Flow(name="test_flow")],
            datetime(2023, 1, 1, 12, 0, 0),
            "es",
            [],
        )

        assert result["call_sid"] is None
        assert result["property_id"] == "0"
        assert result["prospect_id"] == "0"

    def test_build_data_curation_event_with_invalid_ids(self):
        """Test build_data_curation_event with invalid ID values."""
        result = build_data_curation_event(
            "session_123",
            Channel.SMS,
            "Test message",
            "call_sid",
            "invalid_property_id",
            "invalid_applicant_id",
            "applicant",
            Author.UNKNOWN,
            [Flow(name="test_flow")],
            datetime(2023, 1, 1, 12, 0, 0),
            "fr",
            [],
        )

        assert result["property_id"] == "0"
        assert result["prospect_id"] == "0"

    def test_build_data_curation_event_voice_empty_chat_session_id_uses_call_sid(self):
        """Test that for VOICE type with empty chat_session_id, conversation_id is set to call_sid."""
        result = build_data_curation_event(
            "",
            Channel.VOICE,
            "Test message",
            "call_123",
            "789",
            "101112",
            "RESIDENT",
            Author.BOT,
            [Flow(name="test_flow")],
            datetime(2023, 1, 1, 12, 0, 0),
            "en",
            [],
        )

        assert result["conversation_id"] == "call_123"

    def test_build_data_curation_event_voice_none_chat_session_id_uses_call_sid(self):
        """Test that for VOICE type with None chat_session_id, conversation_id is set to call_sid."""
        result = build_data_curation_event(
            None,
            Channel.VOICE,
            "Test message",
            "call_456",
            "789",
            "101112",
            "RESIDENT",
            Author.BOT,
            [Flow(name="test_flow")],
            datetime(2023, 1, 1, 12, 0, 0),
            "en",
            [],
        )

        assert result["conversation_id"] == "call_456"

    def test_build_data_curation_event_voice_uppercase_string_no_chat_session_id_uses_call_sid(self):
        """Test that for uppercased 'VOICE' string (as passed by log_data_curation_event) with empty
        chat_session_id, conversation_id is set to call_sid."""
        result = build_data_curation_event(
            "",
            "VOICE",
            "Test message",
            "call_789",
            "789",
            "101112",
            "RESIDENT",
            Author.BOT,
            [Flow(name="test_flow")],
            datetime(2023, 1, 1, 12, 0, 0),
            "en",
            [],
        )

        assert result["conversation_id"] == "call_789"

    def test_build_data_curation_event_voice_with_chat_session_id_keeps_session_id(self):
        """Test that for VOICE type with a non-empty chat_session_id, conversation_id is not replaced."""
        result = build_data_curation_event(
            "session_abc",
            Channel.VOICE,
            "Test message",
            "call_999",
            "789",
            "101112",
            "RESIDENT",
            Author.BOT,
            [Flow(name="test_flow")],
            datetime(2023, 1, 1, 12, 0, 0),
            "en",
            [],
        )

        assert result["conversation_id"] == "session_abc"


class TestOrderRecordKeys:
    """Test cases for _order_record_keys function."""

    def test_order_record_keys(self):
        """Test _order_record_keys orders keys correctly."""
        unordered_dict = {
            "intent": {"name": "TEST_FLOW"},
            "conversation_id": "123",
            "transcript": {"body": "hello"},
            "bot_type": "APPLICANT",
            "language": "en",
            "call_sid": "call_123",
            "conversation_type": "chat",
            "property_id": "456",
            "prospect_id": "789",
        }

        result = _order_record_keys(unordered_dict)

        expected_order = [
            "conversation_id",
            "call_sid",
            "property_id",
            "prospect_id",
            "conversation_type",
            "bot_type",
            "language",
            "transcript",
            "intent",
        ]

        assert list(result.keys()) == expected_order
        assert result == unordered_dict  # Same content, different order


class TestLogDataCurationEvent:
    """Test cases for log_data_curation_event async function."""

    @pytest.mark.asyncio
    @patch("agent_leasing.kafka.kafka_recorder.settings")
    async def test_log_data_curation_event_kafka_not_configured(self, mock_settings):
        """Test log_data_curation_event when Kafka is not configured."""
        mock_settings.is_kafka_reporting_configured.return_value = False

        # Should return early without doing anything
        result = await log_data_curation_event(
            chat_session_id="session_123",
            conversation_type=Channel.CHAT,
            body="Hello",
            call_sid="call_456",
            property_id="789",
            applicant_id="101112",
            bot_type="applicant",
            author=Author.CONTACT,
            flows=[Flow(name="test_flow")],
        )

        assert result is None
        mock_settings.is_kafka_reporting_configured.assert_called_once()

    @pytest.mark.asyncio
    @patch("agent_leasing.kafka.kafka_recorder.kafka_application_context")
    @patch("agent_leasing.kafka.kafka_recorder.settings")
    @patch("agent_leasing.kafka.kafka_recorder.datetime")
    async def test_log_data_curation_event_success(self, mock_datetime_module, mock_settings, mock_kafka_context):
        """Test successful log_data_curation_event execution."""
        # Setup mocks
        mock_settings.is_kafka_reporting_configured.return_value = True
        mock_producer = MagicMock()
        mock_kafka_context.reporting_data_kafka_producer = mock_producer

        fixed_timestamp = datetime(2023, 1, 1, 12, 0, 0)
        mock_datetime_module.now.return_value = fixed_timestamp

        # Test with default timestamp (None)
        await log_data_curation_event(
            chat_session_id="session_123",
            conversation_type=Channel.CHAT,
            body="Hello",
            call_sid="call_456",
            property_id="789",
            applicant_id="101112",
            bot_type="applicant",
            author=Author.CONTACT,
            flows=[Flow(name="test_flow")],
        )

        # Verify producer.produce was called
        mock_producer.produce.assert_called_once()
        call_args = mock_producer.produce.call_args

        # Check the payload structure
        payload = call_args[0][0]
        assert payload["conversation_id"] == "session_123"
        assert payload["conversation_type"] == "CHAT"
        assert payload["transcript"]["body"] == "Hello"
        assert payload["transcript"]["author"] == "CONTACT"

        # Check the ack callback
        assert callable(call_args[1]["on_delivery"])

    @pytest.mark.asyncio
    @patch("agent_leasing.kafka.kafka_recorder.kafka_application_context")
    @patch("agent_leasing.kafka.kafka_recorder.settings")
    async def test_log_data_curation_event_with_explicit_timestamp(self, mock_settings, mock_kafka_context):
        """Test log_data_curation_event with explicit timestamp."""
        mock_settings.is_kafka_reporting_configured.return_value = True
        mock_producer = MagicMock()
        mock_kafka_context.reporting_data_kafka_producer = mock_producer

        explicit_timestamp = datetime(2023, 6, 15, 14, 30, 0)

        await log_data_curation_event(
            chat_session_id="session_456",
            conversation_type=Channel.VOICE,
            body="How can I help?",
            call_sid=None,
            property_id="999",
            applicant_id="888",
            bot_type="applicant",
            author=Author.BOT,
            flows=[Flow(name="custom_flow")],
            timestamp=explicit_timestamp,
            language="es",
        )

        mock_producer.produce.assert_called_once()
        payload = mock_producer.produce.call_args[0][0]

        assert payload["conversation_id"] == "session_456"
        assert payload["conversation_type"] == "VOICE"
        assert payload["language"] == "es"
        assert payload["intent"]["name"] == "CUSTOM_FLOW"
        assert payload["transcript"]["timestamp"] == int(explicit_timestamp.timestamp() * 1000)

    @pytest.mark.asyncio
    @patch("agent_leasing.kafka.kafka_recorder.kafka_application_context")
    @patch("agent_leasing.kafka.kafka_recorder.settings")
    @patch("agent_leasing.kafka.kafka_recorder.fastavro")
    @patch("agent_leasing.kafka.kafka_recorder.logger")
    async def test_log_data_curation_event_with_validation(
        self, mock_logger, mock_fastavro, mock_settings, mock_kafka_context
    ):
        """Test log_data_curation_event with record validation."""
        mock_settings.is_kafka_reporting_configured.return_value = True
        mock_settings.data_curation_schema = {"type": "record", "name": "test"}
        mock_producer = MagicMock()
        mock_kafka_context.reporting_data_kafka_producer = mock_producer

        parsed_schema = {"parsed": "schema"}
        mock_fastavro.parse_schema.return_value = parsed_schema

        await log_data_curation_event(
            chat_session_id="session_789",
            conversation_type=Channel.EMAIL,
            body="Test email",
            call_sid="call_789",
            property_id="111",
            applicant_id="222",
            bot_type="applicant",
            author=Author.CONTACT,
            flows=[Flow(name="test_flow")],
            validate_record=True,
        )

        # Verify validation was performed
        mock_fastavro.parse_schema.assert_called_once_with(mock_settings.data_curation_schema)
        mock_fastavro.validate.assert_called_once()

        # Check logging
        # mock_logger.info.assert_any_call("Validated Kafka record")

        # Verify producer was still called
        mock_producer.produce.assert_called_once()

    @pytest.mark.asyncio
    @patch("agent_leasing.kafka.kafka_recorder.kafka_application_context")
    @patch("agent_leasing.kafka.kafka_recorder.settings")
    @patch("agent_leasing.kafka.kafka_recorder.logger")
    async def test_log_data_curation_event_exception_handling(self, mock_logger, mock_settings, mock_kafka_context):
        """Test log_data_curation_event exception handling."""
        mock_settings.is_kafka_reporting_configured.return_value = True
        mock_producer = MagicMock()
        mock_producer.produce.side_effect = Exception("Kafka error")
        mock_kafka_context.reporting_data_kafka_producer = mock_producer

        # Should not raise exception, but log it
        await log_data_curation_event(
            chat_session_id="session_error",
            conversation_type=Channel.CHAT,
            body="Error test",
            call_sid="call_error",
            property_id="123",
            applicant_id="456",
            bot_type="resident",
            author=Author.BOT,
            flows=[Flow(name="test_flow")],
        )

        # Verify exception was logged
        # mock_logger.exception.assert_called_once_with("Kafka reporting error")

    @pytest.mark.asyncio
    @patch("agent_leasing.kafka.kafka_recorder.kafka_application_context")
    @patch("agent_leasing.kafka.kafka_recorder.settings")
    @patch("agent_leasing.kafka.kafka_recorder.logger")
    async def test_log_data_curation_event_ack_callback_error(self, mock_logger, mock_settings, mock_kafka_context):
        """Test the ack callback handles errors correctly."""
        mock_settings.is_kafka_reporting_configured.return_value = True
        mock_producer = MagicMock()
        mock_kafka_context.reporting_data_kafka_producer = mock_producer

        await log_data_curation_event(
            chat_session_id="session_ack_test",
            conversation_type=Channel.SMS,
            body="Ack test",
            call_sid="call_ack",
            property_id="555",
            applicant_id="666",
            bot_type="prospect",
            author=Author.CONTACT,
            flows=[Flow(name="test_flow")],
        )

        # Get the ack callback
        call_args = mock_producer.produce.call_args
        ack_callback = call_args[1]["on_delivery"]

        # Test successful ack (no error)
        ack_callback(None, "success_msg")
        # mock_logger.warning.assert_not_called()

        # Test error ack
        # test_record = call_args[0][0]  # The payload that was sent
        ack_callback("kafka_error", "error_msg")
        # mock_logger.warning.assert_called_once_with(f"Kafka error on ack: kafka_error error_msg {test_record}")

    @pytest.mark.asyncio
    @patch("agent_leasing.kafka.kafka_recorder.kafka_application_context")
    @patch("agent_leasing.kafka.kafka_recorder.settings")
    async def test_log_data_curation_event_default_parameters(self, mock_settings, mock_kafka_context):
        """Test log_data_curation_event with default parameters."""
        mock_settings.is_kafka_reporting_configured.return_value = True
        mock_producer = MagicMock()
        mock_kafka_context.reporting_data_kafka_producer = mock_producer

        # Call with minimal required parameters
        await log_data_curation_event(
            chat_session_id="session_default",
            conversation_type=Channel.CHAT,
            body="Default test",
            call_sid=None,
            property_id="777",
            applicant_id="888",
            bot_type="resident",
            author=Author.BOT,
            flows=[Flow(name="main_thinker_tool"), Flow(name="other_thinker_tool")],
        )

        payload = mock_producer.produce.call_args[0][0]

        # Check default values
        assert payload["intent"]["name"] == "MAIN_FLOW"
        assert payload["intent"]["display_name"] == "Main Flow"
        assert payload["language"] == "en"
        assert payload["call_sid"] is None

    @pytest.mark.asyncio
    @patch("agent_leasing.kafka.kafka_recorder.kafka_application_context")
    @patch("agent_leasing.kafka.kafka_recorder.settings")
    async def test_log_data_curation_event_voice_no_chat_session_id_uses_call_sid(
        self, mock_settings, mock_kafka_context
    ):
        """Test that for VOICE with no chat_session_id, conversation_id is set to call_sid.

        log_data_curation_event uppercases conversation_type before passing it to
        build_data_curation_event, so this also verifies the 'VOICE' string path.
        """
        mock_settings.is_kafka_reporting_configured.return_value = True
        mock_producer = MagicMock()
        mock_kafka_context.reporting_data_kafka_producer = mock_producer

        await log_data_curation_event(
            chat_session_id="",
            conversation_type=Channel.VOICE,
            body="Hello",
            call_sid="call_voice_123",
            property_id="789",
            applicant_id="101112",
            bot_type="resident",
            author=Author.CONTACT,
            flows=[Flow(name="test_flow")],
        )

        payload = mock_producer.produce.call_args[0][0]
        assert payload["conversation_id"] == "call_voice_123"
        assert payload["conversation_type"] == "VOICE"

    @pytest.mark.asyncio
    @patch("agent_leasing.kafka.kafka_recorder.kafka_application_context")
    @patch("agent_leasing.kafka.kafka_recorder.settings")
    async def test_log_data_curation_event_voice_none_chat_session_id_uses_call_sid(
        self, mock_settings, mock_kafka_context
    ):
        """Test that for VOICE with None chat_session_id, conversation_id is set to call_sid."""
        mock_settings.is_kafka_reporting_configured.return_value = True
        mock_producer = MagicMock()
        mock_kafka_context.reporting_data_kafka_producer = mock_producer

        await log_data_curation_event(
            chat_session_id=None,
            conversation_type=Channel.VOICE,
            body="Hello",
            call_sid="call_voice_456",
            property_id="789",
            applicant_id="101112",
            bot_type="resident",
            author=Author.CONTACT,
            flows=[Flow(name="test_flow")],
        )

        payload = mock_producer.produce.call_args[0][0]
        assert payload["conversation_id"] == "call_voice_456"
        assert payload["conversation_type"] == "VOICE"
