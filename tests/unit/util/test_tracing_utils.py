import json
from unittest.mock import MagicMock, patch

import pytest

from agent_leasing.util.tracing_utils import (
    MAX_SPAN_DATA_TOTAL_OBJECT_BYTES,
    MAX_SPAN_DATA_VALUE_BYTES,
    DeferredSpanTree,
    build_openai_trace_url,
    record_initial_greeting_latency,
    set_span_data,
)


class _DummySpanData:
    def __init__(self):
        self.data: dict = {}


class _DummySpan:
    def __init__(self):
        self.span_data = _DummySpanData()


class TestBuildOpenaiTraceUrl:
    def test_build_openai_trace_url_valid(self):
        assert build_openai_trace_url("abc123") == "https://platform.openai.com/logs/trace?trace_id=abc123"

    def test_build_openai_trace_url_none(self):
        assert build_openai_trace_url(None) is None

    def test_build_openai_trace_url_empty(self):
        assert build_openai_trace_url("") is None


class TestSetSpanDataSizing:
    def test_each_value_is_capped_to_5kb(self):
        span = _DummySpan()
        big = "x" * (6 * 1024)

        set_span_data(span, big_value=big)

        stored = span.span_data.data["big_value"]
        assert isinstance(stored, str)
        assert len(stored.encode("utf-8")) <= 5 * 1024

    def test_nested_value_is_structurally_capped_and_json_serializable(self):
        span = _DummySpan()
        value = {
            "keys": [
                "a" * (MAX_SPAN_DATA_VALUE_BYTES + 100),
                "b" * (MAX_SPAN_DATA_VALUE_BYTES + 100),
            ]
        }

        set_span_data(span, nested=value)

        stored_value = span.span_data.data["nested"]

        dumped = json.dumps(stored_value, ensure_ascii=False, separators=(",", ":"), default=str)
        assert len(dumped.encode("utf-8")) <= MAX_SPAN_DATA_VALUE_BYTES

    def test_after_9kb_only_keys_are_added_without_values_and_total_under_10kb(self):
        span = _DummySpan()

        # Two ~5KB values will push us past the 9KB soft cap; after that,
        # remaining keys should be included with None (if they fit under 10KB).
        updates = {
            "k1": "a" * (5 * 1024 - 64),
            "k2": "b" * (5 * 1024 - 64),
            "k3": "c" * (5 * 1024 - 64),
            "k4": "d" * (5 * 1024 - 64),
        }

        set_span_data(span, **updates)

        payload = span.span_data.data
        total_bytes = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))
        assert total_bytes <= MAX_SPAN_DATA_TOTAL_OBJECT_BYTES

        # At least one of the later keys should have been stored as None (key name only).
        assert payload.get("k3") is None or payload.get("k4") is None


# --- DeferredSpanTree tests ---

SAMPLE_PHASES = [
    ("phase_a", "a_start", "a_end"),
    ("phase_b", "b_start", "b_end"),
]


def _mock_run():
    run = MagicMock()
    run.create_child.return_value = MagicMock()
    run.create_child.return_value.create_child.return_value = MagicMock()
    return run


