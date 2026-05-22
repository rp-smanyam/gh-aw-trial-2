"""Tests for AudioPacer — frame slicing, mark tracking, flush_partial."""

from agent_leasing.voice.audio.pacer import _FRAME_BYTES, _SILENCE_BYTE, AudioChunk, AudioPacer
from agent_leasing.voice.config import VoiceConfig


async def _noop_frame(b: bytes) -> None:
    pass


async def _noop_mark(s: str) -> None:
    pass


def _make_pacer() -> AudioPacer:
    return AudioPacer(VoiceConfig(), send_frame=_noop_frame, send_mark=_noop_mark)


class TestPacerEnqueue:
    def test_empty_audio_returns_empty_mark(self):
        p = _make_pacer()
        mark = p.enqueue(AudioChunk(audio=b""))
        assert mark == ""

    def test_exact_frame_produces_one_frame(self):
        p = _make_pacer()
        mark = p.enqueue(AudioChunk(audio=b"\x00" * _FRAME_BYTES, item_id="item1"))
        assert mark != ""
        assert len(p._frame_q) == 1

    def test_double_frame_produces_two_frames(self):
        p = _make_pacer()
        p.enqueue(AudioChunk(audio=b"\x00" * (_FRAME_BYTES * 2), item_id="item1"))
        assert len(p._frame_q) == 2

    def test_partial_stays_in_partial_buffer(self):
        p = _make_pacer()
        p.enqueue(AudioChunk(audio=b"\x00" * 100, item_id="item1"))
        assert len(p._frame_q) == 0
        assert len(p._partial) == 100

    def test_mark_id_increments(self):
        p = _make_pacer()
        m1 = p.enqueue(AudioChunk(audio=b"\x00" * _FRAME_BYTES, item_id="a"))
        m2 = p.enqueue(AudioChunk(audio=b"\x00" * _FRAME_BYTES, item_id="b"))
        assert m1 != m2
        assert int(m2) > int(m1)

    def test_last_mark_for_item_updated(self):
        p = _make_pacer()
        p.enqueue(AudioChunk(audio=b"\x00" * _FRAME_BYTES, item_id="item1"))
        m2 = p.enqueue(AudioChunk(audio=b"\x00" * _FRAME_BYTES, item_id="item1"))
        assert p.last_mark_for_item("item1") == m2


class TestPacerFlushPartial:
    def test_flush_partial_pads_and_enqueues(self):
        p = _make_pacer()
        p.enqueue(AudioChunk(audio=b"\x00" * 100, item_id="item1"))
        assert len(p._frame_q) == 0
        p.flush_partial()
        assert len(p._frame_q) == 1
        assert len(p._partial) == 0
        # Frame should be padded to _FRAME_BYTES
        frame, _ = p._frame_q[0]
        assert len(frame) == _FRAME_BYTES
        # Padding should be silence bytes
        assert frame[100:] == bytes([_SILENCE_BYTE]) * (_FRAME_BYTES - 100)

    def test_flush_partial_noop_when_empty(self):
        p = _make_pacer()
        p.flush_partial()
        assert len(p._frame_q) == 0


class TestPacerClear:
    def test_clear_empties_queue_and_partial(self):
        p = _make_pacer()
        p.enqueue(AudioChunk(audio=b"\x00" * (_FRAME_BYTES + 50), item_id="item1"))
        assert len(p._frame_q) > 0
        assert len(p._partial) > 0
        p.clear()
        assert len(p._frame_q) == 0
        assert len(p._partial) == 0


class TestPacerItemTracking:
    def test_last_mark_for_item_none_for_unknown(self):
        p = _make_pacer()
        assert p.last_mark_for_item("unknown") is None

    def test_remove_item_tracking(self):
        p = _make_pacer()
        p.enqueue(AudioChunk(audio=b"\x00" * _FRAME_BYTES, item_id="item1"))
        assert p.last_mark_for_item("item1") is not None
        p.remove_item_tracking("item1")
        assert p.last_mark_for_item("item1") is None

    def test_has_pending_items(self):
        p = _make_pacer()
        assert p.has_pending_items() is False
        p.enqueue(AudioChunk(audio=b"\x00" * _FRAME_BYTES, item_id="item1"))
        assert p.has_pending_items() is True
