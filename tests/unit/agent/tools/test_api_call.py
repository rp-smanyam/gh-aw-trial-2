"""Tests for call_facilities_thinker_via_api."""

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from agents import RunContextWrapper

from agent_leasing.agent.tools.api_call.api_call import (
    BACKGROUND_TASKS,
    _call_facilities_thinker_via_api_impl,
    _queue_resolution_ack_impl,
)


class _IdField:
    """Simple helper to mimic objects that expose an `id` attribute."""

    def __init__(self, value):
        self.id = value


@pytest.fixture(autouse=True)
def patch_custom_span():
    """Replace custom_span with a no-op context manager for unit tests."""

    def _noop_custom_span(*args, **kwargs):
        return nullcontext()

    with patch("agent_leasing.agent.tools.api_call.api_call.custom_span", new=_noop_custom_span):
        yield


@pytest.fixture
def run_context():
    """Create a RunContextWrapper with realistic product info metadata."""
    product_info = SimpleNamespace(
        uc_company_id=_IdField(7641926),
        uc_property_id=_IdField(7641954),
        uc_resident_household_id=_IdField(105),
        uc_resident_member_id=_IdField(110),
        uc_community_id=_IdField(13319),
        ab_resident_id=_IdField(4860852),
        ab_unit_id=_IdField(2056345),
        uc_consumer_identity_token=_IdField("cidp-token"),
        resident_phone="+15555550123",
    )
    ask_request = SimpleNamespace(
        prompt="Kitchen leak near the sink",
        product="RESIDENT_ONE_CHAT",
        product_info=product_info,
    )
    context = SimpleNamespace(ask_request=ask_request, pte_setting=False)

    wrapper = MagicMock(spec=RunContextWrapper)
    wrapper.context = context
    return wrapper


@pytest.fixture(autouse=True)
def clear_background_tasks():
    """Ensure background task tracking is isolated between tests."""
    BACKGROUND_TASKS.clear()
    yield
    BACKGROUND_TASKS.clear()


@pytest.mark.asyncio
async def test_call_facilities_thinker_builds_expected_payload_for_chat(run_context):
    """The tool should forward resident metadata, context, and emergency flag to the API."""
    api_response = {
        "self_service_available": True,
        "service_request_numbers": ["12345"],
        "instructions": "SR-12345 is created.",
    }
    mock_api_call = AsyncMock(return_value=api_response)

    with patch(
        "agent_leasing.agent.tools.api_call.api_call.perform_api_call",
        new=mock_api_call,
    ):
        result = await _call_facilities_thinker_via_api_impl(run_context, emergency=True)

    assert result["instructions"] == "SR-12345 is created."
    assert result["self_service_available"] is True
    assert result["service_request_numbers"] == ["12345"]
    mock_api_call.assert_awaited_once()
    kwargs = mock_api_call.await_args.kwargs

    payload = kwargs["payload"]
    assert payload["channel"] == "chat"
    assert payload["relevant_context_from_last_user_message"] == run_context.context.ask_request.prompt
    assert payload["cidp_token"] == "cidp-token"
    assert payload["phone_number"] == "+15555550123"
    assert payload["emergency"] is True
    assert "permission_to_enter" not in payload
    assert "permission_entry_notes" not in payload

    ids = payload["resident_identifiers"]
    assert ids["pmc_id"] == 7641926
    assert ids["site_id"] == 7641954
    assert ids["resident_household_id"] == 105
    assert ids["resident_member_id"] == 110
    assert ids["ab_community_id"] == 13319
    assert ids["ab_resident_id"] == 4860852
    assert ids["ab_unit_id"] == 2056345


@pytest.mark.asyncio
async def test_call_facilities_thinker_omits_chat_specific_fields_for_non_chat_channel(run_context):
    """Non-chat channels should not send CIDP token, phone number, or ab_unit_id."""
    run_context.context.ask_request.product = "RESIDENT_ONE_SMS"
    mock_api_call = AsyncMock(
        return_value={
            "self_service_available": False,
            "service_request_numbers": None,
            "instructions": "You have 3 open service requests.",
        }
    )

    with patch(
        "agent_leasing.agent.tools.api_call.api_call.perform_api_call",
        new=mock_api_call,
    ):
        await _call_facilities_thinker_via_api_impl(run_context)

    payload = mock_api_call.await_args.kwargs["payload"]
    assert payload["channel"] == "sms"
    assert "cidp_token" not in payload
    assert "phone_number" not in payload
    assert "ab_unit_id" not in payload["resident_identifiers"]
    assert "permission_to_enter" not in payload
    assert "permission_entry_notes" not in payload


@pytest.mark.asyncio
async def test_call_facilities_thinker_handles_missing_response(run_context):
    """If the downstream API returns None, the tool should surface an error message."""
    mock_api_call = AsyncMock(return_value=None)

    with patch(
        "agent_leasing.agent.tools.api_call.api_call.perform_api_call",
        new=mock_api_call,
    ):
        result = await _call_facilities_thinker_via_api_impl(run_context)

    assert result == "Error: No response from facilities thinker API."


