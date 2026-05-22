"""Tests for AudioBuffer — append, flush, suppress."""

from unittest.mock import AsyncMock

import pytest

from agent_leasing.voice.audio.buffer import AudioBuffer
from agent_leasing.voice.config import VoiceConfig


def _make_buffer() -> tuple[AudioBuffer, AsyncMock]:
    send = AsyncMock()
    buf = AudioBuffer(VoiceConfig(), send_audio=send)
    return buf, send


class TestBufferAppend:
    def test_append_returns_false_when_below_threshold(self):
        buf, _ = _make_buffer()
        result = buf.append(b"\x00" * 100)
        assert result is False

    def test_append_returns_true_when_threshold_reached(self):
        buf, _ = _make_buffer()
        # buffer_size_bytes = sample_rate * chunk_seconds = 8000 * 0.05 = 400
        result = buf.append(b"\x00" * 400)
        assert result is True

    def test_pending_bytes(self):
        buf, _ = _make_buffer()
        buf.append(b"\x00" * 100)
        assert buf.pending_bytes == 100


class TestBufferFlush:
    @pytest.mark.asyncio
    async def test_flush_sends_audio(self):
        buf, send = _make_buffer()
        buf.append(b"\x00" * 100)
        await buf.flush()
        send.assert_called_once()
        assert buf.pending_bytes == 0

    @pytest.mark.asyncio
    async def test_flush_noop_when_empty(self):
        buf, send = _make_buffer()
        await buf.flush()
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_flush_suppressed_clears_buffer(self):
        buf, send = _make_buffer()
        buf.suppress_flush = True
        buf.append(b"\x00" * 100)
        await buf.flush()
        send.assert_not_called()
        assert buf.pending_bytes == 0  # Buffer was cleared, not sent


class TestBufferClear:
    def test_clear(self):
        buf, _ = _make_buffer()
        buf.append(b"\x00" * 100)
        buf.clear()
        assert buf.pending_bytes == 0
