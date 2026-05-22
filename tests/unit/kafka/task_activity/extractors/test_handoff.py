import pytest

from agent_leasing.api.model import HandoffReasonCode, HandoffTopic
from agent_leasing.kafka.task_activity.extractors.handoff import (
    ACTIVITY_SUMMARY_HANDOFF,
    HandoffFacts,
    build_handoff_event,
    extract_handoff_events,
    parse_handoff_facts,
)


class TestParseHandoffFacts:
    def test_with_message_link_and_reason(self):
        facts = parse_handoff_facts(
            transfer_message="Pantry light is out and I need a maintenance visit",
            reason=HandoffReasonCode.RESIDENT_REQUESTED,
            handoff_portal_link="https://example.loftliving.com/portal/messenger",
        )
        assert facts == [
            HandoffFacts(
                handoff_message="Pantry light is out and I need a maintenance visit",
                reason=HandoffReasonCode.RESIDENT_REQUESTED,
                handoff_portal_link="https://example.loftliving.com/portal/messenger",
            )
        ]

    def test_message_only_link_and_reason_optional(self):
        facts = parse_handoff_facts(transfer_message="Need help with rent payment")
        assert facts == [
            HandoffFacts(handoff_message="Need help with rent payment", reason=None, handoff_portal_link=None)
        ]

    def test_empty_message_still_records_handoff(self):
        # Voice tool can transfer with no summary (resident refused).
        # The activity stream should still see the handoff event.
        facts = parse_handoff_facts(transfer_message=None)
        assert len(facts) == 1
        assert facts[0].handoff_message is None

    def test_empty_string_message_normalized_to_none(self):
        facts = parse_handoff_facts(transfer_message="")
        assert facts[0].handoff_message is None

    def test_topic_passes_through(self):
        facts = parse_handoff_facts(
            transfer_message="Need late fee waiver",
            reason=HandoffReasonCode.COMPLAINT,
            topic=HandoffTopic.BALANCE_RESOLUTION,
        )
        assert facts[0].topic == HandoffTopic.BALANCE_RESOLUTION

    def test_topic_defaults_to_none(self):
        facts = parse_handoff_facts(transfer_message="anything")
        assert facts[0].topic is None


class TestBuildHandoffEvent:
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

    def test_summary_uses_bare_handoff_when_no_reason(self, kwargs):
        # Defensive fallback when an upstream caller forgets to pass reason.
        facts = HandoffFacts(handoff_message="Billing question")
        event = build_handoff_event(facts, **kwargs)
        assert event["activity"]["summary"] == ACTIVITY_SUMMARY_HANDOFF

    @pytest.mark.parametrize(
        "reason,sub_label",
        [
            (HandoffReasonCode.RESIDENT_REQUESTED, "Resident Requested"),
            (HandoffReasonCode.SYSTEM_ERROR, "System Error"),
            (HandoffReasonCode.EMERGENCY, "Emergency"),
            (HandoffReasonCode.OUT_OF_SCOPE, "Out of Scope"),
            (HandoffReasonCode.MISSING_DATA, "Missing Data"),
            (HandoffReasonCode.ALREADY_IN_HANDOFF, "Already in Handoff"),
            (HandoffReasonCode.COMPLAINT, "Complaint"),
        ],
    )
    def test_summary_sub_label_per_reason(self, kwargs, reason, sub_label):
        facts = HandoffFacts(handoff_message="m", reason=reason)
        event = build_handoff_event(facts, **kwargs)
        assert event["activity"]["summary"] == f"{ACTIVITY_SUMMARY_HANDOFF} - {sub_label}"

    def test_extras_carry_handoff_reason_string(self, kwargs):
        facts = HandoffFacts(handoff_message="m", reason=HandoffReasonCode.SYSTEM_ERROR)
        event = build_handoff_event(facts, **kwargs)
        assert event["extra"]["handoff_reason"] == "SYSTEM_ERROR"

    def test_extras_omit_handoff_reason_when_missing(self, kwargs):
        facts = HandoffFacts(handoff_message="m", reason=None)
        event = build_handoff_event(facts, **kwargs)
        assert "handoff_reason" not in event["extra"]

    def test_detail_includes_handoff_message(self, kwargs):
        facts = HandoffFacts(handoff_message="Billing question about June rent")
        event = build_handoff_event(facts, **kwargs)
        assert event["activity"]["detail"] == "Handed off to staff: Billing question about June rent"

    def test_detail_omits_message_when_missing(self, kwargs):
        facts = HandoffFacts(handoff_message=None)
        event = build_handoff_event(facts, **kwargs)
        assert event["activity"]["detail"] == "Handed off to staff"

    def test_extras_include_handoff_message_and_common_context(self, kwargs):
        facts = HandoffFacts(handoff_message="Billing question")
        event = build_handoff_event(facts, **kwargs)
        extra = event["extra"]
        assert extra["handoff_message"] == "Billing question"
        # Common context fields land here too.
        assert extra["channel"] == "CHAT"
        assert extra["originating_source"] == "RESIDENT_AI"
        assert extra["chat_session_id"] == "cs-1"
        # Resident is in references, not extras.
        assert "knock_resident_id" not in extra

    def test_extras_omit_handoff_message_when_missing(self, kwargs):
        facts = HandoffFacts(handoff_message=None)
        event = build_handoff_event(facts, **kwargs)
        assert "handoff_message" not in event["extra"]

    def test_handoff_portal_link_lands_in_extras_as_loft_living_link(self, kwargs):
        # Handoff emits the portal link under the unified `loft_living_link`
        # key — same shape as sr_created / guest_parking events.
        facts = HandoffFacts(
            handoff_message="Billing question",
            handoff_portal_link="https://example.loftliving.com/portal/messenger",
        )
        event = build_handoff_event(facts, **kwargs)
        assert event["extra"]["loft_living_link"] == "https://example.loftliving.com/portal/messenger"
        assert "handoff_portal_link" not in event["extra"]

    def test_extras_omit_loft_living_link_when_missing(self, kwargs):
        facts = HandoffFacts(handoff_message="Voice transfer", handoff_portal_link=None)
        event = build_handoff_event(facts, **kwargs)
        assert "loft_living_link" not in event["extra"]
        assert "handoff_portal_link" not in event["extra"]

    def test_extras_carry_handoff_topic_string(self, kwargs):
        facts = HandoffFacts(
            handoff_message="Late fee waiver",
            reason=HandoffReasonCode.COMPLAINT,
            topic=HandoffTopic.BALANCE_RESOLUTION,
        )
        event = build_handoff_event(facts, **kwargs)
        assert event["extra"]["handoff_topic"] == "BALANCE_RESOLUTION"

    def test_extras_omit_handoff_topic_when_missing(self, kwargs):
        facts = HandoffFacts(handoff_message="m", topic=None)
        event = build_handoff_event(facts, **kwargs)
        assert "handoff_topic" not in event["extra"]

    def test_references_seeded_with_company_property_resident(self, kwargs):
        facts = HandoffFacts(handoff_message="Billing question")
        event = build_handoff_event(facts, **kwargs)
        types = [r["type"] for r in event["references"]]
        assert types == ["COMPANY", "PROPERTY", "RESIDENT"]
        resident_ref = next(r for r in event["references"] if r["type"] == "RESIDENT")
        assert resident_ref["id"] == "r-3"


