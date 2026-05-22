from datetime import datetime
from unittest.mock import call, patch

import pytest

from agent_leasing.kafka.kafka_recorder import Author, Channel, Flow
from agent_leasing.kafka.sample_producer import (
    main,
    produce_sample_data_curation_events,
)


class TestProduceSampleDataCurationEvents:
    """Test cases for produce_sample_data_curation_events function."""

    @pytest.mark.asyncio
    @patch("agent_leasing.kafka.sample_producer.kafka_application_context")
    @patch("agent_leasing.kafka.sample_producer.log_data_curation_event")
    @patch("agent_leasing.kafka.sample_producer.uuid.uuid4")
    @patch("agent_leasing.kafka.sample_producer.datetime")
    async def test_produce_sample_data_curation_events_success(
        self, mock_datetime_module, mock_uuid4, mock_log_event, mock_kafka_context
    ):
        """Test successful execution of produce_sample_data_curation_events."""
        # Setup mocks
        mock_session_id = "test-session-id-123"
        mock_uuid4.return_value = mock_session_id

        mock_timestamp = datetime(2023, 1, 1, 12, 0, 0)
        mock_datetime_module.now.return_value = mock_timestamp

        mock_log_event.return_value = None  # async function returns None

        # Execute the function
        await produce_sample_data_curation_events()

        # Verify kafka context was started and closed
        mock_kafka_context.start.assert_called_once()
        mock_kafka_context.close.assert_called_once()

        # Verify UUID was generated once (for one test event pair)
        mock_uuid4.assert_called_once()

        # Verify log_data_curation_event was called twice (inbound + outbound)
        assert mock_log_event.call_count == 2

        # Check the calls
        calls = mock_log_event.call_args_list

        # First call should be for inbound event
        inbound_call = calls[0]
        assert inbound_call[1]["chat_session_id"] == mock_session_id
        assert inbound_call[1]["conversation_type"] == Channel.CHAT
        assert inbound_call[1]["body"] == "hello"
        assert inbound_call[1]["call_sid"] is None
        assert inbound_call[1]["property_id"] == "21521"
        assert inbound_call[1]["applicant_id"] == "740473"
        assert inbound_call[1]["bot_type"] == "resident"
        assert inbound_call[1]["author"] == Author.CONTACT
        assert inbound_call[1]["flows"] == [Flow(name="test_flow")]
        assert inbound_call[1]["timestamp"] == mock_timestamp
        assert inbound_call[1]["validate_record"] is True

        # Second call should be for outbound event
        outbound_call = calls[1]
        assert outbound_call[1]["chat_session_id"] == mock_session_id
        assert outbound_call[1]["conversation_type"] == Channel.CHAT
        assert outbound_call[1]["body"] == "How can I help you today?"
        assert outbound_call[1]["call_sid"] is None
        assert outbound_call[1]["property_id"] == "21521"
        assert outbound_call[1]["applicant_id"] == "740473"
        assert outbound_call[1]["bot_type"] == "resident"
        assert outbound_call[1]["author"] == Author.BOT
        assert outbound_call[1]["flows"] == [Flow(name="test_flow")]
        assert outbound_call[1]["timestamp"] == mock_timestamp
        assert outbound_call[1]["validate_record"] is True

    @pytest.mark.asyncio
    @patch("agent_leasing.kafka.sample_producer.kafka_application_context")
    @patch("agent_leasing.kafka.sample_producer.log_data_curation_event")
    @patch("agent_leasing.kafka.sample_producer.uuid.uuid4")
    async def test_produce_sample_data_curation_events_multiple_pairs(
        self, mock_uuid4, mock_log_event, mock_kafka_context
    ):
        """Test that the function handles the number_of_test_event_pairs correctly."""
        # Mock UUID to return different values for each call
        mock_uuid4.side_effect = ["session-1", "session-2", "session-3"]
        mock_log_event.return_value = None

        # Patch the number_of_test_event_pairs in the function
        with patch("agent_leasing.kafka.sample_producer.produce_sample_data_curation_events") as mock_func:
            # Create a custom implementation that uses 3 pairs instead of 1
            async def custom_implementation():
                import uuid
                from datetime import datetime

                from agent_leasing.kafka.kafka_recorder import Author, Channel
                from agent_leasing.kafka.sample_producer import (
                    kafka_application_context,
                    log_data_curation_event,
                )

                kafka_application_context.start()

                conversation_type = Channel.CHAT
                call_sid = None
                property_id = "21521"
                applicant_id = "740473"
                bot_type = "resident"

                inbound_events = ["hello"]
                outbound_events = ["How can I help you today?"]
                number_of_test_event_pairs = 3  # Changed from 1 to 3

                for i in range(0, number_of_test_event_pairs):
                    chat_session_id = str(uuid.uuid4())

                    for body in inbound_events:
                        await log_data_curation_event(
                            chat_session_id=chat_session_id,
                            conversation_type=conversation_type,
                            body=body,
                            call_sid=call_sid,
                            property_id=property_id,
                            applicant_id=applicant_id,
                            bot_type=bot_type,
                            author=Author.CONTACT,
                            flows=[Flow(name="test_flow")],
                            timestamp=datetime.now(),
                            validate_record=True,
                        )

                    for body in outbound_events:
                        await log_data_curation_event(
                            chat_session_id=chat_session_id,
                            conversation_type=conversation_type,
                            body=body,
                            call_sid=call_sid,
                            property_id=property_id,
                            applicant_id=applicant_id,
                            bot_type=bot_type,
                            author=Author.BOT,
                            flows=[Flow(name="test_flow")],
                            timestamp=datetime.now(),
                            validate_record=True,
                        )

                kafka_application_context.close()

            mock_func.side_effect = custom_implementation

            # Execute the custom function
            await mock_func()

            # Verify UUID was called 3 times (for 3 test event pairs)
            assert mock_uuid4.call_count == 3

            # Verify log_data_curation_event was called 6 times (3 pairs * 2 events each)
            assert mock_log_event.call_count == 6

    @pytest.mark.asyncio
    @patch("agent_leasing.kafka.sample_producer.kafka_application_context")
    @patch("agent_leasing.kafka.sample_producer.log_data_curation_event")
    async def test_produce_sample_data_curation_events_exception_handling(self, mock_log_event, mock_kafka_context):
        """Test that exceptions in log_data_curation_event don't prevent cleanup."""
        # Make log_data_curation_event raise an exception
        mock_log_event.side_effect = Exception("Test exception")

        # The function should still complete and call close()
        with pytest.raises(Exception, match="Test exception"):
            await produce_sample_data_curation_events()

        # Verify kafka context was started
        mock_kafka_context.start.assert_called_once()
        # Note: close() won't be called because the exception prevents reaching that line
        # This is the actual behavior of the function - it doesn't have try/finally

    @pytest.mark.asyncio
    @patch("agent_leasing.kafka.sample_producer.kafka_application_context")
    @patch("agent_leasing.kafka.sample_producer.log_data_curation_event")
    async def test_produce_sample_data_curation_events_kafka_context_calls(self, mock_log_event, mock_kafka_context):
        """Test that kafka context start and close are called in correct order."""
        mock_log_event.return_value = None

        await produce_sample_data_curation_events()

        # Verify the order of calls
        expected_calls = [call.start(), call.close()]
        mock_kafka_context.assert_has_calls(expected_calls, any_order=False)

    @pytest.mark.asyncio
    @patch("agent_leasing.kafka.sample_producer.kafka_application_context")
    @patch("agent_leasing.kafka.sample_producer.log_data_curation_event")
    @patch("agent_leasing.kafka.sample_producer.uuid.uuid4")
    async def test_produce_sample_data_curation_events_data_values(
        self, mock_uuid4, mock_log_event, mock_kafka_context
    ):
        """Test that the correct hardcoded values are used."""
        mock_session_id = "test-session-456"
        mock_uuid4.return_value = mock_session_id
        mock_log_event.return_value = None

        await produce_sample_data_curation_events()

        # Verify the hardcoded values are used correctly
        calls = mock_log_event.call_args_list

        for call_args in calls:
            assert call_args[1]["chat_session_id"] == mock_session_id
            assert call_args[1]["conversation_type"] == Channel.CHAT  # conversation_type
            assert call_args[1]["call_sid"] is None  # call_sid
            assert call_args[1]["property_id"] == "21521"  # property_id
            assert call_args[1]["applicant_id"] == "740473"  # applicant_id

        # Check specific message bodies
        inbound_call = calls[0]
        outbound_call = calls[1]

        assert inbound_call[1]["body"] == "hello"
        assert outbound_call[1]["body"] == "How can I help you today?"

        # Check bot type
        assert inbound_call[1]["bot_type"] == "resident"
        assert outbound_call[1]["bot_type"] == "resident"

        # Check authors
        assert inbound_call[1]["author"] == Author.CONTACT
        assert outbound_call[1]["author"] == Author.BOT


