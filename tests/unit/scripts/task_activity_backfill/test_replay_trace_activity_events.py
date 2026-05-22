"""Unit tests for the channel-aware backfill task.id derivation.

Guards against the mixed-channel collapse bug — when a single LangSmith
thread contains runs from both SMS and EMAIL (real scenario: Redis cache
key is channel-blind on chat_session_id), the backfill must emit one
task.id per (channel, thread), not one per thread.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import replay_trace_activity_events as replay

THREAD_ID = "T-test"


def _ctx_with_product(product: str) -> dict:
    SYNTH = {"id": "synthesized", "source": "synthesized"}
    return {
        "thread_id": THREAD_ID,
        "ask_request": {
            "product": product,
            "chat_session_id": "cs-1",
            "product_info": {
                "knock_property_id": "prop-1",
                "knock_resident_id": "res-1",
                "uc_company_id": SYNTH,
                "uc_property_id": SYNTH,
                "uc_resident_household_id": SYNTH,
                "uc_resident_member_id": SYNTH,
                "ab_resident_id": SYNTH,
                "uc_lease_id": SYNTH,
                "uc_portal_base_url": "https://example.test",
            },
        },
        "session_marker": "marker-1",
    }


def _handoff_run(product: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"run-{product}",
        run_type="tool",
        name="transfer_to_staff_text",
        inputs={
            "ctx": {"context": _ctx_with_product(product)},
            "transfer_message": "test",
            "reason": "RESIDENT_REQUESTED",
        },
        outputs={},
        extra={"metadata": {"product": product}},
        parent_run_id=None,
        parent_run_ids=[],
        trace_id=f"trace-{product}",
        start_time=None,
    )


class TestComputeBackfillTaskId:
    def test_includes_channel_in_hash(self):
        """Same thread + different channel ⇒ different task.id (the headline guarantee)."""
        sms_id = replay.compute_backfill_task_id(THREAD_ID, "SMS")
        email_id = replay.compute_backfill_task_id(THREAD_ID, "EMAIL")
        assert sms_id != email_id

    def test_deterministic(self):
        assert replay.compute_backfill_task_id(THREAD_ID, "SMS") == replay.compute_backfill_task_id(THREAD_ID, "SMS")

    def test_rejects_unknown_channel(self):
        with pytest.raises(ValueError, match="channel must be one of"):
            replay.compute_backfill_task_id(THREAD_ID, "TELEGRAM")


class TestDeriveRunChannel:
    """Three-tier resolution: inputs.ctx → metadata.product → fallback."""

    def test_prefers_inputs_ctx_over_metadata(self):
        # ctx says EMAIL, metadata says SMS — ctx wins
        run = SimpleNamespace(
            inputs={"ctx": {"context": _ctx_with_product("resident_one_email")}},
            extra={"metadata": {"product": "resident_one_sms"}},
        )
        assert replay.derive_run_channel(run, fallback_ctx=None) == "EMAIL"

    def test_falls_back_to_metadata_when_no_ctx(self):
        run = SimpleNamespace(inputs={}, extra={"metadata": {"product": "resident_one_sms"}})
        assert replay.derive_run_channel(run, fallback_ctx=None) == "SMS"

    def test_falls_back_to_fallback_ctx_when_no_metadata(self):
        run = SimpleNamespace(inputs={}, extra={"metadata": {}})
        assert replay.derive_run_channel(run, fallback_ctx=_ctx_with_product("resident_one_chat")) == "CHAT"

    def test_voice_product_resolves(self):
        run = SimpleNamespace(inputs={}, extra={"metadata": {"product": "resident_one_voice"}})
        assert replay.derive_run_channel(run, fallback_ctx=None) == "VOICE"

    def test_raises_when_no_source_yields_channel(self):
        run = SimpleNamespace(inputs={}, extra={"metadata": {}})
        with pytest.raises(ValueError, match="no product found"):
            replay.derive_run_channel(run, fallback_ctx=None)


class TestRewriteTaskId:
    def _make_event(self, prev_channel: str | None = None) -> dict:
        return {
            "task": {"id": "stale-uuid"},
            "extra": ({"channel": prev_channel} if prev_channel else {}),
        }

    def test_overwrites_task_id(self):
        events = [self._make_event()]
        replay.rewrite_task_id(events, THREAD_ID, "SMS")
        assert events[0]["task"]["id"] == replay.compute_backfill_task_id(THREAD_ID, "SMS")

    def test_overwrites_extra_channel(self):
        """Critical: extra.channel from extractor may reflect the thread-level
        fallback ctx, not the run that owns this event. rewrite_task_id must
        stamp the run's actual channel."""
        events = [self._make_event(prev_channel="SMS")]
        replay.rewrite_task_id(events, THREAD_ID, "EMAIL")
        assert events[0]["extra"]["channel"] == "EMAIL"

    def test_creates_extra_when_missing(self):
        event = {"task": {"id": "stale"}}
        replay.rewrite_task_id([event], THREAD_ID, "SMS")
        assert event["extra"]["channel"] == "SMS"


def test_mixed_channel_thread_produces_two_task_ids():
    """Regression test for 1547 — the bug this fix exists for.

    Two synthetic handoff runs from different channels share one LangSmith
    thread. End-to-end (resolve_thread_ctx → replay_handoff →
    derive_run_channel → rewrite_task_id) must produce per-channel task.ids
    and per-event extra.channel labels matching each run's product.
    """
    runs = [_handoff_run("resident_one_sms"), _handoff_run("resident_one_email")]
    fallback_ctx, source = replay.resolve_thread_ctx(runs, THREAD_ID)
    assert source == "borrowed"

    all_events: list[tuple[str, dict]] = []
    for run in runs:
        ev_list = replay.replay_handoff(run.name, run.inputs, fallback_ctx)
        run_channel = replay.derive_run_channel(run, fallback_ctx)
        replay.rewrite_task_id(ev_list, THREAD_ID, run_channel)
        for ev in ev_list:
            all_events.append((run.extra["metadata"]["product"], ev))

    assert len(all_events) == 2
    task_ids = {ev["task"]["id"] for _, ev in all_events}
    channels = {ev["extra"]["channel"] for _, ev in all_events}
    assert len(task_ids) == 2, f"expected 2 distinct task.ids, got {task_ids}"
    assert channels == {"SMS", "EMAIL"}

    by_product = dict(all_events)
    assert by_product["resident_one_sms"]["extra"]["channel"] == "SMS"
    assert by_product["resident_one_email"]["extra"]["channel"] == "EMAIL"
    assert by_product["resident_one_sms"]["task"]["id"] == replay.compute_backfill_task_id(THREAD_ID, "SMS")
    assert by_product["resident_one_email"]["task"]["id"] == replay.compute_backfill_task_id(THREAD_ID, "EMAIL")
