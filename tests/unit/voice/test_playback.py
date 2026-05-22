"""Tests for PlaybackTracker — mark registration, playback, response completion."""

from unittest.mock import AsyncMock

import pytest

from agent_leasing.voice.audio.playback import MarkData, PlaybackTracker


class TestPlaybackRegistration:
    def test_register_mark(self):
        t = PlaybackTracker()
        t.register_mark("1", MarkData(item_id="a", content_index=0, byte_count=160))
        assert "1" in t._marks
        assert t._last_mark_for_item["a"] == "1"

    def test_register_updates_last_mark(self):
        t = PlaybackTracker()
        t.register_mark("1", MarkData(item_id="a", content_index=0, byte_count=160))
        t.register_mark("2", MarkData(item_id="a", content_index=0, byte_count=160))
        assert t._last_mark_for_item["a"] == "2"

    def test_has_pending_items(self):
        t = PlaybackTracker()
        assert t.has_pending_items() is False
        t.register_mark("1", MarkData(item_id="a", content_index=0, byte_count=160))
        assert t.has_pending_items() is True

    def test_pending_item_ids(self):
        t = PlaybackTracker()
        t.register_mark("1", MarkData(item_id="a", content_index=0, byte_count=160))
        t.register_mark("2", MarkData(item_id="b", content_index=0, byte_count=160))
        ids = t.pending_item_ids()
        assert set(ids) == {"a", "b"}


class TestPlaybackMarkPlayed:
    @pytest.mark.asyncio
    async def test_non_last_mark_does_not_fire_callback(self):
        t = PlaybackTracker()
        fired = []
        t.on_response_completed = lambda item_id: fired.append(item_id)
        t.register_mark("1", MarkData(item_id="a", content_index=0, byte_count=160))
        t.register_mark("2", MarkData(item_id="a", content_index=0, byte_count=160))
        await t.on_mark_played("1")  # Not the last mark for "a"
        assert fired == []

    @pytest.mark.asyncio
    async def test_last_mark_fires_callback(self):
        t = PlaybackTracker()
        fired = []

        async def callback(item_id):
            fired.append(item_id)

        t.on_response_completed = callback
        t.register_mark("1", MarkData(item_id="a", content_index=0, byte_count=160))
        await t.on_mark_played("1")  # This IS the last mark
        assert fired == ["a"]
        assert "a" not in t._last_mark_for_item

    @pytest.mark.asyncio
    async def test_records_end_time(self):
        t = PlaybackTracker()
        t.on_response_completed = AsyncMock()
        t.register_mark("1", MarkData(item_id="a", content_index=0, byte_count=160))
        await t.on_mark_played("1")
        assert "a" in t.message_end_times


class TestPlaybackTimestamps:
    def test_record_start_time(self):
        t = PlaybackTracker()
        t.record_start_time("a")
        assert "a" in t.message_start_times

    def test_record_start_time_idempotent(self):
        t = PlaybackTracker()
        t.record_start_time("a")
        first = t.message_start_times["a"]
        t.record_start_time("a")
        assert t.message_start_times["a"] is first


class TestPlaybackReset:
    def test_clear(self):
        t = PlaybackTracker()
        t.register_mark("1", MarkData(item_id="a", content_index=0, byte_count=160))
        t.clear()
        assert len(t._marks) == 0
        assert len(t._last_mark_for_item) == 0

    def test_reset(self):
        t = PlaybackTracker()
        t.register_mark("1", MarkData(item_id="a", content_index=0, byte_count=160))
        t.record_start_time("a")
        t.reset()
        assert len(t._marks) == 0
        assert len(t.message_start_times) == 0
        assert len(t.message_end_times) == 0
