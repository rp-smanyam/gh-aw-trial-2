import asyncio
import json

import pytest

from agent_leasing.util.streaming_util import (
    DONE,
    FILLER_PHRASES,
    _extract_sse_data,
    aggregate_streaming_outputs,
    end,
    error,
    filler,
    generating,
    handoff,
    heartbeat,
    start,
    streaming_chunk,
    with_heartbeat,
)


class TestStreamingChunk:
    def test_streaming_chunk_returns_sse_format(self):
        data = {"content": "hello", "status": "active"}
        result = streaming_chunk(data)
        assert result == f"data: {json.dumps(data)}\n\n"

    def test_streaming_chunk_empty_dict(self):
        result = streaming_chunk({})
        assert result == "data: {}\n\n"


class TestHeartbeat:
    def test_heartbeat_returns_thinking_phase(self):
        result = heartbeat()
        parsed = json.loads(result.removeprefix("data: ").strip())
        assert parsed["content"] == ""
        assert parsed["status"] == "active"
        assert parsed["phase"] == "thinking"

    def test_heartbeat_has_no_elapsed(self):
        result = heartbeat()
        parsed = json.loads(result.removeprefix("data: ").strip())
        assert "elapsed" not in parsed


class TestHandoff:
    def test_handoff_includes_metadata(self):
        metadata = {"target": "staff", "reason": "escalation"}
        result = handoff(elapsed=500, metadata=metadata)
        parsed = json.loads(result.removeprefix("data: ").strip())
        assert parsed["metadata"] == metadata
        assert parsed["phase"] == "thinking"
        assert parsed["status"] == "active"
        assert parsed["content"] == ""

    def test_handoff_empty_metadata(self):
        result = handoff(elapsed=0, metadata={})
        parsed = json.loads(result.removeprefix("data: ").strip())
        assert parsed["metadata"] == {}


class TestFiller:
    def test_filler_returns_known_phrase(self):
        result = filler(elapsed=100)
        parsed = json.loads(result.removeprefix("data: ").strip())
        # Content should be one of the FILLER_PHRASES with a trailing newline
        content = parsed["content"]
        assert content.endswith("\n")
        assert content.rstrip("\n") in FILLER_PHRASES

    def test_filler_phase_is_thinking(self):
        result = filler(elapsed=100)
        parsed = json.loads(result.removeprefix("data: ").strip())
        assert parsed["phase"] == "thinking"
        assert parsed["status"] == "active"


class TestWithHeartbeat:
    @pytest.mark.asyncio
    async def test_with_heartbeat_yields_items(self):
        async def fast_stream():
            yield "a"
            yield "b"

        results = []
        async for item in with_heartbeat(fast_stream()):
            results.append(item)
        assert results == ["a", "b"]

    @pytest.mark.asyncio
    async def test_with_heartbeat_yields_none_on_timeout(self):
        async def slow_stream():
            await asyncio.sleep(2)
            yield "late"

        results = []
        async for item in with_heartbeat(slow_stream(), heartbeat_interval=0.1):
            results.append(item)
            if item is None:
                break  # got heartbeat, stop
        assert None in results

    @pytest.mark.asyncio
    async def test_with_heartbeat_empty_stream(self):
        async def empty_stream():
            return
            yield  # noqa: F841 — makes this an async generator

        results = []
        async for item in with_heartbeat(empty_stream()):
            results.append(item)
        assert results == []

    @pytest.mark.asyncio
    async def test_with_heartbeat_multiple_heartbeats_then_item(self):
        """Heartbeats fire while waiting, then the real item arrives."""

        async def delayed_stream():
            await asyncio.sleep(0.35)
            yield "finally"

        results = []
        async for item in with_heartbeat(delayed_stream(), heartbeat_interval=0.1):
            results.append(item)
        # Should have at least 2 None heartbeats before "finally"
        heartbeats = [r for r in results if r is None]
        items = [r for r in results if r is not None]
        assert len(heartbeats) >= 2
        assert items == ["finally"]

    @pytest.mark.asyncio
    async def test_with_heartbeat_cleanup_on_break(self):
        """Breaking out of the generator should cancel pending tasks cleanly."""

        async def infinite_stream():
            count = 0
            while True:
                yield count
                count += 1
                await asyncio.sleep(0.05)

        results = []
        async for item in with_heartbeat(infinite_stream(), heartbeat_interval=0.5):
            results.append(item)
            if len(results) >= 3:
                break
        assert len(results) == 3