@pytest.mark.asyncio
async def test_call_facilities_thinker_handles_exception(run_context):
    """Exceptions from perform_api_call should be caught and returned as error text."""
    mock_api_call = AsyncMock(side_effect=RuntimeError("boom"))

    with patch(
        "agent_leasing.agent.tools.api_call.api_call.perform_api_call",
        new=mock_api_call,
    ):
        result = await _call_facilities_thinker_via_api_impl(run_context)

    assert result.startswith("Error calling facilities thinker via API: RuntimeError('boom')")


@pytest.mark.asyncio
async def test_call_facilities_thinker_uses_custom_message(run_context):
    """Explicit message argument should override the prompt in the payload."""
    run_context.context.ask_request.prompt = "Old prompt"
    custom_message = "List my active service requests"
    mock_api_call = AsyncMock(
        return_value={
            "self_service_available": False,
            "service_request_numbers": None,
            "instructions": "You have 3 open service requests.",
        }
    )

    with patch(
        "agent_leasing.agent.tools.api_call.api_call.perform_api_call",
        new=mock_api_call,
    ):
        await _call_facilities_thinker_via_api_impl(run_context, message=custom_message)

    payload = mock_api_call.await_args.kwargs["payload"]
    assert payload["relevant_context_from_last_user_message"] == custom_message


@pytest.mark.asyncio
async def test_call_facilities_thinker_respects_pte_setting_default(run_context):
    """When property requires permission-to-enter and no response provided, fields are omitted."""
    run_context.context.pte_setting = True
    mock_api_call = AsyncMock(
        return_value={
            "self_service_available": False,
            "service_request_numbers": None,
            "instructions": "You have 3 open service requests.",
        }
    )

    with patch(
        "agent_leasing.agent.tools.api_call.api_call.perform_api_call",
        new=mock_api_call,
    ):
        await _call_facilities_thinker_via_api_impl(run_context)

    payload = mock_api_call.await_args.kwargs["payload"]
    assert "permission_to_enter" not in payload
    assert "permission_entry_notes" not in payload


@pytest.mark.asyncio
async def test_call_facilities_thinker_honors_permission_overrides(run_context):
    """Explicit permission_to_enter and notes should override defaults."""
    run_context.context.pte_setting = True
    mock_api_call = AsyncMock(
        return_value={
            "self_service_available": False,
            "service_request_numbers": None,
            "instructions": "Created.",
        }
    )

    with patch(
        "agent_leasing.agent.tools.api_call.api_call.perform_api_call",
        new=mock_api_call,
    ):
        await _call_facilities_thinker_via_api_impl(
            run_context,
            permission_to_enter=True,
            permission_entry_notes="Resident approves entry while away",
        )

    payload = mock_api_call.await_args.kwargs["payload"]
    assert payload["permission_to_enter"] is True
    assert payload["permission_entry_notes"] == "Resident approves entry while away"


class _DummyContext:
    def __init__(self) -> None:
        self.langsmith_run_tree = {"trace": "id"}
        self.model_copy_calls: list[bool] = []

    def model_copy(self, deep: bool = False):
        self.model_copy_calls.append(deep)
        clone = _DummyContext()
        clone.langsmith_run_tree = self.langsmith_run_tree
        return clone


@pytest.mark.asyncio
async def test_queue_resolution_ack_tracks_task_and_cleans_up(monkeypatch):
    """Background resolution ack task should be tracked and removed when done."""

    dummy_context = _DummyContext()
    wrapper = MagicMock(spec=RunContextWrapper)
    wrapper.context = dummy_context

    callbacks: list = []

    class _DummyTask(Mock):
        def __init__(self):
            super().__init__(spec=asyncio.Task)

        def add_done_callback(self, cb):
            callbacks.append(cb)

    mock_task = _DummyTask()

    monkeypatch.setattr(
        "agent_leasing.agent.tools.api_call.api_call._call_facilities_thinker_via_api_impl", AsyncMock()
    )

    create_task_calls = 0

    def _fake_create_task(coro, *args, **kwargs):
        nonlocal create_task_calls
        create_task_calls += 1
        # Prevent unawaited coroutine warnings in tests
        coro.close()
        return mock_task

    monkeypatch.setattr("agent_leasing.agent.tools.api_call.api_call.asyncio.create_task", _fake_create_task)

    response = await _queue_resolution_ack_impl(wrapper, message="Fixed leak")

    assert response.startswith("Noted")
    assert create_task_calls == 1
    assert mock_task in BACKGROUND_TASKS

    # Simulate task finishing successfully
    callbacks[0](mock_task)
    assert mock_task not in BACKGROUND_TASKS


@pytest.mark.asyncio
async def test_queue_resolution_ack_uses_shallow_copy(monkeypatch):
    """Ensure we call model_copy with deep=False instead of deepcopying the context."""

    dummy_context = _DummyContext()
    wrapper = MagicMock(spec=RunContextWrapper)
    wrapper.context = dummy_context

    mock_task = Mock(spec=asyncio.Task)
    mock_task.add_done_callback = lambda cb: None

    monkeypatch.setattr(
        "agent_leasing.agent.tools.api_call.api_call._call_facilities_thinker_via_api_impl", AsyncMock()
    )

    def _fake_create_task(coro, *args, **kwargs):
        coro.close()
        return mock_task

    monkeypatch.setattr("agent_leasing.agent.tools.api_call.api_call.asyncio.create_task", _fake_create_task)

    await _queue_resolution_ack_impl(wrapper, message="Fixed leak")

    assert dummy_context.model_copy_calls == [False]