class TestExtractHandoffEvents:
    """Top-level entry point: takes a `SessionScope`, derives common
    identifiers + task_id from the session itself."""

    def test_returns_one_event_per_handoff(self, make_session):
        events = extract_handoff_events(
            "Billing question",
            context=make_session(),
            reason=HandoffReasonCode.RESIDENT_REQUESTED,
        )
        assert len(events) == 1
        assert events[0]["activity"]["summary"] == "Handoff to Staff - Resident Requested"
        assert events[0]["activity"]["detail"] == "Handed off to staff: Billing question"

    def test_emits_event_even_without_message(self, make_session):
        events = extract_handoff_events(
            None,
            context=make_session(channel="VOICE"),
            reason=HandoffReasonCode.EMERGENCY,
        )
        assert len(events) == 1
        assert events[0]["activity"]["detail"] == "Handed off to staff"
        assert events[0]["activity"]["summary"] == "Handoff to Staff - Emergency"
        assert "handoff_message" not in events[0]["extra"]

    def test_reason_lands_in_extras_handoff_reason(self, make_session):
        events = extract_handoff_events(
            "Billing question",
            context=make_session(),
            reason=HandoffReasonCode.COMPLAINT,
        )
        assert events[0]["extra"]["handoff_reason"] == "COMPLAINT"

    def test_handoff_portal_link_passed_through_as_loft_living_link(self, make_session):
        events = extract_handoff_events(
            "Billing question",
            context=make_session(),
            handoff_portal_link="https://example.loftliving.com/portal/messenger",
        )
        assert events[0]["extra"]["loft_living_link"] == "https://example.loftliving.com/portal/messenger"

    def test_common_context_pulled_from_session(self, make_session):
        events = extract_handoff_events("Billing question", context=make_session())
        extra = events[0]["extra"]
        assert extra["chat_session_id"] == "cs-1"
        assert extra["channel"] == "CHAT"
        # Resident is in references (id "r-3"), not extras.
        assert "knock_resident_id" not in extra
        resident_ref = next(r for r in events[0]["references"] if r["type"] == "RESIDENT")
        assert resident_ref["id"] == "r-3"

    def test_voice_session_includes_call_sid(self, make_session):
        events = extract_handoff_events(
            "Billing question",
            context=make_session(channel="VOICE"),
        )
        assert events[0]["extra"]["call_sid"] == "CA-1"

    def test_topic_lands_in_extras_handoff_topic(self, make_session):
        events = extract_handoff_events(
            "Need late fee waiver",
            context=make_session(),
            reason=HandoffReasonCode.COMPLAINT,
            topic=HandoffTopic.BALANCE_RESOLUTION,
        )
        assert events[0]["extra"]["handoff_topic"] == "BALANCE_RESOLUTION"

    def test_topic_omitted_when_unset(self, make_session):
        events = extract_handoff_events(
            "Generic question",
            context=make_session(),
            reason=HandoffReasonCode.RESIDENT_REQUESTED,
        )
        assert "handoff_topic" not in events[0]["extra"]

    def test_property_metadata_and_stream_id_in_extras(self, make_session):
        events = extract_handoff_events(
            "Late fee waiver",
            context=make_session(
                channel="VOICE",
                chat_session_id="cs-voice",
                property_name="Cassidy South",
                property_timezone="America/Chicago",
                product_info_thread_id="PZAYT6NL-1744186406",
            ),
            reason=HandoffReasonCode.COMPLAINT,
        )
        extra = events[0]["extra"]
        assert extra["property_name"] == "Cassidy South"
        assert extra["property_timezone"] == "America/Chicago"
        assert extra["resident_stream_id"] == "PZAYT6NL-1744186406"

    def test_property_metadata_and_stream_id_omitted_when_missing(self, make_session):
        events = extract_handoff_events(
            "Generic",
            context=make_session(),
            reason=HandoffReasonCode.RESIDENT_REQUESTED,
        )
        extra = events[0]["extra"]
        assert "property_name" not in extra
        assert "property_timezone" not in extra
        assert "resident_stream_id" not in extra
