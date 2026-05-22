import pytest

from agent_leasing.api.model import UCReference
from agent_leasing.kafka.task_activity.event_context import build_common_event_context


class TestBuildCommonEventContext:
    def test_chat_context_pulls_through_identifiers(self, make_session):
        session = make_session(channel="CHAT", chat_session_id="cs-1")
        ctx = build_common_event_context(session)
        assert ctx["channel"] == "CHAT"
        assert ctx["knock_company_id"] == "c-1"
        assert ctx["knock_property_id"] == "p-2"
        assert ctx["knock_resident_id"] == "r-3"
        assert ctx["first_name"] == "Alex"
        assert ctx["last_name"] == "Smith"
        assert ctx["ab_unit_number"] == "204"
        assert ctx["ab_building_number"] == "B"
        assert ctx["chat_session_id"] == "cs-1"
        assert ctx["thread_id"] is None
        assert ctx["call_sid"] is None
        # task_id is the deterministic uuid5 of channel + key.
        assert ctx["task_id"]

    def test_voice_pulls_call_sid_from_product_info(self, make_session):
        session = make_session(channel="VOICE", chat_session_id="cs-voice")
        ctx = build_common_event_context(session)
        assert ctx["channel"] == "VOICE"
        assert ctx["call_sid"] == "CA-1"

    def test_carries_no_activity_specific_fields(self, make_session):
        session = make_session(channel="CHAT", chat_session_id="cs-1")
        ctx = build_common_event_context(session)
        assert "loft_living_link" not in ctx
        assert "user_request" not in ctx
        assert "handoff_portal_link" not in ctx

    def test_raises_when_chat_session_id_missing(self, make_session):
        # `derive_conversation_key` treats chat_session_id as an invariant
        # of every code path that reaches publishing — let the error surface
        # loud rather than fabricating an orphan task_id.
        with pytest.raises(ValueError, match="conversation_key"):
            build_common_event_context(make_session(channel="CHAT", chat_session_id=None))

    def test_derives_property_metadata_and_stream_id(self, make_session):
        session = make_session(
            channel="VOICE",
            chat_session_id="cs-voice",
            property_name="Cassidy South",
            property_timezone="America/Chicago",
            product_info_thread_id="PZAYT6NL-1744186406",
        )
        ctx = build_common_event_context(session)
        assert ctx["property_name"] == "Cassidy South"
        assert ctx["property_timezone"] == "America/Chicago"
        assert ctx["resident_stream_id"] == "PZAYT6NL-1744186406"

    def test_property_metadata_optional_when_payload_missing(self, make_session):
        # Resident chat/sms payloads may omit thread_id and property metadata —
        # the dict still includes the keys (with None) so extractors can
        # `.get(...)` uniformly; build_common_context drops them later.
        ctx = build_common_event_context(make_session(channel="CHAT"))
        assert ctx["property_name"] is None
        assert ctx["property_timezone"] is None
        assert ctx["resident_stream_id"] is None

    def test_pulls_os_and_resident_ids_from_uc_references(self, make_session):
        session = make_session(
            channel="CHAT",
            chat_session_id="cs-1",
            uc_company_id=UCReference(id="7661634", source="OS"),
            uc_property_id=UCReference(id="7661666", source="OS"),
            uc_resident_household_id=UCReference(id="136", source="OS"),
            uc_resident_member_id=UCReference(id="137", source="OS"),
        )
        ctx = build_common_event_context(session)
        assert ctx["os_company_id"] == "7661634"
        assert ctx["os_property_id"] == "7661666"
        assert ctx["resident_household_id"] == "136"
        assert ctx["resident_member_id"] == "137"

    def test_os_and_resident_ids_none_when_uc_refs_missing(self, make_session):
        ctx = build_common_event_context(make_session(channel="CHAT"))
        assert ctx["os_company_id"] is None
        assert ctx["os_property_id"] is None
        assert ctx["resident_household_id"] is None
        assert ctx["resident_member_id"] is None

    def test_integer_uc_reference_ids_are_stringified(self, make_session):
        session = make_session(
            channel="CHAT",
            chat_session_id="cs-1",
            uc_resident_member_id=UCReference(id=137, source="OS"),
        )
        ctx = build_common_event_context(session)
        assert ctx["resident_member_id"] == "137"