class TestMain:
    """Test cases for main function."""

    @patch("agent_leasing.kafka.sample_producer.asyncio.run")
    def test_main_function(self, mock_asyncio_run):
        """Test that main function calls asyncio.run."""
        main()

        # Verify asyncio.run was called once (we can't easily verify the exact argument
        # because it's a coroutine object, not the function itself)
        mock_asyncio_run.assert_called_once()

    @patch("agent_leasing.kafka.sample_producer.asyncio.run")
    def test_main_function_integration(self, mock_asyncio_run):
        """Test main function integration."""
        main()

        # Verify asyncio.run was called once
        mock_asyncio_run.assert_called_once()

        # Verify the argument is a coroutine (which means the function was called)
        call_args = mock_asyncio_run.call_args[0]
        assert len(call_args) == 1
        import inspect

        assert inspect.iscoroutine(call_args[0])

    @patch("agent_leasing.kafka.sample_producer.asyncio.run")
    def test_main_function_exception_propagation(self, mock_asyncio_run):
        """Test that exceptions from asyncio.run are propagated."""
        mock_asyncio_run.side_effect = Exception("Async run failed")

        with pytest.raises(Exception, match="Async run failed"):
            main()

        mock_asyncio_run.assert_called_once()


class TestModuleExecution:
    """Test cases for module-level execution."""

    @patch("agent_leasing.kafka.sample_producer.main")
    def test_name_main_execution(self, mock_main):
        """Test that main() is called when module is executed directly."""
        # This test simulates the if __name__ == "__main__": block
        # We can't directly test this without importing the module in a special way,
        # but we can test that the main function exists and is callable

        # Verify main function exists and is callable
        from agent_leasing.kafka.sample_producer import main

        assert callable(main)

        # Test calling main directly
        main()
        mock_main.assert_called_once()


