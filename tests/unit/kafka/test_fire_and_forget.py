import asyncio
from unittest.mock import MagicMock

import pytest

from agent_leasing.kafka.fire_and_forget import (
    drain_pending_publishes,
    fire_and_forget_publish,
)


@pytest.fixture
def event():
    return {
        "task": {"id": "task-uuid"},
        "activity": {"summary": "x"},
    }


class TestGates:
    def test_skipped_when_disabled(self, event):
        producer = MagicMock()
        pending: set[asyncio.Task] = set()

        task = fire_and_forget_publish(
            producer,
            event,
            pending,
            enabled=False,
            timeout_seconds=0.5,
            log_prefix="test",
        )

        assert task is None
        assert pending == set()
        producer.produce.assert_not_called()

    def test_skipped_when_no_running_loop(self, event):
        producer = MagicMock()
        pending: set[asyncio.Task] = set()

        task = fire_and_forget_publish(
            producer,
            event,
            pending,
            enabled=True,
            timeout_seconds=0.5,
            log_prefix="test",
        )

        assert task is None
        assert pending == set()
        producer.produce.assert_not_called()


@pytest.mark.asyncio
class TestFireAndForget:
    async def test_produce_invoked_on_background_task(self, event):
        producer = MagicMock()
        pending: set[asyncio.Task] = set()

        task = fire_and_forget_publish(
            producer,
            event,
            pending,
            enabled=True,
            timeout_seconds=0.5,
            log_prefix="test",
        )

        assert task is not None
        assert task in pending
        await task

        producer.produce.assert_called_once_with(event)
        assert pending == set()

    async def test_producer_exception_swallowed(self, event):
        producer = MagicMock()
        producer.produce.side_effect = RuntimeError("broken")
        pending: set[asyncio.Task] = set()

        task = fire_and_forget_publish(
            producer,
            event,
            pending,
            enabled=True,
            timeout_seconds=0.5,
            log_prefix="test",
        )
        # Must not re-raise.
        await task

    async def test_timeout_logs_and_drops(self, event, monkeypatch):
        import agent_leasing.kafka.fire_and_forget as module

        async def never_returns(*_args, **_kwargs):
            await asyncio.sleep(10)

        monkeypatch.setattr(module.asyncio, "to_thread", never_returns)
        producer = MagicMock()
        pending: set[asyncio.Task] = set()

        task = fire_and_forget_publish(
            producer,
            event,
            pending,
            enabled=True,
            timeout_seconds=0.01,
            log_prefix="test",
        )
        await task


@pytest.mark.asyncio
class TestDrain:
    async def test_no_op_on_empty(self):
        await drain_pending_publishes(set())

    async def test_awaits_pending(self):
        ran: list[int] = []

        async def slow():
            await asyncio.sleep(0.01)
            ran.append(1)

        pending = {asyncio.create_task(slow()), asyncio.create_task(slow())}
        await drain_pending_publishes(pending)
        assert ran == [1, 1]

    async def test_swallows_task_exceptions(self):
        async def boom():
            raise RuntimeError("nope")

        pending = {asyncio.create_task(boom())}
        await drain_pending_publishes(pending)

    async def test_clears_set_on_exit(self):
        async def noop():
            return None

        pending = {asyncio.create_task(noop()), asyncio.create_task(noop())}
        await drain_pending_publishes(pending)
        assert pending == set()
