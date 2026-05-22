"""Tests for ResponseGate — response serialization and stale result detection."""

import asyncio

import pytest

from agent_leasing.voice.session.response_gate import ResponseGate


class TestResponseGateBasic:
    def test_initial_state(self):
        gate = ResponseGate()
        assert gate.turn_id == 0

    @pytest.mark.asyncio
    async def test_acquire_succeeds_when_idle(self):
        gate = ResponseGate()
        ok = await gate.acquire(0)
        assert ok is True

    @pytest.mark.asyncio
    async def test_acquire_fails_with_stale_turn_id(self):
        gate = ResponseGate()
        gate.on_interrupt()  # turn_id -> 1
        ok = await gate.acquire(0)  # snapshot was 0
        assert ok is False

    @pytest.mark.asyncio
    async def test_acquire_succeeds_with_current_turn_id(self):
        gate = ResponseGate()
        gate.on_interrupt()  # turn_id -> 1
        ok = await gate.acquire(1)
        assert ok is True

    def test_on_interrupt_increments_turn_id(self):
        gate = ResponseGate()
        assert gate.turn_id == 0
        gate.on_interrupt()
        assert gate.turn_id == 1
        gate.on_interrupt()
        assert gate.turn_id == 2

    @pytest.mark.asyncio
    async def test_on_response_completed_unblocks_next_acquire(self):
        gate = ResponseGate()
        # First acquire — marks response as active
        ok = await gate.acquire(0)
        assert ok is True

        # Second acquire in a task — should block until response completes
        result = None

        async def second_acquire():
            nonlocal result
            result = await gate.acquire(0)

        task = asyncio.create_task(second_acquire())
        await asyncio.sleep(0.05)
        assert result is None  # Still blocked

        gate.on_response_completed()
        await asyncio.sleep(0.05)
        assert result is True  # Unblocked

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestResponseGateInterruptDuringWait:
    @pytest.mark.asyncio
    async def test_interrupt_while_waiting_returns_stale(self):
        """If an interrupt arrives while a second acquire is waiting,
        the waiter should see staleness and return False."""
        gate = ResponseGate()

        # First acquire — holds the response
        ok = await gate.acquire(0)
        assert ok is True

        result = None

        async def second_acquire():
            nonlocal result
            result = await gate.acquire(0)  # snapshot=0

        task = asyncio.create_task(second_acquire())
        await asyncio.sleep(0.05)

        # Interrupt — turn_id goes to 1, response_done is set
        gate.on_interrupt()
        await asyncio.sleep(0.05)

        # The waiter should have gotten False (stale)
        assert result is False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
