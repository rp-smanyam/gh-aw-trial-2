import asyncio
import json
import random
import time
from typing import Any, AsyncIterator

import structlog
from agents import ItemHelpers
from pydantic import ValidationError

from agent_leasing.agent.util import ResidentResponderOutput

DONE = "data: [DONE]\n\n"

STREAMING_START_FRAGMENT = '{"response":"'
STREAMING_STOP_FRAGMENT = '",'

FILLER_PHRASES = [
    "Working on it…",
    "Processing…",
    "Fetching…",
    "Crunching…",
    "In progress…",
    "Hang tight…",
    "Almost there…",
    "Wrapping up this up…",
]

logger = structlog.getLogger()


def elapsed_ms(start_time: float) -> int:
    """Calculate elapsed time in milliseconds since start_time.

    Args:
        start_time: The starting timestamp from time.time()

    Returns:
        Elapsed time in milliseconds as an integer
    """
    return int((time.time() - start_time) * 1000)


def streaming_chunk(data: dict):
    return f"data: {json.dumps(data)}\n\n"


def start(elapsed: int):
    return streaming_chunk({"content": "", "phase": "thinking", "elapsed": elapsed})


def end(elapsed: int):
    return streaming_chunk({"content": "", "status": "done", "done": True, "elapsed": elapsed})


def generating(content: str, elapsed: int):
    return streaming_chunk(
        {
            "content": content,
            "status": "active",
            "phase": "generating",
            "elapsed": elapsed,
        }
    )


def error(content: str):
    return streaming_chunk({"content": content, "status": "error", "done": True})


def heartbeat():
    """Return a heartbeat message to keep the connection alive."""
    return streaming_chunk({"content": "", "status": "active", "phase": "thinking"})


def handoff(elapsed: int, metadata: dict):
    """Return a heartbeat message to keep the connection alive."""
    return streaming_chunk(
        {
            "content": "",
            "status": "active",
            "phase": "thinking",
            "elapsed": "elapsed",
            "metadata": metadata,
        }
    )


def filler(elapsed: int):
    """Return a heartbeat message to keep the connection alive."""
    # Add with newline
    content = random.choice(FILLER_PHRASES) + "\n"
    return streaming_chunk(
        {
            "content": content,
            "status": "active",
            "phase": "thinking",
            "elapsed": "elapsed",
        }
    )