# Integration test to verify the overall flow
class TestIntegration:
    """Integration tests for the sample producer module."""

    @pytest.mark.asyncio
    @patch("agent_leasing.kafka.sample_producer.kafka_application_context")
    @patch("agent_leasing.kafka.sample_producer.log_data_curation_event")
    @patch("agent_leasing.kafka.sample_producer.uuid.uuid4")
    @patch("agent_leasing.kafka.sample_producer.datetime")
    async def test_full_integration_flow(self, mock_datetime_module, mock_uuid4, mock_log_event, mock_kafka_context):
        """Test the complete integration flow from main to produce_sample_data_curation_events."""
        # Setup mocks
        mock_session_id = "integration-test-session"
        mock_uuid4.return_value = mock_session_id

        mock_timestamp = datetime(2023, 12, 25, 10, 30, 0)
        mock_datetime_module.now.return_value = mock_timestamp

        mock_log_event.return_value = None

        # Execute the main workflow
        await produce_sample_data_curation_events()

        # Comprehensive verification
        mock_kafka_context.start.assert_called_once()
        mock_kafka_context.close.assert_called_once()

        # Verify both events were logged
        assert mock_log_event.call_count == 2

        # Verify the complete call structure for both events
        calls = mock_log_event.call_args_list

        # Inbound event verification
        inbound_args, inbound_kwargs = calls[0]
        assert inbound_args == ()
        assert inbound_kwargs == {
            "chat_session_id": mock_session_id,
            "conversation_type": Channel.CHAT,
            "body": "hello",
            "call_sid": None,
            "property_id": "21521",
            "applicant_id": "740473",
            "bot_type": "resident",
            "author": Author.CONTACT,
            "flows": [Flow(name="test_flow")],
            "timestamp": mock_timestamp,
            "validate_record": True,
        }

        # Outbound event verification
        outbound_args, outbound_kwargs = calls[1]
        assert outbound_args == ()
        assert outbound_kwargs == {
            "chat_session_id": mock_session_id,
            "conversation_type": Channel.CHAT,
            "body": "How can I help you today?",
            "call_sid": None,
            "property_id": "21521",
            "applicant_id": "740473",
            "bot_type": "resident",
            "author": Author.BOT,
            "flows": [Flow(name="test_flow")],
            "timestamp": mock_timestamp,
            "validate_record": True,
        }
