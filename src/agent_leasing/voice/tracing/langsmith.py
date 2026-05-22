"""VoiceTracer — LangSmith tracing for voice calls.

Tracing logic from ``twilio_handler.py`` methods ``trace_messages_to_langsmith`` and ``_post_langsmith_child_run``.

Creates child runs for each conversation message with accurate start/end
timestamps from the PlaybackTracker.  Runs are posted as background tasks
to avoid blocking the audio event loop.
"""

from __future__ import annotations

import asyncio
import datetime
from collections.abc import Mapping
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _log_task_exception(task: asyncio.Task[None]) -> None:
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc:
        logger.warning(f"Trace task failed: {exc}")


class VoiceTracer:
    """Manages LangSmith tracing for a single voice call.

    Reads timestamps from PlaybackTracker, filler item IDs from
    FillerManager, and conversation history from SessionManager.

    Usage::

        tracer = VoiceTracer()
        tracer.fire_trace_task(history, ...)  # non-blocking
        await tracer.finalize(...)            # at cleanup — drain + final pass
    """

    def __init__(self) -> None:
        self._pending_tasks: set[asyncio.Task[None]] = set()
        self._viewed_messages: set[str] = set()

    # ------------------------------------------------------------------
    # Background tracing
    # ------------------------------------------------------------------

    def fire_trace_task(
        self,
        history: list[Mapping[str, Any]],
        root_run: Any,
        message_start_times: dict[str, datetime.datetime],
        message_end_times: dict[str, datetime.datetime],
        filler_item_ids: set[str],
        rendered_system_prompt: str | None = None,
    ) -> None:
        """Launch a background task to trace messages to LangSmith."""
        if not root_run:
            return
        task = asyncio.create_task(
            self._trace_messages(
                history=history,
                root_run=root_run,
                start_times=message_start_times,
                end_times=message_end_times,
                filler_item_ids=filler_item_ids,
                rendered_system_prompt=rendered_system_prompt,
            )
        )
        task.add_done_callback(_log_task_exception)
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    # ------------------------------------------------------------------
    # Finalization (called during cleanup)
    # ------------------------------------------------------------------

    async def finalize(
        self,
        history: list[Mapping[str, Any]],
        root_run: Any,
        message_start_times: dict[str, datetime.datetime],
        message_end_times: dict[str, datetime.datetime],
        filler_item_ids: set[str],
        rendered_system_prompt: str | None = None,
    ) -> None:
        """Drain pending tasks, fill missing end times, then do a final trace pass."""
        # Drain in-flight tasks
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
            self._pending_tasks.clear()

        # Fill missing end times (items that never got a mark confirmation)
        for item_id, start in message_start_times.items():
            if item_id not in message_end_times:
                message_end_times[item_id] = start

        # Final trace pass
        await self._trace_messages(
            history=history,
            root_run=root_run,
            start_times=message_start_times,
            end_times=message_end_times,
            filler_item_ids=filler_item_ids,
            rendered_system_prompt=rendered_system_prompt,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _trace_messages(
        self,
        history: list[Mapping[str, Any]],
        root_run: Any,
        start_times: dict[str, datetime.datetime],
        end_times: dict[str, datetime.datetime],
        filler_item_ids: set[str],
        rendered_system_prompt: str | None = None,
    ) -> None:
        """Post child runs for un-traced messages in the history."""
        if not root_run:
            return

        for message in history:
            item_id = message.get("item_id")
            role = message.get("role")

            if not self._should_trace(item_id, role, end_times):
                continue

            self._viewed_messages.add(item_id)
            await self._post_child_run(
                message=message,
                item_id=item_id,
                role=role,
                root_run=root_run,
                start_times=start_times,
                end_times=end_times,
                filler_item_ids=filler_item_ids,
                rendered_system_prompt=rendered_system_prompt,
            )

    def _should_trace(
        self,
        item_id: str | None,
        role: str | None,
        end_times: dict[str, datetime.datetime],
    ) -> bool:
        """Check if a message should be traced (not yet viewed, has end time if assistant)."""
        if item_id in self._viewed_messages:
            return False
        # Defer assistant messages until playback is confirmed
        if role == "assistant" and item_id not in end_times:
            return False
        return True

    async def _post_child_run(
        self,
        message: Mapping[str, Any],
        item_id: str,
        role: str,
        root_run: Any,
        start_times: dict[str, datetime.datetime],
        end_times: dict[str, datetime.datetime],
        filler_item_ids: set[str],
        rendered_system_prompt: str | None = None,
    ) -> None:
        """Create and post a single LangSmith child run."""
        start_ts = start_times.get(item_id)
        end_ts = end_times.get(item_id)
        start_ts, end_ts = _normalize_times(start_ts, end_ts)
        if end_ts is None:
            end_ts = datetime.datetime.now(datetime.UTC)
        if start_ts is None:
            start_ts = end_ts

        name = "HumanMessage" if role == "user" else "AIMessage" if role == "assistant" else None
        if not name:
            return

        inputs: dict[str, str] = {}
        if name == "AIMessage" and rendered_system_prompt:
            inputs["system_prompt"] = rendered_system_prompt

        is_filler = item_id in filler_item_ids
        extra = {"metadata": {"filler": is_filler}} if name == "AIMessage" else None

        child = root_run.create_child(
            name=name,
            run_type="llm",
            inputs=inputs,
            outputs={"message": message.get("content", "")},
            start_time=start_ts,
            end_time=end_ts,
            extra=extra,
        )
        # child.post() is a blocking HTTP call — offload to a thread
        await asyncio.to_thread(child.post)

    def reset(self) -> None:
        """Reset for recovery or cleanup."""
        self._viewed_messages.clear()
        self._pending_tasks.clear()


def _normalize_times(
    start_ts: datetime.datetime | None,
    end_ts: datetime.datetime | None,
) -> tuple[datetime.datetime | None, datetime.datetime | None]:
    """Ensure timestamps are present and non-decreasing for LangSmith durations."""
    if start_ts is None and end_ts is None:
        return None, None
    if end_ts is None:
        end_ts = datetime.datetime.now(datetime.UTC)
    if start_ts is None:
        start_ts = end_ts
    if start_ts > end_ts:
        start_ts = end_ts
    return start_ts, end_ts