async def with_heartbeat[T](stream: AsyncIterator[T], heartbeat_interval: float = 0.5) -> AsyncIterator[T]:
    """
    Wrap an async iterator to yield heartbeat signals if no items are received
    within the specified interval.

    Args:
        stream: The async iterator to wrap
        heartbeat_interval: Time in seconds before sending a heartbeat (default 0.5s)

    Yields:
        Items from the stream, or None to indicate a heartbeat timeout
    """
    iterator = stream.__aiter__()
    pending_task = None

    try:
        while True:
            # Create task for next item if we don't have one pending
            if pending_task is None:
                pending_task = asyncio.create_task(iterator.__anext__())

            # Race the pending task against the heartbeat timeout
            done, pending = await asyncio.wait(
                [pending_task],
                timeout=heartbeat_interval,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if done:
                # Item arrived - yield it
                task = done.pop()
                pending_task = None  # Clear so we create a new one next iteration
                try:
                    item = task.result()
                    yield item
                except StopAsyncIteration:
                    # Stream is exhausted
                    break
            else:
                # Timeout occurred - yield None as heartbeat signal
                # Keep pending_task alive to continue waiting for the item
                yield None
    finally:
        # Clean up pending task if we exit early
        if pending_task is not None and not pending_task.done():
            pending_task.cancel()
            try:
                await pending_task
            except (asyncio.CancelledError, StopAsyncIteration):
                pass


def process_streaming_json_chunk(key: str, chunk: str, accumulated_json: str) -> str | None:
    """
    Extracts a value from a streaming JSON.

    Because it's a stream, the JSON can be partial.

    Examples:
        # The next part of the response attribute value is "Hello "
        key: response
        chunk: "Hello "
        accumulated_json: "{response:"
        returns: "Hello "

        # The values of the response attribute have not arrived yet
        key: response
        chunk: "response:"
        accumulated_json: "{"
        returns: None

        # The next part of the response attribute value is " you"
        key: response,
        chunk: " you"
        accumulated_json: "{response:"Hello"
        returns: " you"

        # The value has already been extracted so return None
        key: response,
        chunk: "whatever"
        accumulated_json: "{response:"Hello you."reasoning:""}
        returns: None

        # The value has already been extracted so return None
        key: response,
        chunk: "whatever"
        accumulated_json: "{response: "Hello you.",reasoning:"}"
        returns: None

        # The value has already been extracted so return None
        key: response,
        chunk: "whatever"
        accumulated_json: "{response: "Hello you\n.",reasoning:"}"
        returns: None

    Args:
        key: The JSON attribute to extract
        chunk: A chunk from the streaming response
        accumulated_json: The accumulated text

    Returns:
        Text if it can be extracted or otherwise None
    """
    # Search for the key pattern: "key":"
    key_pattern = f'"{key}":'
    key_start = accumulated_json.find(key_pattern)

    if key_start == -1:
        return None  # Key not found yet

    # Find the opening quote after the key
    value_start_search = key_start + len(key_pattern)
    quote_pos = accumulated_json.find('"', value_start_search)

    if quote_pos == -1:
        return None  # Opening quote not found yet

    # The value starts after the opening quote
    value_start = quote_pos + 1

    # Find where the value ends (if it has ended)
    value_end = None
    search_pos = value_start
    while search_pos < len(accumulated_json):
        quote_idx = accumulated_json.find('"', search_pos)
        if quote_idx == -1:
            # No closing quote found yet - value continues
            break

        # Check if this quote is escaped
        if quote_idx > 0 and accumulated_json[quote_idx - 1] == "\\":
            # Escaped quote, keep searching
            search_pos = quote_idx + 1
            continue

        # Found an unescaped quote - this is the end of the value
        value_end = quote_idx
        break

    # Calculate chunk boundaries in accumulated_json
    chunk_start = len(accumulated_json) - len(chunk)
    chunk_end = len(accumulated_json)

    # If value has ended and chunk starts at or after the end, return None
    if value_end is not None and chunk_start >= value_end:
        return None

    # Calculate the intersection of chunk range with value range
    extract_start = max(chunk_start, value_start)
    extract_end = chunk_end if value_end is None else min(chunk_end, value_end)

    # If there's no overlap, return None
    if extract_start >= extract_end:
        return None

    # Extract the relevant portion from the chunk
    offset_in_chunk = extract_start - chunk_start
    length = extract_end - extract_start
    return chunk[offset_in_chunk : offset_in_chunk + length]


class StreamEventProcessor:
    """Processes streaming events from agent runs and yields processed chunks."""

    def __init__(self, json_attribute: str = "response"):
        """
        Initialize the stream event processor.

        Args:
            json_attribute: The JSON attribute to extract from streaming responses
        """
        self.json_attribute = json_attribute
        self.logger = structlog.getLogger()
        self._streamed_events: list[str] = []
        self._final_output: ResidentResponderOutput | None = None
        self._final_output_response: str = ""

    async def process_events(self, result):
        """
        Process streaming events from the agent result.

        Args:
            result: The agent run result with stream_events() method

        Yields:
            tuple: (processed_chunk, final_output) where:
                - processed_chunk: str | None - Text chunk to stream to client
                - final_output: ResidentResponderOutput | None - Complete structured output when available
        """
        async for event in result.stream_events():
            # Handle message output event (final structured output)
            if event.type == "run_item_stream_event" and event.item.type == "message_output_item":
                self._handle_message_output_event(event)
                continue

            # Handle streaming text delta events
            if event.type == "raw_response_event" and hasattr(event.data, "delta"):
                processed_chunk = self._handle_text_delta_event(event)
                if processed_chunk is not None:
                    yield processed_chunk

        # Yield final output at the end if we have it
        if self._final_output is not None:
            yield None

    def _handle_message_output_event(self, event) -> None:
        """
        Handle message output events containing the final structured output.

        Args:
            event: The message output event
        """
        message_text = ItemHelpers.text_message_output(event.item)
        self.logger.info(f"Message output: {message_text}")

        try:
            # Convert to structured output
            self._final_output = ResidentResponderOutput(**json.loads(message_text))
            self._final_output_response = self._final_output.response
        except (json.JSONDecodeError, TypeError, ValidationError) as e:
            # ValidationError catches model-side schema violations — the closed
            # `Literal[QnATopic]` taxonomy can fail Pydantic if strict-mode is
            # off or if the model emits an unknown topic. Resident-facing
            # response deltas have already streamed; dropping the structured
            # output here matches the JSONDecodeError path (no activity events
            # emit, language_code defaults — but the conversation continues).
            self.logger.error(f"Failed to parse message output: {e}")

    def _handle_text_delta_event(self, event) -> str | None:
        """
        Handle text delta events and return processed chunk if available.

        Args:
            event: The text delta event

        Returns:
            Processed text chunk or None if chunk should be skipped
        """
        chunk = event.data.delta
        # The Responses API can emit a text-delta event with a non-str delta
        # (observed: None). Appending it would make "".join(...) below raise
        # TypeError and kill the stream mid-response.
        if not isinstance(chunk, str):
            self.logger.warning(
                "Skipping non-str delta chunk",
                delta=chunk,
                delta_type=type(chunk).__name__,
            )
            return None
        self._streamed_events.append(chunk)

        self.logger.debug(f"Chunk: {chunk}")
        response_so_far = "".join(self._streamed_events)
        self.logger.debug(f"Response so far: {response_so_far}")

        processed_chunk = process_streaming_json_chunk(self.json_attribute, chunk, response_so_far)

        if processed_chunk is not None:
            self.logger.debug(f"Streamed: {processed_chunk}")

        return processed_chunk

    @property
    def final_output(self) -> ResidentResponderOutput | None:
        """Get the final structured output."""
        return self._final_output

    @property
    def final_output_response(self) -> str:
        """Get the final output response text."""
        return self._final_output_response


def _extract_sse_data(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None

    # A chunk may contain multiple SSE lines; join all `data:` lines.
    data_lines: list[str] = []
    for line in value.splitlines():
        if line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").lstrip())

    if not data_lines:
        return None

    data = "\n".join(data_lines).strip()
    return data or None


def aggregate_streaming_outputs(outputs: list[Any]) -> dict[str, str]:
    """
    LangSmith `reduce_fn` for SSE streaming chunks.
    """
    parts: list[str] = []

    for item in outputs:
        data = _extract_sse_data(item)
        if not data or data == "[DONE]":
            continue

        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue

        if not isinstance(payload, dict):
            continue

        content = payload.get("content", "")
        if not content:
            continue

        phase = payload.get("phase")
        status = payload.get("status")

        if phase == "generating" or status == "error":
            parts.append(content)

    return {"message": "".join(parts)}
