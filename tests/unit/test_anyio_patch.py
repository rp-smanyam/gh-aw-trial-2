"""Tests for the anyio _deliver_cancellation monkey-patch (server.py).

Reproduces KNCK-39169: clearing _tasks in the patch caused an AssertionError
in anyio's TaskGroup.task_done callback when orphaned tasks eventually completed.
The fix: stop the CPU spin without clearing _tasks, using a permanent flag.
"""

import asyncio
import time

import pytest
from anyio._backends._asyncio import CancelScope, _task_states


def _make_task_state(scope):
    return type("TaskState", (), {"parent_id": 0, "cancel_scope": scope})()


def _anyio_task_done_callback(_task):
    """Simplified version of anyio's TaskGroup._spawn.task_done.

    Mirrors the assertion logic at anyio/_backends/_asyncio.py:811-816.
    """
    task_state = _task_states[_task]
    assert task_state.cancel_scope is not None
    assert _task in task_state.cancel_scope._tasks
    task_state.cancel_scope._tasks.remove(_task)
    del _task_states[_task]


class TestKNCK39169Reproduction:
    """Prove that clearing cancel_scope._tasks breaks task_done callbacks."""

    @pytest.mark.asyncio
    async def test_clearing_cancel_scope_tasks_causes_assertion_error(self):
        """_tasks.clear() → AssertionError in task_done callback.

        This is the mechanism behind KNCK-39169: orphaned tasks complete
        after _tasks has been emptied, and their done callback asserts
        the task should still be in the set.
        """
        errors: list[BaseException] = []

        def capture_handler(loop, context):
            exc = context.get("exception")
            if exc:
                errors.append(exc)

        loop = asyncio.get_event_loop()
        old_handler = loop.get_exception_handler()
        loop.set_exception_handler(capture_handler)

        try:
            release = asyncio.Event()

            async def worker():
                await release.wait()

            scope = CancelScope()
            task = asyncio.create_task(worker())
            _task_states[task] = _make_task_state(scope)
            scope._tasks.add(task)
            task.add_done_callback(_anyio_task_done_callback)

            assert task in scope._tasks

            # Simulate what the OLD patch did
            scope._tasks.clear()

            # Task completes → task_done fires → assertion fails
            release.set()
            await asyncio.sleep(0.05)

            assertion_errors = [e for e in errors if isinstance(e, AssertionError)]
            assert len(assertion_errors) > 0, (
                "Expected AssertionError from task_done callback after _tasks.clear(). "
                "This proves KNCK-39169: clearing _tasks breaks task_done."
            )

        finally:
            _task_states.pop(task, None)
            loop.set_exception_handler(old_handler)

    @pytest.mark.asyncio
    async def test_not_clearing_tasks_avoids_assertion_error(self):
        """Stopping cancellation without clearing _tasks is safe.

        Tasks remain tracked, task_done callbacks clean up normally.
        """
        errors: list[BaseException] = []

        def capture_handler(loop, context):
            exc = context.get("exception")
            if exc:
                errors.append(exc)

        loop = asyncio.get_event_loop()
        old_handler = loop.get_exception_handler()
        loop.set_exception_handler(capture_handler)

        try:
            release = asyncio.Event()

            async def worker():
                await release.wait()

            scope = CancelScope()
            task = asyncio.create_task(worker())
            _task_states[task] = _make_task_state(scope)
            scope._tasks.add(task)
            task.add_done_callback(_anyio_task_done_callback)

            # THE FIX: stop the cancel handle without clearing _tasks
            scope._cancel_handle = None

            release.set()
            await asyncio.sleep(0.05)

            assertion_errors = [e for e in errors if isinstance(e, AssertionError)]
            assert len(assertion_errors) == 0, (
                f"Should NOT get AssertionError when _tasks is not cleared: {assertion_errors}"
            )

        finally:
            _task_states.pop(task, None)
            loop.set_exception_handler(old_handler)


