import pytest

from agent_leasing.kafka.task_activity.extractors.frustrated_user import (
    ACTIVITY_SUMMARY_FRUSTRATED_USER,
    FrustratedUserFacts,
    build_frustrated_user_event,
    extract_frustrated_user_events,
    parse_frustrated_user_facts,
)


class TestParseFrustratedUserFacts:
    def test_first_true_emits(self):
        facts = parse_frustrated_user_facts(
            user_frustrated=True,
            user_message="get me a manager",
            already_emitted=False,
        )
        assert facts == [FrustratedUserFacts(user_message="get me a manager")]

    def test_false_returns_empty(self):
        assert (
            parse_frustrated_user_facts(
                user_frustrated=False,
                user_message="x",
                already_emitted=False,
            )
            == []
        )

    def test_already_emitted_suppresses(self):
        # Once-per-conversation gate — even if frustration repeats, no new emit.
        assert (
            parse_frustrated_user_facts(
                user_frustrated=True,
                user_message="still angry",
                already_emitted=True,
            )
            == []
        )

    def test_empty_user_message_normalized_to_none(self):
        facts = parse_frustrated_user_facts(
            user_frustrated=True,
            user_message="",
            already_emitted=False,
        )
        assert facts[0].user_message is None

    def test_none_user_message_kept_as_none(self):
        facts = parse_frustrated_user_facts(
            user_frustrated=True,
            user_message=None,
            already_emitted=False,
        )
        assert facts[0].user_message is None


class TestBuildFrustratedUserEvent:
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

    def test_summary(self, kwargs):
        facts = FrustratedUserFacts(user_message="x")
        event = build_frustrated_user_event(facts, **kwargs)
        assert event["activity"]["summary"] == ACTIVITY_SUMMARY_FRUSTRATED_USER

    def test_detail_includes_user_message(self, kwargs):
        facts = FrustratedUserFacts(user_message="get me a manager")
        event = build_frustrated_user_event(facts, **kwargs)
        assert event["activity"]["detail"] == "Resident frustration detected: get me a manager"

    def test_detail_omits_message_when_missing(self, kwargs):
        facts = FrustratedUserFacts(user_message=None)
        event = build_frustrated_user_event(facts, **kwargs)
        assert event["activity"]["detail"] == "Resident frustration detected"

    def test_extras_include_user_message(self, kwargs):
        facts = FrustratedUserFacts(user_message="get me a manager")
        event = build_frustrated_user_event(facts, **kwargs)
        assert event["extra"]["user_message"] == "get me a manager"

    def test_extras_omit_user_message_when_missing(self, kwargs):
        facts = FrustratedUserFacts(user_message=None)
        event = build_frustrated_user_event(facts, **kwargs)
        assert "user_message" not in event["extra"]

    def test_references_seeded_with_company_property_resident(self, kwargs):
        facts = FrustratedUserFacts(user_message="x")
        event = build_frustrated_user_event(facts, **kwargs)
        types = [r["type"] for r in event["references"]]
        assert types == ["COMPANY", "PROPERTY", "RESIDENT"]


class TestExtractFrustratedUserEvents:
    """Dedup is now delivery-time — the extractor no longer flips the
    flag itself. The publish layer's on_success callback is responsible
    for setting `context.frustrated_user_emitted = True` after a
    confirmed publish (see test_publish_responder_output_activities and
    test_realtime_util for the call-site coverage).
    """

    def test_first_true_emits_event_without_flipping_flag(self, make_session):
        session = make_session()
        # Default attribute on the mock — read returns Mock, falsy is needed.
        session.frustrated_user_emitted = False
        events = extract_frustrated_user_events(
            user_frustrated=True,
            context=session,
            user_message="get me a manager",
        )
        assert len(events) == 1
        assert events[0]["activity"]["summary"] == "Frustrated User"
        # Extractor does NOT flip the flag — that's the caller's on_success job.
        assert session.frustrated_user_emitted is False

    def test_repeat_true_after_emit_is_suppressed(self, make_session):
        session = make_session()
        session.frustrated_user_emitted = True
        events = extract_frustrated_user_events(
            user_frustrated=True,
            context=session,
            user_message="still angry",
        )
        assert events == []
        # Flag stays True (no double-flip noise).
        assert session.frustrated_user_emitted is True

    def test_false_does_not_emit_or_flip_flag(self, make_session):
        session = make_session()
        session.frustrated_user_emitted = False
        events = extract_frustrated_user_events(
            user_frustrated=False,
            context=session,
            user_message="x",
        )
        assert events == []
        assert session.frustrated_user_emitted is False

    def test_voice_session_includes_call_sid(self, make_session):
        session = make_session(channel="VOICE")
        session.frustrated_user_emitted = False
        events = extract_frustrated_user_events(
            user_frustrated=True,
            context=session,
            user_message="x",
        )
        assert events[0]["extra"]["call_sid"] == "CA-1"

    def test_common_context_pulled_from_session(self, make_session):
        session = make_session()
        session.frustrated_user_emitted = False
        events = extract_frustrated_user_events(
            user_frustrated=True,
            context=session,
            user_message="x",
        )
        extra = events[0]["extra"]
        assert extra["chat_session_id"] == "cs-1"
        assert extra["channel"] == "CHAT"
        # Resident is in references (id "r-3"), not extras.
        assert "knock_resident_id" not in extra
