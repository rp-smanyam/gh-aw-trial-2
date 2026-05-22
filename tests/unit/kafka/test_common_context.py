import pytest

from agent_leasing.kafka.common_context import build_common_context


def test_minimal_context_has_source_and_channel():
    ctx = build_common_context(channel="CHAT")
    assert ctx == {"originating_source": "RESIDENT_AI", "channel": "CHAT"}


def test_fully_populated_context():
    ctx = build_common_context(
        channel="VOICE",
        first_name="Alex",
        last_name="Smith",
        ab_unit_number="204",
        ab_building_number="B",
        chat_session_id="cs-1",
        thread_id="th-1",
        call_sid="CA-1",
        property_name="Cassidy South",
        property_timezone="America/Chicago",
        resident_stream_id="PZAYT6NL-1744186406",
        os_company_id="7661634",
        os_property_id="7661666",
        resident_household_id="136",
        resident_member_id="137",
    )
    assert ctx == {
        "originating_source": "RESIDENT_AI",
        "channel": "VOICE",
        "first_name": "Alex",
        "last_name": "Smith",
        "ab_unit_number": "204",
        "ab_building_number": "B",
        "chat_session_id": "cs-1",
        "thread_id": "th-1",
        "call_sid": "CA-1",
        "property_name": "Cassidy South",
        "property_timezone": "America/Chicago",
        "resident_stream_id": "PZAYT6NL-1744186406",
        "os_company_id": "7661634",
        "os_property_id": "7661666",
        "resident_household_id": "136",
        "resident_member_id": "137",
    }


def test_os_and_resident_id_keys_omitted_when_missing():
    ctx = build_common_context(channel="CHAT")
    assert "os_company_id" not in ctx
    assert "os_property_id" not in ctx
    assert "resident_household_id" not in ctx
    assert "resident_member_id" not in ctx


def test_new_optional_fields_omitted_when_missing():
    ctx = build_common_context(channel="CHAT")
    assert "property_name" not in ctx
    assert "property_timezone" not in ctx
    assert "resident_stream_id" not in ctx


def test_resident_id_is_not_emitted_in_extras():
    # Resident identity rides in `references` (type RESIDENT), not `extra.map`.
    ctx = build_common_context(channel="CHAT")
    assert "knock_resident_id" not in ctx
    assert "resident_id" not in ctx


def test_falsy_fields_are_omitted_not_empty_strings():
    ctx = build_common_context(
        channel="SMS",
        first_name="",
        last_name=None,
    )
    assert "first_name" not in ctx
    assert "last_name" not in ctx


def test_channel_is_required():
    with pytest.raises(ValueError):
        build_common_context(channel="")


def test_non_string_values_are_stringified():
    ctx = build_common_context(channel="CHAT", ab_unit_number=12)
    assert ctx["ab_unit_number"] == "12"