# ---------------------------------------------------------------------------
# _extract_sse_data
# ---------------------------------------------------------------------------


class TestExtractSseData:
    def test_extracts_single_data_line(self):
        chunk = 'data: {"key": "val"}\n\n'
        assert _extract_sse_data(chunk) == '{"key": "val"}'

    def test_returns_none_for_empty_string(self):
        assert _extract_sse_data("") is None

    def test_returns_none_for_whitespace_only(self):
        assert _extract_sse_data("   \n\n") is None

    def test_returns_none_when_no_data_prefix(self):
        assert _extract_sse_data("event: message\nid: 1\n\n") is None

    def test_returns_done_sentinel(self):
        assert _extract_sse_data("data: [DONE]\n\n") == "[DONE]"

    def test_strips_leading_space_after_colon(self):
        assert _extract_sse_data("data:   hello\n\n") == "hello"

    def test_joins_multiple_data_lines(self):
        chunk = "data: line1\ndata: line2\n\n"
        assert _extract_sse_data(chunk) == "line1\nline2"

    def test_ignores_non_data_lines_in_mixed_chunk(self):
        chunk = "event: message\ndata: payload\nid: 42\n\n"
        assert _extract_sse_data(chunk) == "payload"


# ---------------------------------------------------------------------------
# aggregate_streaming_outputs
# ---------------------------------------------------------------------------


class TestAggregateStreamingOutputs:
    def test_empty_list_returns_empty_message(self):
        assert aggregate_streaming_outputs([]) == {"message": ""}

    def test_collects_generating_chunks(self):
        chunks = [generating("Hello ", elapsed=10), generating("world", elapsed=20)]
        assert aggregate_streaming_outputs(chunks) == {"message": "Hello world"}

    def test_skips_start_event(self):
        assert aggregate_streaming_outputs([start(elapsed=0)]) == {"message": ""}

    def test_skips_end_event(self):
        assert aggregate_streaming_outputs([end(elapsed=100)]) == {"message": ""}

    def test_skips_done_sentinel(self):
        assert aggregate_streaming_outputs([DONE]) == {"message": ""}

    def test_includes_error_status(self):
        chunks = [error("Something went wrong")]
        assert aggregate_streaming_outputs(chunks) == {"message": "Something went wrong"}

    def test_skips_thinking_phase_events(self):
        chunks = [start(0), filler(10), heartbeat()]
        assert aggregate_streaming_outputs(chunks) == {"message": ""}

    def test_skips_handoff_event(self):
        chunks = [handoff(elapsed=0, metadata={"human_handoff": True})]
        assert aggregate_streaming_outputs(chunks) == {"message": ""}

    def test_mixed_events_collects_only_generating(self):
        chunks = [
            start(0),
            generating("Hello ", 10),
            generating("there", 20),
            end(30),
            DONE,
        ]
        assert aggregate_streaming_outputs(chunks) == {"message": "Hello there"}

    def test_skips_non_json_data(self):
        assert aggregate_streaming_outputs(["data: not-valid-json\n\n"]) == {"message": ""}

    def test_skips_non_dict_json(self):
        assert aggregate_streaming_outputs(["data: [1,2,3]\n\n"]) == {"message": ""}

    def test_skips_chunk_with_empty_content(self):
        chunk = 'data: {"content": "", "phase": "generating"}\n\n'
        assert aggregate_streaming_outputs([chunk]) == {"message": ""}

    def test_returns_dict_with_message_key(self):
        result = aggregate_streaming_outputs([generating("hi", 0)])
        assert isinstance(result, dict)
        assert set(result.keys()) == {"message"}
