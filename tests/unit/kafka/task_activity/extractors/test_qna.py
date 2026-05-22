import pytest

from agent_leasing.api.model import UCReference
from agent_leasing.kafka.task_activity.extractors.qna import (
    ACTIVITY_SUMMARY_QNA,
    QnAFacts,
    build_qna_event,
    extract_qna_events,
    parse_qna_facts,
)


class TestParseQnAFacts:
    def test_qna_only_returns_answered_true(self):
        facts = parse_qna_facts(
            workflow_codes=["qna_flow"],
            qna_topics=["AMENITIES_AND_FACILITIES.POOL"],
            user_message="Is the pool open?",
        )
        assert facts == [
            QnAFacts(
                answered=True,
                qna_topics=("AMENITIES_AND_FACILITIES.POOL",),
                user_message="Is the pool open?",
            )
        ]

    def test_qna_with_handoff_returns_answered_false(self):
        # Verification-step turn: agent has decided to hand off but is
        # still asking the resident to confirm. Both flows fire together.
        facts = parse_qna_facts(
            workflow_codes=["qna_flow", "handoff_to_human_flow"],
            qna_topics=["LEASING.MOVE_OUT"],
            user_message="When is move-out?",
        )
        assert facts[0].answered is False

    def test_no_qna_flow_returns_empty(self):
        # No emit when qna_flow is missing — even if topics were set.
        assert parse_qna_facts(["facilities_flow"], ["LEASING.OTHER"], "x") == []

    def test_empty_workflow_codes_returns_empty(self):
        assert parse_qna_facts([], ["X"], "x") == []
        assert parse_qna_facts(None, ["X"], "x") == []

    def test_empty_topics_allowed(self):
        # Topic classification missing is not a reason to skip the event.
        facts = parse_qna_facts(["qna_flow"], [], "x")
        assert facts[0].qna_topics == ()

    def test_none_topics_allowed(self):
        facts = parse_qna_facts(["qna_flow"], None, "x")
        assert facts[0].qna_topics == ()

    def test_drops_non_string_and_empty_topics(self):
        facts = parse_qna_facts(
            ["qna_flow"],
            ["LEASING.MOVE_OUT", "", None, 5, "PARKING.GARAGE"],
            "x",
        )
        assert facts[0].qna_topics == ("LEASING.MOVE_OUT", "PARKING.GARAGE")

    def test_empty_user_message_normalized_to_none(self):
        facts = parse_qna_facts(["qna_flow"], [], "")
        assert facts[0].user_message is None

    def test_none_user_message_kept_as_none(self):
        facts = parse_qna_facts(["qna_flow"], [], None)
        assert facts[0].user_message is None


class TestBuildQnAEvent:
    @pytest.fixture
    def kwargs(self):
        return dict(
            task_id="task-uuid",
            channel="CHAT",
            knock_company_id="c-1",
            knock_property_id="p-2",
            knock_resident_id="r-3",
            first_name="Alex",
            last_name="Smith",
            ab_unit_number="204",
            ab_building_number="B",
            chat_session_id="cs-1",
        )

    def test_summary_answered(self, kwargs):
        facts = QnAFacts(answered=True, qna_topics=("AMENITIES_AND_FACILITIES.POOL",), user_message="x")
        event = build_qna_event(facts, **kwargs)
        assert event["activity"]["summary"] == f"{ACTIVITY_SUMMARY_QNA} - Answered"

    def test_summary_unanswered(self, kwargs):
        facts = QnAFacts(answered=False, qna_topics=(), user_message="x")
        event = build_qna_event(facts, **kwargs)
        assert event["activity"]["summary"] == f"{ACTIVITY_SUMMARY_QNA} - Unanswered"

    def test_detail_includes_topics_when_present(self, kwargs):
        facts = QnAFacts(
            answered=True,
            qna_topics=("AMENITIES_AND_FACILITIES.POOL", "PARKING.GARAGE"),
            user_message="x",
        )
        event = build_qna_event(facts, **kwargs)
        assert event["activity"]["detail"] == "Answered Q&A on: AMENITIES_AND_FACILITIES.POOL, PARKING.GARAGE"

    def test_detail_omits_topics_when_empty(self, kwargs):
        facts = QnAFacts(answered=True, qna_topics=(), user_message="x")
        event = build_qna_event(facts, **kwargs)
        assert event["activity"]["detail"] == "Answered Q&A"

    def test_extras_include_answered_flag(self, kwargs):
        facts = QnAFacts(answered=False, qna_topics=(), user_message="x")
        event = build_qna_event(facts, **kwargs)
        # extras is a map<string,string> in the Avro schema — bool is flattened.
        assert event["extra"]["qna_answered"] == "false"

    def test_extras_answered_true_serialized_as_string(self, kwargs):
        facts = QnAFacts(answered=True, qna_topics=(), user_message="x")
        event = build_qna_event(facts, **kwargs)
        assert event["extra"]["qna_answered"] == "true"

    def test_extras_include_topics_list(self, kwargs):
        facts = QnAFacts(answered=True, qna_topics=("PARKING.GARAGE",), user_message="x")
        event = build_qna_event(facts, **kwargs)
        assert event["extra"]["qna_topics"] == "PARKING.GARAGE"

    def test_extras_topics_join_multiple_with_comma(self, kwargs):
        facts = QnAFacts(
            answered=True,
            qna_topics=("LEASING.MOVE_OUT", "PARKING.GARAGE"),
            user_message="x",
        )
        event = build_qna_event(facts, **kwargs)
        assert event["extra"]["qna_topics"] == "LEASING.MOVE_OUT,PARKING.GARAGE"

    def test_extras_omit_topics_when_empty(self, kwargs):
        facts = QnAFacts(answered=True, qna_topics=(), user_message="x")
        event = build_qna_event(facts, **kwargs)
        assert "qna_topics" not in event["extra"]

    def test_extras_include_user_message(self, kwargs):
        facts = QnAFacts(answered=True, qna_topics=(), user_message="Is the pool open?")
        event = build_qna_event(facts, **kwargs)
        assert event["extra"]["user_message"] == "Is the pool open?"

    def test_extras_omit_user_message_when_missing(self, kwargs):
        facts = QnAFacts(answered=True, qna_topics=(), user_message=None)
        event = build_qna_event(facts, **kwargs)
        assert "user_message" not in event["extra"]

    def test_references_seeded_with_company_property_resident(self, kwargs):
        facts = QnAFacts(answered=True, qna_topics=(), user_message="x")
        event = build_qna_event(facts, **kwargs)
        types = [r["type"] for r in event["references"]]
        assert types == ["COMPANY", "PROPERTY", "RESIDENT"]
        resident_ref = next(r for r in event["references"] if r["type"] == "RESIDENT")
        assert resident_ref["id"] == "r-3"


