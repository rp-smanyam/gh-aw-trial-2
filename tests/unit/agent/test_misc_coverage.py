"""Miscellaneous unit tests to increase coverage across multiple modules.

Targets uncovered lines in:
  - pii_guardrail.py (lines 54, 181-190, 231-233, 264-266, 292)
  - api_call.py (lines 73, 190-195, 207-220, 247-256, 265, 294)
  - simple/agent.py (lines 36-44, 60, 69-71)
"""

import json
from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from agents import RunContextWrapper, tool_context
from agents.tool_context import ToolContext
from openai.types.responses import ResponseFunctionToolCall

from agent_leasing.agent.guardrails.pii_guardrail.pii_guardrail import (
    PIIGuardrailOutput,
    _check_pii,
    detect_pii,
    is_business_phone_number,
)
from agent_leasing.agent.tools.api_call.api_call import (
    BACKGROUND_TASKS,
    _build_facilities_payload,
    _queue_resolution_ack_impl,
    call_facilities_thinker_via_api,
    prefetch_active_service_requests,
    queue_resolution_ack,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _IdField:
    """Mimics objects that expose an ``id`` attribute."""

    def __init__(self, value):
        self.id = value


def _make_run_context(product="RESIDENT_ONE_CHAT"):
    """Build a minimal RunContextWrapper with realistic product info metadata."""
    product_info = SimpleNamespace(
        uc_company_id=_IdField(100),
        uc_property_id=_IdField(200),
        uc_resident_household_id=_IdField(300),
        uc_resident_member_id=_IdField(400),
        uc_community_id=_IdField(500),
        ab_resident_id=_IdField(600),
        ab_unit_id=_IdField(700),
        uc_consumer_identity_token=_IdField("tok-1"),
        resident_phone="+15555550199",
    )
    ask_request = SimpleNamespace(
        prompt="Test prompt",
        product=product,
        product_info=product_info,
    )
    context = SimpleNamespace(
        ask_request=ask_request,
        pte_setting=False,
        previous_response_id=None,
        identity_verified={"CHAT": True},
        identity_verified_with_birth_year={},
    )
    wrapper = MagicMock(spec=RunContextWrapper)
    wrapper.context = context
    return wrapper


# ---------------------------------------------------------------------------
# pii_guardrail.py
# ---------------------------------------------------------------------------


class TestPIIGuardrailOutputLabels:
    """Cover line 54 -- the ``labels`` property."""

    def test_labels_returns_pii_types_found(self):
        output = PIIGuardrailOutput(
            reasoning="test",
            pii_types_found=["ssn", "credit card"],
            is_pii=True,
        )
        assert output.labels == ["ssn", "credit card"]

    def test_labels_empty_list(self):
        output = PIIGuardrailOutput(
            reasoning="clean",
            pii_types_found=[],
            is_pii=False,
        )
        assert output.labels == []


class TestIsBusinessPhoneNumber:
    """Cover lines 181-190."""

    def test_returns_true_for_towing_context(self):
        text = "Call the towing company at 555-123-4567 for assistance"
        assert is_business_phone_number(text, 28, 40) is True

    def test_returns_true_for_maintenance_context(self):
        text = "For maintenance call 555-123-4567"
        assert is_business_phone_number(text, 21, 33) is True

    def test_returns_true_for_office_context(self):
        text = "The office number is 555-123-4567"
        assert is_business_phone_number(text, 21, 33) is True

    def test_returns_true_for_emergency_context(self):
        text = "In an emergency dial 555-123-4567"
        assert is_business_phone_number(text, 21, 33) is True

    def test_returns_false_for_personal_context(self):
        text = "My number is 555-123-4567"
        assert is_business_phone_number(text, 13, 25) is False

    def test_returns_false_with_no_keywords(self):
        text = "Please dial 555-123-4567 today"
        # "dial" is not in BUSINESS_PHONE_CONTEXTS (only "contact", "service", etc.)
        # Actually "contact" IS there. Let's use a truly neutral string.
        text = "Here is 555-123-4567 for you"
        assert is_business_phone_number(text, 8, 20) is False

    def test_context_window_limited_to_50_chars(self):
        # Place a business keyword more than 50 characters away from the phone number
        padding = "x" * 60
        text = f"towing {padding} 555-123-4567"
        phone_start = len(f"towing {padding} ")
        phone_end = phone_start + 12
        assert is_business_phone_number(text, phone_start, phone_end) is False


class TestDetectPiiBusinessPhoneSkip:
    """Cover lines 231-233 -- phone number detected but skipped because it's a business phone."""

    def test_business_phone_skipped(self):
        # PHONE_NUMBER is commented out of ENTITIES_TO_DETECT, so we must mock the
        # analyzer to return a phone number result to exercise the business phone skip path.
        fake_result = Mock()
        fake_result.entity_type = "PHONE_NUMBER"
        fake_result.start = 27
        fake_result.end = 39

        with patch(
            "agent_leasing.agent.guardrails.pii_guardrail.pii_guardrail.analyzer.analyze",
            return_value=[fake_result],
        ):
            text = "Call the towing service at 555-123-4567 for help"
            result = detect_pii(text)

        # The phone number should be skipped because it's in a business context
        assert result.contains_pii is False
        assert "phone number" not in result.pii_types_found

    def test_personal_phone_not_skipped(self):
        # Phone number without business context should NOT be skipped
        fake_result = Mock()
        fake_result.entity_type = "PHONE_NUMBER"
        fake_result.start = 13
        fake_result.end = 25

        with patch(
            "agent_leasing.agent.guardrails.pii_guardrail.pii_guardrail.analyzer.analyze",
            return_value=[fake_result],
        ):
            text = "My number is 555-123-4567"
            result = detect_pii(text)

        # Personal phone number should be flagged as PII
        assert result.contains_pii is True
        assert "phone number" in result.pii_types_found


class TestDetectPiiExceptionHandler:
    """Cover lines 264-266 -- exception inside detect_pii."""

    def test_exception_returns_contains_pii_true(self):
        with patch(
            "agent_leasing.agent.guardrails.pii_guardrail.pii_guardrail.analyzer.analyze",
            side_effect=Exception("kaboom"),
        ):
            result = detect_pii("some text")
        assert result.contains_pii is True
        assert result.pii_types_found == ["detection error"]
        assert "kaboom" in result.reasoning
        assert result.redacted_text == "some text"


class TestCheckPiiInvalidContentType:
    """Cover line 292 -- _check_pii with invalid content_type."""

    @pytest.mark.asyncio
    async def test_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid content type"):
            await _check_pii("some content", "invalid_type", "en")


# ---------------------------------------------------------------------------
# api_call.py
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_custom_span():
    """Replace custom_span with a no-op context manager for unit tests."""

    def _noop_custom_span(*args, **kwargs):
        return nullcontext()

    with patch("agent_leasing.agent.tools.api_call.api_call.custom_span", new=_noop_custom_span):
        yield


@pytest.fixture(autouse=True)
def _clear_background_tasks():
    """Ensure background task tracking is isolated between tests."""
    BACKGROUND_TASKS.clear()
    yield
    BACKGROUND_TASKS.clear()


class TestBuildFacilitiesPayloadSelfServiceDisabled:
    """Cover line 73 -- self-service disabled message append."""

    def test_appends_no_self_service_message(self):
        ctx = _make_run_context()

        with (
            patch(
                "agent_leasing.agent.tools.api_call.api_call.get_channel_from_context",
                return_value="CHAT",
            ),
            patch("agent_leasing.agent.tools.api_call.api_call.settings") as mock_settings,
        ):
            mock_settings.facilities_thinker_self_service_enabled = False
            payload = _build_facilities_payload(
                ctx,
                emergency=False,
                channel="chat",
                message="Fix my sink",
                permission_to_enter=None,
                permission_entry_notes=None,
            )

        assert payload["relevant_context_from_last_user_message"].endswith(
            " Resident does not want self-service troubleshooting steps."
        )

    def test_does_not_append_when_self_service_enabled(self):
        ctx = _make_run_context()

        with (
            patch(
                "agent_leasing.agent.tools.api_call.api_call.get_channel_from_context",
                return_value="CHAT",
            ),
            patch("agent_leasing.agent.tools.api_call.api_call.settings") as mock_settings,
        ):
            mock_settings.facilities_thinker_self_service_enabled = True
            payload = _build_facilities_payload(
                ctx,
                emergency=False,
                channel="chat",
                message="Fix my sink",
                permission_to_enter=None,
                permission_entry_notes=None,
            )

        assert payload["relevant_context_from_last_user_message"] == "Fix my sink"

    def test_does_not_append_for_list_active_service_requests(self):
        ctx = _make_run_context()

        with (
            patch(
                "agent_leasing.agent.tools.api_call.api_call.get_channel_from_context",
                return_value="CHAT",
            ),
            patch("agent_leasing.agent.tools.api_call.api_call.settings") as mock_settings,
        ):
            mock_settings.facilities_thinker_self_service_enabled = False
            payload = _build_facilities_payload(
                ctx,
                emergency=False,
                channel="chat",
                message="List my active service requests",
                permission_to_enter=None,
                permission_entry_notes=None,
            )

        # The special "List my active service requests" message should NOT get the suffix
        assert payload["relevant_context_from_last_user_message"] == "List my active service requests"


class TestCallFacilitiesThinkerViaApiVerificationCheck:
    """Cover lines 190-195 -- verification check failure in function tool wrapper."""

    @pytest.mark.asyncio
    async def test_returns_error_when_verification_fails(self):
        context = SimpleNamespace(
            ask_request=SimpleNamespace(product="RESIDENT_ONE_SMS"),
            identity_verified={},
            identity_verified_with_birth_year={},
        )
        tool_call = ResponseFunctionToolCall(
            arguments="{}",
            call_id="test-call-id",
            name="call_facilities_thinker_via_api",
            type="function_call",
        )
        mock_tool_ctx = ToolContext.from_agent_context(
            RunContextWrapper(context=context),
            tool_call_id="test-call-id",
            tool_call=tool_call,
        )

        with patch(
            "agent_leasing.agent.tools.api_call.api_call.check_verification_status",
            return_value=(False, "VERIFICATION_REQUIRED: Call verify_resident_identity first."),
        ):
            result = await call_facilities_thinker_via_api.on_invoke_tool(mock_tool_ctx, json.dumps({}))

        assert isinstance(result, dict)
        assert "error" in result
        assert "VERIFICATION_REQUIRED" in result["error"]

    @pytest.mark.asyncio
    async def test_passes_through_when_verification_succeeds(self):
        context = SimpleNamespace(
            ask_request=SimpleNamespace(product="RESIDENT_ONE_CHAT"),
            identity_verified={"CHAT": True},
            identity_verified_with_birth_year={},
        )
        tool_call = ResponseFunctionToolCall(
            arguments="{}",
            call_id="test-call-id",
            name="call_facilities_thinker_via_api",
            type="function_call",
        )
        mock_tool_ctx = ToolContext.from_agent_context(
            RunContextWrapper(context=context),
            tool_call_id="test-call-id",
            tool_call=tool_call,
        )
        api_response = {"instructions": "Done."}

        with (
            patch(
                "agent_leasing.agent.tools.api_call.api_call.check_verification_status",
                return_value=(True, None),
            ),
            patch(
                "agent_leasing.agent.tools.api_call.api_call._call_facilities_thinker_via_api_impl",
                new_callable=AsyncMock,
                return_value=api_response,
            ),
        ):
            result = await call_facilities_thinker_via_api.on_invoke_tool(mock_tool_ctx, json.dumps({}))

        assert result == api_response


class TestPrefetchActiveServiceRequests:
    """Cover lines 207-220."""

    @pytest.mark.asyncio
    async def test_stores_instructions_in_context(self):
        ctx = _make_run_context()
        api_response = {"instructions": "You have 2 open service requests."}

        with patch(
            "agent_leasing.agent.tools.api_call.api_call._call_facilities_thinker_via_api_impl",
            new_callable=AsyncMock,
            return_value=api_response,
        ):
            tool_name = await prefetch_active_service_requests(ctx)

        assert tool_name == "call_facilities_thinker_via_api"
        assert ctx.context.active_service_requests == "You have 2 open service requests."

    @pytest.mark.asyncio
    async def test_truncates_long_instructions(self):
        ctx = _make_run_context()
        long_instructions = "A" * 1500
        api_response = {"instructions": long_instructions}

        with patch(
            "agent_leasing.agent.tools.api_call.api_call._call_facilities_thinker_via_api_impl",
            new_callable=AsyncMock,
            return_value=api_response,
        ):
            tool_name = await prefetch_active_service_requests(ctx)

        assert tool_name == "call_facilities_thinker_via_api"
        stored = ctx.context.active_service_requests
        assert len(stored) == 1003  # 1000 + "..."
        assert stored.endswith("...")

    @pytest.mark.asyncio
    async def test_returns_none_when_no_instructions(self):
        ctx = _make_run_context()
        api_response = {"other_field": "data"}

        with patch(
            "agent_leasing.agent.tools.api_call.api_call._call_facilities_thinker_via_api_impl",
            new_callable=AsyncMock,
            return_value=api_response,
        ):
            tool_name = await prefetch_active_service_requests(ctx)

        assert tool_name is None

    @pytest.mark.asyncio
    async def test_returns_none_when_string_response(self):
        ctx = _make_run_context()

        with patch(
            "agent_leasing.agent.tools.api_call.api_call._call_facilities_thinker_via_api_impl",
            new_callable=AsyncMock,
            return_value="Error: something went wrong",
        ):
            tool_name = await prefetch_active_service_requests(ctx)

        assert tool_name is None


class TestQueueResolutionAckImpl:
    """Cover lines 247-256, 265, 294."""

    @pytest.mark.asyncio
    async def test_inner_run_calls_impl(self):
        """Verify that when the background task actually runs, it calls the impl."""
        ctx = _make_run_context()

        class _DummyContext:
            def __init__(self):
                self.langsmith_run_tree = None

            def model_copy(self, deep=False):
                return _DummyContext()

        ctx.context = _DummyContext()

        mock_impl = AsyncMock()

        with patch(
            "agent_leasing.agent.tools.api_call.api_call._call_facilities_thinker_via_api_impl",
            new=mock_impl,
        ):
            result = await _queue_resolution_ack_impl(ctx, message="Fixed leak")

            assert result.startswith("Noted")

            # Wait for the background task to complete (must be inside patch scope)
            tasks = list(BACKGROUND_TASKS)
            assert len(tasks) == 1
            await tasks[0]

        mock_impl.assert_awaited_once()
        call_kwargs = mock_impl.await_args.kwargs
        assert "Fixed leak" in call_kwargs["message"]
        assert "no further action" in call_kwargs["message"]

    @pytest.mark.asyncio
    async def test_inner_run_exception_handler(self):
        """Cover line 265 -- exception inside the background _run() coroutine."""
        ctx = _make_run_context()

        class _DummyContext:
            def __init__(self):
                self.langsmith_run_tree = None

            def model_copy(self, deep=False):
                return _DummyContext()

        ctx.context = _DummyContext()

        mock_impl = AsyncMock(side_effect=RuntimeError("boom"))

        with patch(
            "agent_leasing.agent.tools.api_call.api_call._call_facilities_thinker_via_api_impl",
            new=mock_impl,
        ):
            result = await _queue_resolution_ack_impl(ctx, message="Fixed it")

            assert result.startswith("Noted")

            # The background task should complete without raising (must be inside patch scope)
            tasks = list(BACKGROUND_TASKS)
            assert len(tasks) == 1
            await tasks[0]
            # The task completed (exception was caught inside _run)

    @pytest.mark.asyncio
    async def test_queue_resolution_ack_function_tool(self):
        """Cover line 294 -- the function tool wrapper delegates to impl."""
        context = SimpleNamespace(
            ask_request=SimpleNamespace(product="RESIDENT_ONE_CHAT"),
        )
        mock_tool_ctx = Mock(spec=tool_context.ToolContext)
        mock_tool_ctx.context = context
        mock_tool_ctx.tool_name = "queue_resolution_ack"

        with patch(
            "agent_leasing.agent.tools.api_call.api_call._queue_resolution_ack_impl",
            new_callable=AsyncMock,
            return_value="Noted. I'll let the team know the issue is resolved.",
        ) as mock_impl:
            result = await queue_resolution_ack.on_invoke_tool(mock_tool_ctx, json.dumps({"message": "Fixed sink"}))

        assert result.startswith("Noted")
        mock_impl.assert_awaited_once()


# ---------------------------------------------------------------------------
# simple/agent.py
# ---------------------------------------------------------------------------


class TestSimpleAgent:
    """Cover lines 36-44, 60, 69-71."""

    @pytest.mark.asyncio
    async def test_create_agent_realtime(self):
        """Line 36-44: When real_time=True, _create_agent returns a RealtimeAgent."""
        context = SimpleNamespace(
            property_id="prop-1",
            prospect_id="prospect-1",
            current_time=datetime(2025, 1, 1),
            ask_request=SimpleNamespace(product="simple"),
        )

        with (
            patch(
                "agent_leasing.agent.simple.agent.settings",
            ) as mock_settings,
            patch(
                "agent_leasing.agent.simple.agent.MCPServerStreamableHttp",
            ),
        ):
            mock_settings.knock_mcp_server = "http://localhost:9999"
            mock_settings.model = "gpt-4o"
            mock_settings.model_reasoning_effort = None
            mock_settings.model_verbosity = None
            mock_settings.model_temperature = None
            mock_settings.model_service_tier = None

            from agent_leasing.agent.simple.agent import SimpleAgent

            agent = SimpleAgent(context, real_time=True)

        realtime_agent = await agent._create_agent()

        from agents.realtime import RealtimeAgent

        assert isinstance(realtime_agent, RealtimeAgent)

    @pytest.mark.asyncio
    async def test_create_agent_non_realtime(self):
        """Lines 43-57: When real_time=False, _create_agent returns a regular Agent."""
        context = SimpleNamespace(
            property_id="prop-1",
            prospect_id="prospect-1",
            current_time=datetime(2025, 1, 1),
            ask_request=SimpleNamespace(product="simple"),
        )

        from agents import Agent
        from agents.model_settings import ModelSettings

        with (
            patch(
                "agent_leasing.agent.simple.agent.settings",
            ) as mock_settings,
            patch(
                "agent_leasing.agent.simple.agent.MCPServerStreamableHttp",
            ),
            patch(
                "agent_leasing.agent.simple.agent.build_model_settings",
                return_value=ModelSettings(),
            ),
        ):
            mock_settings.knock_mcp_server = "http://localhost:9999"
            mock_settings.model = "gpt-4o"
            mock_settings.model_reasoning_effort = None
            mock_settings.model_verbosity = None
            mock_settings.model_temperature = None
            mock_settings.model_service_tier = None

            from agent_leasing.agent.simple.agent import SimpleAgent

            agent = SimpleAgent(context, real_time=False)

            regular_agent = await agent._create_agent()
            assert isinstance(regular_agent, Agent)

    def test_agent_property(self):
        """Line 60: agent() returns agent_instance."""
        context = SimpleNamespace(
            property_id="prop-1",
            prospect_id="prospect-1",
            current_time=datetime(2025, 1, 1),
            ask_request=SimpleNamespace(product="simple"),
        )

        with (
            patch(
                "agent_leasing.agent.simple.agent.settings",
            ) as mock_settings,
            patch(
                "agent_leasing.agent.simple.agent.MCPServerStreamableHttp",
            ),
        ):
            mock_settings.knock_mcp_server = "http://localhost:9999"

            from agent_leasing.agent.simple.agent import SimpleAgent

            agent = SimpleAgent(context)

        sentinel = object()
        agent.agent_instance = sentinel
        assert agent.agent() is sentinel

    @pytest.mark.asyncio
    async def test_get_agent_instructions_renders_template(self):
        """Lines 69-71: _get_agent_instructions renders Jinja2 template with context."""
        context = SimpleNamespace(
            property_id="prop-1",
            prospect_id="prospect-1",
            current_time=datetime(2025, 6, 15, 10, 30),
            ask_request=SimpleNamespace(product="simple"),
        )

        with (
            patch(
                "agent_leasing.agent.simple.agent.settings",
            ) as mock_settings,
            patch(
                "agent_leasing.agent.simple.agent.MCPServerStreamableHttp",
            ),
        ):
            mock_settings.knock_mcp_server = "http://localhost:9999"

            from agent_leasing.agent.simple.agent import SimpleAgent

            agent = SimpleAgent(context)

        run_context = MagicMock(spec=RunContextWrapper)
        run_context.context = context

        mock_agent_arg = MagicMock()

        result = await agent._get_agent_instructions(run_context, mock_agent_arg)

        # The template should have rendered with context values
        assert "prop-1" in result
        assert "prospect-1" in result
        assert isinstance(result, str)
        assert len(result) > 0