class TestDeferredSpanTree:
    def test_mark_is_idempotent(self):
        t = DeferredSpanTree("test", SAMPLE_PHASES)
        t.mark("x")
        first = t._marks["x"][0]
        t.mark("x")
        assert t._marks["x"][0] == first

    def test_elapsed_ms(self):
        t = DeferredSpanTree("test", SAMPLE_PHASES)
        t.mark("s")
        t.mark("e")
        assert t.elapsed_ms("s", "e") >= 0
        assert t.elapsed_ms("s", "missing") is None

    def test_attach_creates_run_and_returns_context_manager(self):
        root = _mock_run()
        t = DeferredSpanTree("test", SAMPLE_PHASES)
        cm = t.attach(root)
        assert t._run is root.create_child.return_value
        assert hasattr(cm, "__enter__")

    @pytest.mark.asyncio
    async def test_finalize_with_attach(self):
        root = _mock_run()
        t = DeferredSpanTree("test", SAMPLE_PHASES)
        t._run = MagicMock()
        t._run.create_child.return_value = MagicMock()
        t.mark("a_start")
        t.mark("a_end")
        t.mark("b_start")
        t.mark("b_end")
        with patch("agent_leasing.util.tracing_utils.asyncio.to_thread") as m:
            m.return_value = None
            await t.finalize(root)
        assert t._run.create_child.call_count == 2
        root.create_child.assert_not_called()

    @pytest.mark.asyncio
    async def test_finalize_fallback(self):
        root = _mock_run()
        t = DeferredSpanTree("test", SAMPLE_PHASES)
        t.mark("a_start")
        t.mark("a_end")
        with patch("agent_leasing.util.tracing_utils.asyncio.to_thread") as m:
            m.return_value = None
            await t.finalize(root)
        root.create_child.assert_called_once()

    @pytest.mark.asyncio
    async def test_finalize_skips_missing_marks(self):
        root = _mock_run()
        t = DeferredSpanTree("test", SAMPLE_PHASES)
        t.mark("a_start")
        t.mark("a_end")
        with patch("agent_leasing.util.tracing_utils.asyncio.to_thread") as m:
            m.return_value = None
            await t.finalize(root)
        parent = root.create_child.return_value
        assert parent.create_child.call_count == 1

    @pytest.mark.asyncio
    async def test_finalize_idempotent(self):
        root = _mock_run()
        t = DeferredSpanTree("test", SAMPLE_PHASES)
        t.mark("a_start")
        t.mark("a_end")
        with patch("agent_leasing.util.tracing_utils.asyncio.to_thread") as m:
            m.return_value = None
            await t.finalize(root)
            await t.finalize(root)
        root.create_child.assert_called_once()

    @pytest.mark.asyncio
    async def test_finalize_no_parent_noop(self):
        t = DeferredSpanTree("test", SAMPLE_PHASES)
        t.mark("a_start")
        await t.finalize(None)
        assert not t._finalized

    @pytest.mark.asyncio
    async def test_finalize_swallows_errors(self):
        root = _mock_run()
        t = DeferredSpanTree("test", SAMPLE_PHASES)
        t.mark("a_start")
        with patch("agent_leasing.util.tracing_utils.asyncio.to_thread", side_effect=Exception):
            await t.finalize(root)
        assert t._finalized


VOICE_STARTUP_PHASES_FOR_TEST = [
    ("process_start_payload", "start_event_received", "start_payload_processed"),
    ("first_utterance_sent", "first_audio_received", "first_utterance_sent"),
]


class TestRecordInitialGreetingLatency:
    def _span_with_anchors(self) -> DeferredSpanTree:
        span = DeferredSpanTree("welcome_agent_init", VOICE_STARTUP_PHASES_FOR_TEST)
        span.mark("start_event_received")
        span.mark("first_audio_received")
        span.mark("first_utterance_sent")
        return span

    def test_writes_metadata_and_returns_ms(self):
        root = MagicMock()
        span = self._span_with_anchors()
        result = record_initial_greeting_latency(root, span)
        assert isinstance(result, int)
        assert result >= 0
        root.add_metadata.assert_called_once()
        payload = root.add_metadata.call_args.args[0]
        assert "initial_greeting_latency_ms" in payload
        assert payload["initial_greeting_latency_ms"] == result

    def test_returns_none_when_start_mark_missing(self):
        root = MagicMock()
        span = DeferredSpanTree("welcome_agent_init", VOICE_STARTUP_PHASES_FOR_TEST)
        span.mark("first_utterance_sent")
        assert record_initial_greeting_latency(root, span) is None
        root.add_metadata.assert_not_called()

    def test_returns_none_when_end_mark_missing(self):
        root = MagicMock()
        span = DeferredSpanTree("welcome_agent_init", VOICE_STARTUP_PHASES_FOR_TEST)
        span.mark("start_event_received")
        assert record_initial_greeting_latency(root, span) is None
        root.add_metadata.assert_not_called()

    def test_returns_none_when_root_run_is_none(self):
        span = self._span_with_anchors()
        # Should not crash, just return None
        assert record_initial_greeting_latency(None, span) is None

    def test_swallows_add_metadata_errors(self):
        root = MagicMock()
        root.add_metadata.side_effect = RuntimeError("transport gone")
        span = self._span_with_anchors()
        assert record_initial_greeting_latency(root, span) is None
