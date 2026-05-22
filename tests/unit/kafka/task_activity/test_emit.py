"""Tests for the slim TaskActivityEvent bridge.

The bridge is intentionally tool-agnostic. Most tests assert plumbing —
factory looks up extractors and threads kwargs through; `publish_task_activity`
short-circuits on flag/None/exception. One integration test runs
through the real `MCP_EXTRACTORS` registry to lock in the on-wire shape.

Activity-specific behaviour (SR portal link, MCP arg name resolution)
is covered by per-extractor tests next to each extractor module.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from mcp.types import CallToolResult, TextContent

from agent_leasing.kafka.task_activity.emit import (
    publish_task_activity,
    task_activity_post_processor,
)
from agent_leasing.kafka.task_activity.extractors import MCP_EXTRACTORS


@pytest.fixture(autouse=True)
def _enable_task_activity_flag():
    """Default to flag-on so tests exercise the full path. The one explicit
    flag-off test overrides locally."""
    with patch("agent_leasing.kafka.task_activity.emit.settings") as s:
        s.task_activity_event_publishing_enabled = True
        yield s


class TestPublishTaskActivity:
    def test_runs_extractor_with_context_and_caller_kwargs(self, make_session):
        extractor = MagicMock(return_value=[{"event": 1}, {"event": 2}])
        context = make_session()
        with patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget") as mock_pub:
            publish_task_activity(extractor, "tool_output", context, user_request="X")
            extractor.assert_called_once_with("tool_output", context=context, user_request="X")
            assert mock_pub.call_count == 2

    def test_no_op_when_flag_disabled(self, _enable_task_activity_flag, make_session):
        _enable_task_activity_flag.task_activity_event_publishing_enabled = False
        extractor = MagicMock(return_value=[{"event": 1}])
        context = make_session()
        with patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget") as mock_pub:
            publish_task_activity(extractor, None, context)
            extractor.assert_not_called()
            mock_pub.assert_not_called()

    def test_no_op_when_extractor_is_none(self, make_session):
        with patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget") as mock_pub:
            publish_task_activity(None, None, make_session())
            mock_pub.assert_not_called()

    def test_swallows_extractor_exception(self, make_session):
        boom = MagicMock(side_effect=RuntimeError("nope"))
        boom.__name__ = "boom"
        with patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget") as mock_pub:
            publish_task_activity(boom, None, make_session())
            mock_pub.assert_not_called()

    def test_skips_publish_when_chat_session_id_missing(self, make_session):
        # `derive_conversation_key` raises rather than fabricating an orphan
        # task_id; `publish_task_activity` swallows extractor exceptions, so
        # the bridge silently skips the publish.
        from agent_leasing.kafka.task_activity.extractors import extract_sr_created_events

        keyless_session = make_session(channel="CHAT", chat_session_id=None, thread_id=None)
        sr_response = {
            "service_request_numbers": [{"sr_id": "X", "priority_number": "3", "priority_name": "Routine"}],
            "action_taken": "service_request_created",
        }
        with patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget") as mock_pub:
            publish_task_activity(extract_sr_created_events, sr_response, keyless_session)
            mock_pub.assert_not_called()


class TestTaskActivityPostProcessor:
    def test_factory_raises_when_no_extractor_registered(self):
        with pytest.raises(ValueError, match="No task-activity extractor"):
            task_activity_post_processor("nonexistent_tool")

    def test_post_processor_is_no_op_on_isError(self, make_session):
        post = task_activity_post_processor("create_service_request")
        result = CallToolResult(content=[TextContent(text="{}", type="text")], isError=True)
        with patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget") as mock_pub:
            assert post(result, context=make_session()) is result
            mock_pub.assert_not_called()

    def test_post_processor_is_no_op_when_content_empty(self, make_session):
        post = task_activity_post_processor("create_service_request")
        result = CallToolResult(content=[], isError=False)
        with patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget") as mock_pub:
            assert post(result, context=make_session()) is result
            mock_pub.assert_not_called()

    def test_post_processor_skips_on_unparseable_json(self, make_session):
        post = task_activity_post_processor("create_service_request")
        result = CallToolResult(content=[TextContent(text="not json", type="text")], isError=False)
        with patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget") as mock_pub:
            assert post(result, context=make_session()) is result
            mock_pub.assert_not_called()

    def test_post_processor_invokes_registered_extractor_with_context_and_arguments(self, make_session):
        # Bridge plumbing: factory pulls the extractor from the registry,
        # threads tool_output + context + raw arguments dict through.
        spy = MagicMock(return_value=[{"event": "ok"}])
        with (
            patch.dict(MCP_EXTRACTORS, {"create_service_request": spy}),
            patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget") as mock_pub,
        ):
            output = {"action_taken": "service_request_created"}
            result = CallToolResult(content=[TextContent(text=json.dumps(output), type="text")], isError=False)
            session = make_session()
            post = task_activity_post_processor("create_service_request")
            post(result, context=session, arguments={"chat_summary": "Pantry light is out"})

            spy.assert_called_once_with(
                output,
                context=session,
                mcp_arguments={"chat_summary": "Pantry light is out"},
            )
            mock_pub.assert_called_once()

    def test_post_processor_passes_empty_arguments_when_caller_omits_them(self, make_session):
        spy = MagicMock(return_value=[])
        with (
            patch.dict(MCP_EXTRACTORS, {"create_service_request": spy}),
            patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget"),
        ):
            output = {"action_taken": "service_request_created"}
            result = CallToolResult(content=[TextContent(text=json.dumps(output), type="text")], isError=False)
            session = make_session()
            post = task_activity_post_processor("create_service_request")
            post(result, context=session)
            spy.assert_called_once_with(output, context=session, mcp_arguments={})

    def test_post_processor_skipped_when_flag_disabled(self, _enable_task_activity_flag, make_session):
        _enable_task_activity_flag.task_activity_event_publishing_enabled = False
        post = task_activity_post_processor("create_service_request")
        output = {"action_taken": "service_request_created"}
        result = CallToolResult(content=[TextContent(text=json.dumps(output), type="text")], isError=False)
        with patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget") as mock_pub:
            assert post(result, context=make_session()) is result
            mock_pub.assert_not_called()

    def test_post_processor_no_op_when_context_missing(self):
        post = task_activity_post_processor("create_service_request")
        output = {"action_taken": "service_request_created"}
        result = CallToolResult(content=[TextContent(text=json.dumps(output), type="text")], isError=False)
        with patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget") as mock_pub:
            assert post(result) is result
            mock_pub.assert_not_called()


class TestPostProcessorIntegration:
    """End-to-end: post-processor → real `MCP_EXTRACTORS` lookup → real
    `extract_sr_created_events` → published event. Locks in the on-wire
    shape so a future refactor that breaks `arguments["chat_summary"]`
    plumbing or the SR portal-link derivation gets caught here.
    """

    def test_real_extractor_publishes_event_with_expected_shape(self, make_session):
        output = {
            "service_request_numbers": [{"sr_id": "100-1", "priority_number": "3", "priority_name": "Routine"}],
            "action_taken": "service_request_created",
        }
        result = CallToolResult(content=[TextContent(text=json.dumps(output), type="text")], isError=False)
        session = make_session(channel="CHAT", chat_session_id="cs-1")
        post = task_activity_post_processor("create_service_request")

        with patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget") as mock_pub:
            post(result, context=session, arguments={"chat_summary": "Pantry light is out"})

            assert mock_pub.call_count == 1
            event = mock_pub.call_args.args[1]
            # Activity shape: chat_summary flowed through to detail.
            assert event["activity"]["summary"] == "Create SR - Non-Emergency"
            assert event["activity"]["detail"] == "Created Routine SR 100-1 for: Pantry light is out"
            # Extras: user_request + SR portal link both populated.
            assert event["extra"]["user_request"] == "Pantry light is out"
            assert event["extra"]["loft_living_link"] == "https://example.loftliving.com/portal/mr"
            assert event["extra"]["sr_number"] == "100-1"
            assert event["extra"]["channel"] == "CHAT"
            # Resident is in references, not extras.
            assert "knock_resident_id" not in event["extra"]
            resident_ref = next(r for r in event["references"] if r["type"] == "RESIDENT")
            assert resident_ref["id"] == "r-3"


class TestResponderOutputExtractorIntegration:
    """End-to-end: `publish_task_activity` → real `extract_qna_events` /
    `extract_frustrated_user_events` → published event. Locks the on-wire
    shape for the responder-output-driven activities (Q&A and
    FRUSTRATED_USER).
    """

    def test_qna_answered_event_published(self, make_session):
        from agent_leasing.kafka.task_activity.extractors import extract_qna_events

        session = make_session(channel="CHAT")
        with patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget") as mock_pub:
            publish_task_activity(
                extract_qna_events,
                ["qna_flow"],
                session,
                qna_topics=["AMENITIES_AND_FACILITIES.POOL"],
                user_message="Is the pool open?",
            )

            assert mock_pub.call_count == 1
            event = mock_pub.call_args.args[1]
            assert event["activity"]["summary"] == "Property Q&A - Answered"
            assert event["activity"]["detail"] == "Answered Q&A on: AMENITIES_AND_FACILITIES.POOL"
            assert event["extra"]["qna_answered"] == "true"
            assert event["extra"]["qna_topics"] == "AMENITIES_AND_FACILITIES.POOL"
            assert event["extra"]["user_message"] == "Is the pool open?"
            assert event["extra"]["channel"] == "CHAT"
            resident_ref = next(r for r in event["references"] if r["type"] == "RESIDENT")
            assert resident_ref["id"] == "r-3"

    def test_qna_unanswered_event_published_when_handoff_also_set(self, make_session):
        from agent_leasing.kafka.task_activity.extractors import extract_qna_events

        session = make_session(channel="SMS")
        with patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget") as mock_pub:
            publish_task_activity(
                extract_qna_events,
                ["qna_flow", "handoff_to_human_flow"],
                session,
                qna_topics=["LEASING.MOVE_OUT"],
                user_message="when is move-out?",
            )

            assert mock_pub.call_count == 1
            event = mock_pub.call_args.args[1]
            assert event["activity"]["summary"] == "Property Q&A - Unanswered"
            assert event["extra"]["qna_answered"] == "false"

    def test_qna_no_event_when_qna_flow_absent(self, make_session):
        from agent_leasing.kafka.task_activity.extractors import extract_qna_events

        session = make_session()
        with patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget") as mock_pub:
            publish_task_activity(
                extract_qna_events,
                ["facilities_flow"],
                session,
                qna_topics=[],
                user_message="my sink is leaking",
            )
            assert mock_pub.call_count == 0

    def test_frustrated_user_event_published_on_first_true(self, make_session):
        from agent_leasing.kafka.task_activity.extractors import extract_frustrated_user_events

        session = make_session(channel="CHAT")
        with patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget") as mock_pub:
            publish_task_activity(
                extract_frustrated_user_events,
                True,
                session,
                user_message="get me a manager",
            )

            assert mock_pub.call_count == 1
            event = mock_pub.call_args.args[1]
            assert event["activity"]["summary"] == "Frustrated User"
            assert event["activity"]["detail"] == "Resident frustration detected: get me a manager"
            assert event["extra"]["user_message"] == "get me a manager"
            # Dedup is delivery-time — the extractor itself does not flip the
            # flag. The caller (server.py / realtime_util.py) wires an
            # on_success callback that flips it after a confirmed publish.
            assert session.frustrated_user_emitted is False

    def test_frustrated_user_dedup_suppresses_second_emit(self, make_session):
        from agent_leasing.kafka.task_activity.extractors import extract_frustrated_user_events

        session = make_session()
        with patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget") as mock_pub:
            publish_task_activity(extract_frustrated_user_events, True, session, user_message="first")
            # Simulate a confirmed publish: the on_success callback would
            # have flipped this flag once delivery completed.
            session.frustrated_user_emitted = True
            publish_task_activity(extract_frustrated_user_events, True, session, user_message="second")
            assert mock_pub.call_count == 1

    def test_frustrated_user_no_event_when_false(self, make_session):
        from agent_leasing.kafka.task_activity.extractors import extract_frustrated_user_events

        session = make_session()
        with patch("agent_leasing.kafka.task_activity.emit.publish_task_activity_fire_and_forget") as mock_pub:
            publish_task_activity(
                extract_frustrated_user_events,
                False,
                session,
                user_message="x",
            )
            assert mock_pub.call_count == 0
            assert session.frustrated_user_emitted is False
