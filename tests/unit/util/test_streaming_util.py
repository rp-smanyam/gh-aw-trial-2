import json
import time
from unittest.mock import MagicMock

import pytest

from agent_leasing.agent.util import ResidentResponderOutput
from agent_leasing.util.streaming_util import (
    DONE,
    StreamEventProcessor,
    elapsed_ms,
    end,
    error,
    generating,
    process_streaming_json_chunk,
    start,
)


class TestStreamingUtility:
    def test_start(self):
        assert (
            start(1726)
            == f"data: {
                json.dumps(
                    {
                        'content': '',
                        'phase': 'thinking',
                        'elapsed': 1726,
                    }
                )
            }\n\n"
        )

    def test_end(self):
        assert (
            end(1726)
            == f"data: {
                json.dumps(
                    {
                        'content': '',
                        'status': 'done',
                        'done': True,
                        'elapsed': 1726,
                    }
                )
            }\n\n"
        )

    def test_done(self):
        assert DONE == "data: [DONE]\n\n"

    def test_error(self):
        assert (
            error("There was an error")
            == f"data: {
                json.dumps(
                    {
                        'content': 'There was an error',
                        'status': 'error',
                        'done': True,
                    }
                )
            }\n\n"
        )

    def test_generating(self):
        assert (
            generating(content="hello", elapsed=1726)
            == f"data: {
                json.dumps(
                    {
                        'content': 'hello',  # chunk
                        'status': 'active',
                        'phase': 'generating',
                        'elapsed': 1726,
                    }
                )
            }\n\n"
        )

    def test_elapsed_ms(self):
        """Test elapsed_ms calculates milliseconds correctly."""
        # Record start time
        start_time = time.time()

        # Wait a small amount
        time.sleep(0.1)  # 100ms

        # Calculate elapsed
        result = elapsed_ms(start_time)

        # Should be roughly 100ms (with some tolerance for execution time)
        assert 90 <= result <= 150, f"Expected ~100ms, got {result}ms"
        assert isinstance(result, int), "Result should be an integer"

    def test_elapsed_ms_zero(self):
        """Test elapsed_ms with immediate calculation."""
        start_time = time.time()
        result = elapsed_ms(start_time)

        # Should be very close to 0 (but might be a few ms due to execution)
        assert 0 <= result <= 10, f"Expected near 0ms, got {result}ms"
        assert isinstance(result, int), "Result should be an integer"

    def test_elapsed_ms_precision(self):
        """Test elapsed_ms returns integer milliseconds."""
        # Use a known past time
        start_time = time.time() - 1.5  # 1.5 seconds ago
        result = elapsed_ms(start_time)

        # Should be approximately 1500ms
        assert 1450 <= result <= 1550, f"Expected ~1500ms, got {result}ms"
        assert isinstance(result, int), "Result should be an integer"


class TestProcessStreamingChunk:
    @pytest.mark.parametrize(
        "chunks,expected",
        [
            (
                ['{"response":"', "Hello", " you", '."', ',"reasoning'],
                "Hello you.",
            ),
            (
                ['{"response":"', "Hello", " you", '.\n"', ',"reasoning'],
                "Hello you.\n",
            ),
            (["{", '"response"', ':"', "Hello", " you", ".", '","'], "Hello you."),
            (["{", '"response"', ':"', "Hello \n you.", '","'], "Hello \n you."),
            (
                ['{"response":"Hello you.","reasoning":"whatever"}'],
                "Hello you.",
            ),
            (
                ['{"response": "Hello you.", "reasoning": "whatever"}'],
                "Hello you.",
            ),
        ],
    )
    def test_streaming_sequence(self, chunks, expected):
        """Simulate a realistic streaming sequence as it happens in server.py."""
        # Simulates lines 736-745 in server.py
        streamed_events = []
        chunks_to_yield = []

        for chunk in chunks:
            streamed_events.append(chunk)
            response_so_far = "".join(streamed_events)
            result1 = process_streaming_json_chunk("response", chunk, response_so_far)
            if result1 is not None:
                chunks_to_yield.append(result1)

        response = "".join(chunks_to_yield)
        assert response == expected