class TestExtractQnAEvents:
    def test_emits_one_event_for_qna_turn(self, make_session):
        events = extract_qna_events(
            ["qna_flow"],
            context=make_session(),
            qna_topics=["AMENITIES_AND_FACILITIES.POOL"],
            user_message="Is the pool open?",
        )
        assert len(events) == 1
        assert events[0]["activity"]["summary"] == "Property Q&A - Answered"
        assert events[0]["extra"]["qna_topics"] == "AMENITIES_AND_FACILITIES.POOL"

    def test_emits_unanswered_when_handoff_present(self, make_session):
        events = extract_qna_events(
            ["qna_flow", "handoff_to_human_flow"],
            context=make_session(),
            qna_topics=["LEASING.MOVE_OUT"],
            user_message="When is move-out?",
        )
        assert events[0]["activity"]["summary"] == "Property Q&A - Unanswered"
        assert events[0]["extra"]["qna_answered"] == "false"

    def test_no_emit_without_qna_flow(self, make_session):
        events = extract_qna_events(
            ["facilities_flow"],
            context=make_session(),
            qna_topics=["X"],
            user_message="x",
        )
        assert events == []

    def test_no_emit_when_workflow_codes_empty(self, make_session):
        assert extract_qna_events([], context=make_session()) == []
        assert extract_qna_events(None, context=make_session()) == []

    def test_common_context_pulled_from_session(self, make_session):
        events = extract_qna_events(
            ["qna_flow"],
            context=make_session(),
            qna_topics=[],
            user_message="x",
        )
        extra = events[0]["extra"]
        assert extra["chat_session_id"] == "cs-1"
        assert extra["channel"] == "CHAT"
        # Resident is in references (id "r-3"), not extras.
        assert "knock_resident_id" not in extra
        resident_ref = next(r for r in events[0]["references"] if r["type"] == "RESIDENT")
        assert resident_ref["id"] == "r-3"

    def test_voice_session_includes_call_sid(self, make_session):
        events = extract_qna_events(
            ["qna_flow"],
            context=make_session(channel="VOICE"),
            qna_topics=[],
            user_message="x",
        )
        assert events[0]["extra"]["call_sid"] == "CA-1"

    def test_extras_include_os_and_resident_ids_from_uc_refs(self, make_session):
        session = make_session(
            uc_company_id=UCReference(id="7661634", source="OS"),
            uc_property_id=UCReference(id="7661666", source="OS"),
            uc_resident_household_id=UCReference(id="136", source="OS"),
            uc_resident_member_id=UCReference(id="137", source="OS"),
        )
        events = extract_qna_events(
            ["qna_flow"],
            context=session,
            qna_topics=[],
            user_message="x",
        )
        extra = events[0]["extra"]
        assert extra["os_company_id"] == "7661634"
        assert extra["os_property_id"] == "7661666"
        assert extra["resident_household_id"] == "136"
        assert extra["resident_member_id"] == "137"

    def test_extras_omit_os_and_resident_ids_when_uc_refs_missing(self, make_session):
        events = extract_qna_events(
            ["qna_flow"],
            context=make_session(),
            qna_topics=[],
            user_message="x",
        )
        extra = events[0]["extra"]
        assert "os_company_id" not in extra
        assert "os_property_id" not in extra
        assert "resident_household_id" not in extra
        assert "resident_member_id" not in extra
