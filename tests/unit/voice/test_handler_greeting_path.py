"""Tests for the VoiceHandler greeting-agent fast path.

These tests drive the pieces of ``VoiceHandler`` that coordinate the
parallel greeting agent / full agent startup. They avoid exercising the
full handler lifecycle (transport, session, tracer) by constructing a
handler instance and overriding only the collaborators that matter for
each behavior under test.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_leasing.api.model import AskRequest, examples
from agent_leasing.util.tracing_utils import DeferredSpanTree
from agent_leasing.voice.coordination.interaction_policy import (
    DefaultPolicy,
    GreetingPolicy,
)
from agent_leasing.voice.handler import VOICE_STARTUP_PHASES, VoiceHandler


def _make_handler(*, greeting_agent_enabled: bool = True, timeout_s: float = 30.0) -> VoiceHandler:
    """Construct a VoiceHandler without running __init__ — avoids real asyncio state."""
    handler = VoiceHandler.__new__(VoiceHandler)
    handler.config = SimpleNamespace(
        greeting_agent_enabled=greeting_agent_enabled,
        greeting_agent_init_timeout_seconds=timeout_s,
    )
    handler.call_active = True
    handler._shutdown_reason = None
    handler._full_agent_task = None
    handler.voice_agent = MagicMock()
    handler.voice_agent.setup = AsyncMock(return_value=MagicMock())
    handler.session_manager = MagicMock()
    handler.session_manager.session = MagicMock()
    handler.session_manager.update_agent = AsyncMock()
    handler.transport = MagicMock()
    handler.transport.call_metadata = SimpleNamespace(call_sid="CA-test")
    handler.root_run = None
    handler._parallel_init_span = MagicMock()
    handler._parallel_init_span.attach.return_value = nullcontext()
    return handler


class TestInitFullVoiceAgent:
    @pytest.mark.asyncio
    async def test_delegates_to_voice_agent_setup(self):
        handler = _make_handler()
        await handler._init_full_voice_agent(
            trace_id="trace_0000000000000000000000000000abcd", group_id="group_0000000000000000000000000000abcd"
        )
        handler.voice_agent.setup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_propagates_setup_error(self):
        handler = _make_handler()
        handler.voice_agent.setup.side_effect = RuntimeError("mcp boom")
        with pytest.raises(RuntimeError, match="mcp boom"):
            await handler._init_full_voice_agent(
                trace_id="trace_0000000000000000000000000000abcd", group_id="group_0000000000000000000000000000abcd"
            )


class TestAwaitFullAgentReady:
    @pytest.mark.asyncio
    async def test_returns_true_when_no_task(self):
        handler = _make_handler()
        handler._full_agent_task = None
        assert await handler._await_full_agent_ready() is True

    @pytest.mark.asyncio
    async def test_returns_true_when_task_succeeds(self):
        handler = _make_handler()

        async def ok() -> None:
            return None

        handler._full_agent_task = asyncio.create_task(ok())
        assert await handler._await_full_agent_ready() is True
        assert handler._full_agent_task is None

    @pytest.mark.asyncio
    async def test_transfers_on_timeout(self):
        handler = _make_handler(timeout_s=0.01)

        async def slow() -> None:
            await asyncio.sleep(5)

        handler._full_agent_task = asyncio.create_task(slow())
        with patch("agent_leasing.voice.handler.transfer_call_on_init_failure", new_callable=AsyncMock) as mock_tx:
            assert await handler._await_full_agent_ready() is False
            mock_tx.assert_awaited_once_with("CA-test")
        assert handler.call_active is False
        assert handler._shutdown_reason == "agent_init_failure"
        assert handler._full_agent_task is None

    @pytest.mark.asyncio
    async def test_transfers_on_exception(self):
        handler = _make_handler()

        async def boom() -> None:
            raise RuntimeError("init failed")

        handler._full_agent_task = asyncio.create_task(boom())
        # Let the task fail before we await to make the path deterministic.
        with contextlib_suppress_exception():
            await handler._full_agent_task
        with patch("agent_leasing.voice.handler.transfer_call_on_init_failure", new_callable=AsyncMock) as mock_tx:
            assert await handler._await_full_agent_ready() is False
            mock_tx.assert_awaited_once_with("CA-test")
        assert handler.call_active is False
        assert handler._full_agent_task is None


class TestGreetingToDefaultSwap:
    """The greeting→default transition waits for the full agent before swap."""

    @pytest.mark.asyncio
    async def test_swaps_in_full_agent_after_greeting(self):
        handler = _make_handler()
        handler.policy = GreetingPolicy()
        handler.buffer = MagicMock()
        handler.buffer.suppress_flush = True
        handler.buffer.pending_bytes = 0
        handler.call_state = MagicMock()
        handler.call_state.first_user_speaking_started_at = None
        handler.playback = MagicMock()
        handler.playback.has_pending_items.return_value = True
        handler.playback.message_start_times = {}
        handler.playback.message_end_times = {}
        handler.filler = MagicMock()
        handler.filler.filler_item_ids = set()
        handler.tracer = MagicMock()
        handler.tracer.fire_trace_task = MagicMock()
        handler.ctx = MagicMock()
        handler.root_run = None

        full_agent = MagicMock()
        handler.voice_agent.setup = AsyncMock(return_value=full_agent)
        handler.voice_agent.agent = MagicMock(return_value=full_agent)
        handler._full_agent_task = asyncio.create_task(
            handler._init_full_voice_agent(
                trace_id="trace_0000000000000000000000000000abcd", group_id="group_0000000000000000000000000000abcd"
            )
        )

        await handler._on_response_completed("item-1")

        assert isinstance(handler.policy, DefaultPolicy)
        handler.session_manager.update_agent.assert_awaited_once_with(full_agent)
        assert handler.ctx.welcome_greeting_delivered is True
        assert handler._full_agent_task is None

    @pytest.mark.asyncio
    async def test_skips_update_agent_when_init_fails(self):
        handler = _make_handler()
        handler.policy = GreetingPolicy()
        handler.buffer = MagicMock()
        handler.buffer.suppress_flush = True
        handler.buffer.pending_bytes = 0
        handler.call_state = MagicMock()
        handler.playback = MagicMock()
        handler.playback.has_pending_items.return_value = True
        handler.playback.message_start_times = {}
        handler.playback.message_end_times = {}
        handler.filler = MagicMock()
        handler.filler.filler_item_ids = set()
        handler.tracer = MagicMock()
        handler.ctx = MagicMock()
        handler.root_run = None

        async def boom() -> None:
            raise RuntimeError("init failed")

        handler._full_agent_task = asyncio.create_task(boom())
        with contextlib_suppress_exception():
            await handler._full_agent_task
        with patch("agent_leasing.voice.handler.transfer_call_on_init_failure", new_callable=AsyncMock):
            await handler._on_response_completed("item-1")

        handler.session_manager.update_agent.assert_not_called()
        assert handler.call_active is False


class TestVoiceHandlerMetadata:
    """Regression tests for refactored voice metadata shape."""

    def test_build_metadata_excludes_input_and_stays_within_limit(self):
        handler = _make_handler()
        handler._session_start_time = None
        handler.config.environment = "test"
        handler.ctx = SimpleNamespace(openai_group_url="https://platform.openai.com/traces/group/test")
        handler.session_manager.trace_id = "trace_0000000000000000000000000000abcd"

        ask_request = AskRequest(**examples.ASK_REQUEST_RESIDENT_VOICE_KNCK)
        metadata = handler._build_metadata(ask_request)

        assert "input" not in metadata
        # OpenAI Realtime currently enforces a maximum of 16 metadata fields.
        # Keep this assertion so future refactors do not reintroduce a 17th field.
        assert len(metadata) <= 16


def contextlib_suppress_exception():
    """Helper: async-aware suppress for awaiting a task known to raise."""
    import contextlib

    return contextlib.suppress(Exception)


def _make_handler_for_ttfar(*, greeting_agent_enabled: bool) -> VoiceHandler:
    """Minimal VoiceHandler for exercising the TTFAR stamp on `_send_frame_marked`."""
    handler = VoiceHandler.__new__(VoiceHandler)
    handler.config = SimpleNamespace(greeting_agent_enabled=greeting_agent_enabled)
    handler._startup_span = DeferredSpanTree("welcome_agent_init", VOICE_STARTUP_PHASES)
    handler.root_run = MagicMock()
    handler._initial_greeting_latency_recorded = False
    handler.transport = MagicMock()
    handler.transport.send_audio = AsyncMock()
    return handler


def _ttfar_metadata_payloads(root_run: MagicMock) -> list[dict]:
    return [c.args[0] for c in root_run.add_metadata.call_args_list if "initial_greeting_latency_ms" in c.args[0]]


class TestInitialGreetingLatencyV2:
    """v2 voice handler stamps `initial_greeting_latency_ms` on the root run
    when the first greeting audio frame is sent to transport. Covers both the
    sequential and greeting-agent startup paths (they converge on
    `_send_frame_marked`)."""

    @pytest.mark.parametrize("greeting_agent_enabled", [False, True])
    @pytest.mark.asyncio
    async def test_records_latency_on_first_utterance(self, greeting_agent_enabled):
        handler = _make_handler_for_ttfar(greeting_agent_enabled=greeting_agent_enabled)
        handler._startup_span.mark("start_event_received")
        handler._startup_span.mark("first_audio_received")

        await handler._send_frame_marked(b"audio")

        payloads = _ttfar_metadata_payloads(handler.root_run)
        assert len(payloads) == 1
        assert isinstance(payloads[0]["initial_greeting_latency_ms"], int)
        assert payloads[0]["initial_greeting_latency_ms"] >= 0
        assert handler._initial_greeting_latency_recorded is True
        handler.transport.send_audio.assert_awaited_once_with(b"audio")

    @pytest.mark.parametrize("greeting_agent_enabled", [False, True])
    @pytest.mark.asyncio
    async def test_records_only_once_across_multiple_frames(self, greeting_agent_enabled):
        handler = _make_handler_for_ttfar(greeting_agent_enabled=greeting_agent_enabled)
        handler._startup_span.mark("start_event_received")
        handler._startup_span.mark("first_audio_received")

        await handler._send_frame_marked(b"frame-1")
        await handler._send_frame_marked(b"frame-2")
        await handler._send_frame_marked(b"frame-3")

        assert len(_ttfar_metadata_payloads(handler.root_run)) == 1

    @pytest.mark.asyncio
    async def test_no_record_before_first_audio_received(self):
        handler = _make_handler_for_ttfar(greeting_agent_enabled=False)
        handler._startup_span.mark("start_event_received")
        # Pre-greeting silence frames: first_audio_received not yet marked.
        await handler._send_frame_marked(b"silence")

        assert _ttfar_metadata_payloads(handler.root_run) == []
        assert handler._initial_greeting_latency_recorded is False

    @pytest.mark.asyncio
    async def test_no_crash_when_root_run_is_none(self):
        handler = _make_handler_for_ttfar(greeting_agent_enabled=False)
        handler.root_run = None
        handler._startup_span.mark("start_event_received")
        handler._startup_span.mark("first_audio_received")

        await handler._send_frame_marked(b"audio")
        handler.transport.send_audio.assert_awaited_once()
