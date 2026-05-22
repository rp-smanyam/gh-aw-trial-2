"""Tests for EventDispatcher — inline vs deferred event routing."""

import asyncio

import pytest

from agent_leasing.voice.coordination.event_dispatcher import (
    INLINE_EVENT_TYPES,
    EventDispatcher,
)


class _FakeEvent:
    def __init__(self, event_type: str, data: str = ""):
        self.type = event_type
        self.data = data


class TestEventDispatcherInline:
    @pytest.mark.asyncio
    async def test_inline_event_called_immediately(self):
        results = []

        async def handler(event):
            results.append(event.data)

        d = EventDispatcher()
        d.register("audio", handler)

        await d.dispatch(_FakeEvent("audio", "frame1"))
        assert results == ["frame1"]

    @pytest.mark.asyncio
    async def test_all_inline_types(self):
        for event_type in INLINE_EVENT_TYPES:
            results = []

            async def handler(event, _results=results):
                _results.append(True)

            d = EventDispatcher()
            d.register(event_type, handler)
            await d.dispatch(_FakeEvent(event_type))
            assert results == [True], f"{event_type} should be inline"


class TestEventDispatcherDeferred:
    @pytest.mark.asyncio
    async def test_deferred_event_processed_in_background(self):
        results = []

        async def handler(event):
            results.append(event.data)

        d = EventDispatcher()
        d.register("history_updated", handler)
        d.start()

        await d.dispatch(_FakeEvent("history_updated", "hist1"))
        await asyncio.sleep(0.1)

        assert results == ["hist1"]
        await d.shutdown()

    @pytest.mark.asyncio
    async def test_unregistered_event_ignored(self):
        d = EventDispatcher()
        d.start()
        await d.dispatch(_FakeEvent("unknown_type"))
        await d.shutdown()

    @pytest.mark.asyncio
    async def test_multiple_deferred_events_processed(self):
        results = []

        async def handler(event):
            results.append(event.data)

        d = EventDispatcher()
        d.register("test", handler)
        d.start()

        for i in range(5):
            await d.dispatch(_FakeEvent("test", str(i)))

        await asyncio.sleep(0.2)  # Let deferred loop process
        assert len(results) == 5
        await d.shutdown()


class TestEventDispatcherErrorHandling:
    @pytest.mark.asyncio
    async def test_deferred_handler_error_does_not_kill_loop(self):
        call_count = 0

        async def bad_handler(event):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("boom")

        d = EventDispatcher()
        d.register("test", bad_handler)
        d.start()

        await d.dispatch(_FakeEvent("test", "first"))
        await asyncio.sleep(0.1)
        await d.dispatch(_FakeEvent("test", "second"))
        await asyncio.sleep(0.1)

        assert call_count == 2  # Second event still processed
        await d.shutdown()