class TestStreamEventProcessor:
    """Test suite for StreamEventProcessor class."""

    @pytest.fixture
    def processor(self):
        """Create a StreamEventProcessor instance."""
        return StreamEventProcessor(json_attribute="response")

    @pytest.fixture
    def mock_text_delta_event(self):
        """Create a mock text delta event."""

        def create_event(delta_text: str):
            event = MagicMock()
            event.type = "raw_response_event"
            event.data = MagicMock()
            event.data.__class__.__name__ = "ResponseTextDeltaEvent"
            event.data.delta = delta_text
            return event

        return create_event

    @pytest.fixture
    def mock_message_output_event(self):
        """Create a mock message output event."""

        def create_event(message_json: dict):
            event = MagicMock()
            event.type = "run_item_stream_event"
            event.item = MagicMock()
            event.item.type = "message_output_item"
            # Mock ItemHelpers.text_message_output to return the JSON string
            with pytest.MonkeyPatch.context() as m:
                m.setattr(
                    "agent_leasing.util.streaming_util.ItemHelpers.text_message_output",
                    lambda x: json.dumps(message_json),
                )
            return event, json.dumps(message_json)

        return create_event

    @pytest.mark.asyncio
    async def test_process_text_delta_events(self, processor, mock_text_delta_event):
        """Test processing of text delta events."""
        # Create mock result with streaming events
        mock_result = MagicMock()

        async def mock_stream():
            # Simulate streaming JSON response
            chunks = ['{"response":"', "Hello", " world", '"}']
            for chunk in chunks:
                yield mock_text_delta_event(chunk)

        mock_result.stream_events = mock_stream

        # Process events
        chunks_received = []
        async for chunk in processor.process_events(mock_result):
            if chunk is not None:
                chunks_received.append(chunk)

        # Verify chunks were processed correctly
        assert "".join(chunks_received) == "Hello world"

    @pytest.mark.asyncio
    async def test_process_message_output_event(self, processor):
        """Test processing of message output event."""
        # Create mock result with message output
        mock_result = MagicMock()

        output_data = {"response": "Hello from agent", "language_code": "en", "flows": []}

        async def mock_stream():
            event = MagicMock()
            event.type = "run_item_stream_event"
            event.item = MagicMock()
            event.item.type = "message_output_item"
            # Need to mock ItemHelpers
            yield event

        mock_result.stream_events = mock_stream

        # Mock ItemHelpers.text_message_output
        import agent_leasing.util.streaming_util as streaming_util

        original_text_output = streaming_util.ItemHelpers.text_message_output
        streaming_util.ItemHelpers.text_message_output = lambda x: json.dumps(output_data)

        try:
            # Process events
            async for _ in processor.process_events(mock_result):
                pass

            # Verify final output was captured
            assert processor.final_output is not None
            assert isinstance(processor.final_output, ResidentResponderOutput)
            assert processor.final_output.response == "Hello from agent"
            assert processor.final_output_response == "Hello from agent"
        finally:
            streaming_util.ItemHelpers.text_message_output = original_text_output

    @pytest.mark.asyncio
    async def test_mixed_event_types(self, processor, mock_text_delta_event):
        """Test processing of mixed event types."""
        mock_result = MagicMock()

        output_data = {"response": "Final response", "language_code": "en", "flows": []}

        async def mock_stream():
            # Yield text delta events
            for chunk in ['{"response":"', "Streaming", " text", '"']:
                yield mock_text_delta_event(chunk)

            # Yield message output event
            event = MagicMock()
            event.type = "run_item_stream_event"
            event.item = MagicMock()
            event.item.type = "message_output_item"
            yield event

        mock_result.stream_events = mock_stream

        # Mock ItemHelpers
        import agent_leasing.util.streaming_util as streaming_util

        original_text_output = streaming_util.ItemHelpers.text_message_output
        streaming_util.ItemHelpers.text_message_output = lambda x: json.dumps(output_data)

        try:
            chunks_received = []
            async for chunk in processor.process_events(mock_result):
                if chunk is not None:
                    chunks_received.append(chunk)

            # Verify both streaming and final output
            assert "".join(chunks_received) == "Streaming text"
            assert processor.final_output is not None
            assert processor.final_output_response == "Final response"
        finally:
            streaming_util.ItemHelpers.text_message_output = original_text_output

    @pytest.mark.asyncio
    async def test_empty_stream(self, processor):
        """Test handling of empty stream."""
        mock_result = MagicMock()

        async def mock_stream():
            return
            yield  # Make it a generator

        mock_result.stream_events = mock_stream

        chunks = []
        async for chunk in processor.process_events(mock_result):
            chunks.append(chunk)

        assert len(chunks) == 0
        assert processor.final_output is None

    @pytest.mark.asyncio
    async def test_invalid_json_in_message_output(self, processor):
        """Test handling of invalid JSON in message output."""
        mock_result = MagicMock()

        async def mock_stream():
            event = MagicMock()
            event.type = "run_item_stream_event"
            event.item = MagicMock()
            event.item.type = "message_output_item"
            yield event

        mock_result.stream_events = mock_stream

        # Mock ItemHelpers to return invalid JSON
        import agent_leasing.util.streaming_util as streaming_util

        original_text_output = streaming_util.ItemHelpers.text_message_output
        streaming_util.ItemHelpers.text_message_output = lambda x: "invalid json{"

        try:
            async for _ in processor.process_events(mock_result):
                pass

            # Should handle error gracefully
            assert processor.final_output is None
        finally:
            streaming_util.ItemHelpers.text_message_output = original_text_output

    @pytest.mark.asyncio
    async def test_pydantic_validation_error_in_message_output(self, processor):
        """ResidentResponderOutput parses with strict-Literal qna_topics — if the
        model emits an unknown topic, Pydantic raises ValidationError. Without
        an explicit catch, the exception escapes the generator after deltas were
        already streamed. Match the JSONDecodeError fallback shape: leave
        final_output None and continue.
        """
        mock_result = MagicMock()

        async def mock_stream():
            event = MagicMock()
            event.type = "run_item_stream_event"
            event.item = MagicMock()
            event.item.type = "message_output_item"
            yield event

        mock_result.stream_events = mock_stream

        # Valid JSON, but qna_topics value isn't in the closed Literal taxonomy.
        bad_payload = json.dumps(
            {
                "response": "Hello from agent",
                "language_code": "en",
                "workflow_codes": ["qna_flow"],
                "qna_topics": ["AMENITIES_AND_FACILITIES.HOT_TUB"],
            }
        )

        import agent_leasing.util.streaming_util as streaming_util

        original_text_output = streaming_util.ItemHelpers.text_message_output
        streaming_util.ItemHelpers.text_message_output = lambda x: bad_payload

        try:
            async for _ in processor.process_events(mock_result):
                pass
            # Should not raise; final_output stays None — same downstream as JSONDecodeError.
            assert processor.final_output is None
        finally:
            streaming_util.ItemHelpers.text_message_output = original_text_output

    @pytest.mark.asyncio
    async def test_custom_json_attribute(self):
        """Test processor with custom JSON attribute."""
        processor = StreamEventProcessor(json_attribute="custom_field")
        mock_result = MagicMock()

        async def mock_stream():
            chunks = ['{"custom_field":"', "Custom", " value", '"}']
            for chunk in chunks:
                event = MagicMock()
                event.type = "raw_response_event"
                event.data = MagicMock()
                event.data.__class__.__name__ = "ResponseTextDeltaEvent"
                event.data.delta = chunk
                yield event

        mock_result.stream_events = mock_stream

        chunks_received = []
        async for chunk in processor.process_events(mock_result):
            if chunk is not None:
                chunks_received.append(chunk)

        assert "".join(chunks_received) == "Custom value"

    @pytest.mark.asyncio
    async def test_skipped_chunks_return_none(self, processor, mock_text_delta_event):
        """Test that chunks before JSON key are skipped (return None)."""
        mock_result = MagicMock()

        async def mock_stream():
            # First chunks don't contain the response value yet
            yield mock_text_delta_event("{")
            yield mock_text_delta_event('"response"')
            yield mock_text_delta_event(':"')
            yield mock_text_delta_event("Hello")

        mock_result.stream_events = mock_stream

        chunks_received = []
        async for chunk in processor.process_events(mock_result):
            if chunk is not None:
                chunks_received.append(chunk)

        # Only "Hello" should be yielded
        assert chunks_received == ["Hello"]

    @pytest.mark.asyncio
    async def test_none_delta_does_not_crash(self, processor, mock_text_delta_event):
        """KNCK-39555: Responses API can emit a text-delta event with delta=None.

        The processor must skip non-str deltas instead of appending them to
        self._streamed_events, which would make "".join(...) raise
        TypeError: sequence item N: expected str instance, NoneType found.

        Reproduces the prod crash at streaming_util.py:338.
        """
        mock_result = MagicMock()

        async def mock_stream():
            yield mock_text_delta_event('{"response":"')
            # 75 valid one-char chunks so the None lands at index 76 out of 77,
            # matching the prod stack ("sequence item 76").
            for _ in range(75):
                yield mock_text_delta_event("a")
            # The bad chunk: upstream emitted a ResponseTextDeltaEvent with delta=None.
            yield mock_text_delta_event(None)
            yield mock_text_delta_event('"}')

        mock_result.stream_events = mock_stream

        chunks_received = []
        async for chunk in processor.process_events(mock_result):
            if chunk is not None:
                chunks_received.append(chunk)

        # Accumulated response should be the 75 "a" chars — the None is dropped,
        # not coerced to the string "None".
        assert "".join(chunks_received) == "a" * 75

    def test_final_output_properties(self, processor):
        """Test final_output and final_output_response properties."""
        # Initially None/empty
        assert processor.final_output is None
        assert processor.final_output_response == ""

        # Set internal state
        processor._final_output = ResidentResponderOutput(response="Test response", language_code="en")
        processor._final_output_response = "Test response"

        # Verify properties
        assert processor.final_output is not None
        assert processor.final_output.response == "Test response"
        assert processor.final_output_response == "Test response"
