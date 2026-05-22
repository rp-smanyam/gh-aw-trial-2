from unittest.mock import MagicMock

import pytest
from cashews import cache
from fastapi.testclient import TestClient

from agent_leasing.server import app


@pytest.fixture(scope="session", autouse=True)
def _configure_cashews_for_unit_tests():
    # Production wires cashews in agent_leasing.util.memory at startup; unit tests
    # don't run that path, so any cashews-decorated function (e.g. fetch_ldp_property_data)
    # raises NotConfiguredError when its test happens to run before something else
    # calls cache.setup. Use an in-memory backend so isolated runs work.
    cache.setup("mem://")
    yield


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as client:
        yield client


_PRODUCT_BY_CHANNEL = {
    "VOICE": "renter_ai_resident_voice",
    "SMS": "renter_ai_resident_sms",
    "EMAIL": "renter_ai_resident_email",
    "CHAT": "renter_ai_resident_chat",
}


def _build_session_mock(
    channel="CHAT",
    chat_session_id="cs-1",
    thread_id=None,
    session_marker="marker-1",
    *,
    product_info_thread_id=None,
    property_name=None,
    property_timezone=None,
    uc_company_id=None,
    uc_property_id=None,
    uc_resident_household_id=None,
    uc_resident_member_id=None,
):
    pi = MagicMock()
    pi.knock_property_id = "p-2"
    pi.knock_resident_id = "r-3"
    pi.uc_first_name = "Alex"
    pi.uc_last_name = "Smith"
    pi.ab_unit_number = "204"
    pi.ab_building_number = "B"
    pi.call_sid = "CA-1" if channel == "VOICE" else None
    pi.knock_company_id = "c-1"
    pi.uc_portal_base_url = "https://example.loftliving.com"
    pi.static_paths = MagicMock(service_request="/portal/mr")
    # Defaults to None so unrelated extractor tests don't see Mock-typed
    # values in `extra.map`. Override per-test via the kwargs above.
    pi.thread_id = product_info_thread_id
    pi.property_name = property_name
    pi.property_timezone = property_timezone
    pi.uc_company_id = uc_company_id
    pi.uc_property_id = uc_property_id
    pi.uc_resident_household_id = uc_resident_household_id
    pi.uc_resident_member_id = uc_resident_member_id

    ask_request = MagicMock()
    ask_request.product_info = pi
    ask_request.product = _PRODUCT_BY_CHANNEL.get(channel, _PRODUCT_BY_CHANNEL["CHAT"])
    ask_request.chat_session_id = chat_session_id
    # `derive_conversation_key` reads `ctx.ask_request.conversation_type.value`.
    ask_request.conversation_type = MagicMock(value=channel.lower())

    session = MagicMock()
    session.ask_request = ask_request
    session.thread_id = thread_id
    session.session_marker = session_marker
    session.pending_activity_publishes = set()
    # MagicMock returns a truthy Mock for any unset attribute, so the dedup
    # gate would silently read True without an explicit default — set False
    # so frustrated-user tests don't have to override per case.
    session.frustrated_user_emitted = False
    return session


@pytest.fixture
def make_session():
    """Lightweight `SessionScope`-shaped mock builder for unit tests.

    Extractors and bridge plumbing only walk attributes — duck-typed
    mock saves the cost of standing up a full pydantic SessionScope per
    test. Callable: `make_session(channel=..., chat_session_id=..., thread_id=...)`.
    """
    return _build_session_mock
