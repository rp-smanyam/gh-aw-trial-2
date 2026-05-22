"""PlaybackTracker — tracks what audio the caller has actually heard.

Mark-based tracking from ``twilio_handler.py``; decoupled from pacer following PR #598's component separation.

Works with the transport's playback notification mechanism (Twilio marks,
WebRTC drain events) to know when each response has finished playing.
This drives state transitions (agent speaking → stopped) and tracing
(message end timestamps).
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

OnResponseCompleted = Callable[[str], Coroutine[Any, Any, None]]


@dataclass(slots=True)
class MarkData:
    """Data associated with a single playback mark."""

    item_id: str
    content_index: int
    byte_count: int


class PlaybackTracker:
    """Tracks playback progress using marks sent by the ``AudioPacer``.

    The pacer sends a mark after each audio chunk's frames are delivered.
    The transport reports when each mark has been played (via
    ``PlaybackNotifier.on_playback_milestone``).  This class correlates
    them to determine when an entire response has finished playing.

    Lifecycle::

        tracker = PlaybackTracker()
        # Audio received from OpenAI:
        mark_id = pacer.enqueue(chunk)
        tracker.register_mark(mark_id, MarkData(...))

        # Transport reports mark played:
        await tracker.on_mark_played(mark_id)
        # -> calls on_response_completed when the last mark for an item plays
    """

    def __init__(self) -> None:
        self._marks: dict[str, MarkData] = {}
        self._max_marks = 1000

        # Last mark_id for each item_id — updated by the handler when
        # audio is enqueued.  Removes the need to reach into the pacer.
        self._last_mark_for_item: dict[str, str] = {}

        # Timestamps for LangSmith tracing
        self.message_start_times: dict[str, datetime.datetime] = {}
        self.message_end_times: dict[str, datetime.datetime] = {}

        # Callback fired when a response finishes playback
        self.on_response_completed: OnResponseCompleted | None = None

    # ------------------------------------------------------------------
    # Registration (called by the handler when audio is enqueued)
    # ------------------------------------------------------------------

    def register_mark(self, mark_id: str, data: MarkData) -> None:
        """Associate a mark_id (from the pacer) with its audio metadata."""
        # Prevent unbounded growth if the transport stops reporting marks
        if len(self._marks) >= self._max_marks:
            oldest = sorted(self._marks.keys(), key=int)[: self._max_marks // 2]
            for old_id in oldest:
                del self._marks[old_id]
            logger.warning(f"Playback tracker cleared {len(oldest)} stale marks")

        self._marks[mark_id] = data
        # Always update the last-mark pointer for this item
        self._last_mark_for_item[data.item_id] = mark_id

    def record_start_time(self, item_id: str) -> None:
        """Record when the first audio for *item_id* was received."""
        if item_id not in self.message_start_times:
            self.message_start_times[item_id] = datetime.datetime.now(datetime.UTC)

    def has_pending_items(self) -> bool:
        """True if there are items still awaiting playback confirmation."""
        return bool(self._last_mark_for_item)

    def pending_item_ids(self) -> list[str]:
        """Return IDs of items still awaiting playback confirmation."""
        return list(self._last_mark_for_item.keys())

    # ------------------------------------------------------------------
    # Mark playback (called when the transport reports a mark as played)
    # ------------------------------------------------------------------

    async def on_mark_played(self, mark_id: str) -> None:
        """Process a playback milestone from the transport.

        If this mark is the last one for its item, records the end time
        and fires ``on_response_completed``.
        """
        data = self._marks.pop(mark_id, None)
        if data is None:
            return

        # Check if this was the last mark for its item
        if self._last_mark_for_item.get(data.item_id) == mark_id:
            del self._last_mark_for_item[data.item_id]
            self.message_end_times[data.item_id] = datetime.datetime.now(datetime.UTC)

            if self.on_response_completed:
                await self.on_response_completed(data.item_id)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Discard all mark data (barge-in)."""
        self._marks.clear()
        self._last_mark_for_item.clear()

    def reset(self) -> None:
        """Full reset for recovery or cleanup."""
        self._marks.clear()
        self._last_mark_for_item.clear()
        self.message_start_times.clear()
        self.message_end_times.clear()