class TestPatchedDeliverCancellation:
    """Test the actual _patch_anyio_deliver_cancellation function."""

    @pytest.fixture(autouse=True)
    def _save_and_restore_deliver(self):
        """Save and restore CancelScope._deliver_cancellation around each test."""
        original = CancelScope._deliver_cancellation
        yield
        CancelScope._deliver_cancellation = original

    @pytest.mark.asyncio
    async def test_patch_does_not_clear_tasks(self):
        """After timeout, _tasks must NOT be cleared (KNCK-39169 fix)."""
        from agent_leasing.server import _patch_anyio_deliver_cancellation

        _patch_anyio_deliver_cancellation()
        patched = CancelScope._deliver_cancellation

        release = asyncio.Event()

        async def worker():
            await release.wait()

        scope = CancelScope()
        task = asyncio.create_task(worker())
        _task_states[task] = _make_task_state(scope)
        scope._tasks.add(task)

        try:
            # Fast-forward past the 5s timeout
            scope._deliver_start = time.monotonic() - 6.0
            patched(scope, scope)

            # _tasks must NOT be cleared
            assert task in scope._tasks, "Patch must NOT clear _tasks — that causes KNCK-39169"

        finally:
            release.set()
            await asyncio.sleep(0.01)
            _task_states.pop(task, None)

    @pytest.mark.asyncio
    async def test_patch_sets_deliver_stopped_flag(self):
        """After timeout, _deliver_stopped flag prevents re-entry."""
        from agent_leasing.server import _patch_anyio_deliver_cancellation

        _patch_anyio_deliver_cancellation()
        patched = CancelScope._deliver_cancellation

        scope = CancelScope()

        scope._deliver_start = time.monotonic() - 6.0
        patched(scope, scope)

        assert getattr(scope, "_deliver_stopped", False) is True
        # Subsequent calls are no-ops
        result = patched(scope, scope)
        assert result is False

    @pytest.mark.asyncio
    async def test_patch_stops_cpu_spin(self):
        """After timeout, _cancel_handle is None so no more call_soon retries."""
        from agent_leasing.server import _patch_anyio_deliver_cancellation

        _patch_anyio_deliver_cancellation()
        patched = CancelScope._deliver_cancellation

        scope = CancelScope()
        scope._cancel_handle = "something"

        scope._deliver_start = time.monotonic() - 6.0
        patched(scope, scope)

        assert scope._cancel_handle is None

    @pytest.mark.asyncio
    async def test_patch_allows_normal_cancellation(self):
        """Before timeout, cancellation works normally and doesn't set the stop flag."""
        from agent_leasing.server import _patch_anyio_deliver_cancellation

        _patch_anyio_deliver_cancellation()
        patched = CancelScope._deliver_cancellation

        scope = CancelScope()
        patched(scope, scope)
        # Normal cancellation (no stuck tasks) should NOT set the stop flag
        assert getattr(scope, "_deliver_stopped", False) is False

    @pytest.mark.asyncio
    async def test_fixed_patch_no_assertion_on_late_task_completion(self):
        """End-to-end: fixed patch stops CPU spin without assertion errors."""
        from agent_leasing.server import _patch_anyio_deliver_cancellation

        _patch_anyio_deliver_cancellation()
        patched = CancelScope._deliver_cancellation

        errors: list[BaseException] = []

        def capture_handler(loop, context):
            exc = context.get("exception")
            if exc:
                errors.append(exc)

        loop = asyncio.get_event_loop()
        old_handler = loop.get_exception_handler()
        loop.set_exception_handler(capture_handler)

        try:
            release = asyncio.Event()

            async def worker():
                await release.wait()

            scope = CancelScope()
            task = asyncio.create_task(worker())
            _task_states[task] = _make_task_state(scope)
            scope._tasks.add(task)
            task.add_done_callback(_anyio_task_done_callback)

            # Fire the timeout
            scope._deliver_start = time.monotonic() - 6.0
            patched(scope, scope)

            # Task completes — task_done should succeed (task is still in _tasks)
            release.set()
            await asyncio.sleep(0.05)

            assertion_errors = [e for e in errors if isinstance(e, AssertionError)]
            assert len(assertion_errors) == 0, f"Fixed patch should NOT cause assertion errors: {assertion_errors}"

        finally:
            _task_states.pop(task, None)
            loop.set_exception_handler(old_handler)

    @pytest.mark.asyncio
    async def test_flag_off_falls_back_to_clear_tasks(self):
        """When anyio_patch_preserve_tasks_enabled=False, old _tasks.clear() behavior is used."""
        from unittest.mock import patch

        with patch("agent_leasing.server.settings") as mock_settings:
            mock_settings.anyio_patch_preserve_tasks_enabled = False
            from agent_leasing.server import _patch_anyio_deliver_cancellation

            _patch_anyio_deliver_cancellation()
            patched = CancelScope._deliver_cancellation

        scope = CancelScope()
        task = asyncio.ensure_future(asyncio.sleep(999))
        scope._tasks.add(task)

        scope._deliver_start = time.monotonic() - 6.0
        patched(scope, scope)

        # Flag off → old behavior: _tasks cleared, no _deliver_stopped flag
        assert task not in scope._tasks, "Flag off should clear _tasks (old behavior)"
        assert not getattr(scope, "_deliver_stopped", False), "Flag off should NOT set _deliver_stopped"

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
