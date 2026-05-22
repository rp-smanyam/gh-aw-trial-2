"""Test cases for SMS consent handling in _handle_active_handoff function."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_leasing.api.model import AskRequest, AskResponse, ProductInfo, UCReference
from agent_leasing.server import _handle_active_handoff
from agent_leasing.util.sms_consent import GateResult


@pytest.fixture
def sms_handoff_request():
    """Create a sample SMS request for handoff testing."""
    return AskRequest(
        product="resident_one_sms",
        prompt="I need help with my rent",
        chat_session_id="test-session-123",
        product_info=ProductInfo(
            source="KNCK",
            knock_property_id="21759",
            knock_resident_id="12338",
            uc_company_id=UCReference(id="7643280", source="OS"),
            uc_property_id=UCReference(id="7643325", source="OS"),
            uc_resident_household_id=UCReference(id="12291", source="OS"),
            uc_resident_member_id=UCReference(id="12338", source="OS"),
            ab_resident_id=UCReference(id="5376834", source="AB"),
            uc_lease_id=UCReference(id="9999", source="OS"),
            uc_portal_base_url="https://testproperty.loftliving.com",
        ),
    )


@pytest.fixture
def email_handoff_request():
    """Create a sample EMAIL request for handoff testing."""
    return AskRequest(
        product="resident_one_email",
        prompt="I need help with my rent",
        chat_session_id="test-session-456",
        product_info=ProductInfo(
            source="KNCK",
            knock_property_id="21759",
            knock_resident_id="12338",
            uc_company_id=UCReference(id="7643280", source="OS"),
            uc_property_id=UCReference(id="7643325", source="OS"),
            uc_resident_household_id=UCReference(id="12291", source="OS"),
            uc_resident_member_id=UCReference(id="12338", source="OS"),
            ab_resident_id=UCReference(id="5376834", source="AB"),
            uc_lease_id=UCReference(id="9999", source="OS"),
            uc_portal_base_url="https://testproperty.loftliving.com",
        ),
    )


@pytest.fixture
def mock_context():
    """Create a mock SessionScope context."""
    context = MagicMock()
    context.sms_consent_status = "granted"
    context.language_code = "en"
    return context


class TestHandleActiveHandoffSmsConsent:
    """Test SMS consent checking in _handle_active_handoff function."""

    @pytest.mark.asyncio
    @patch("agent_leasing.server.is_handoff_active", new_callable=AsyncMock)
    async def test_non_sms_channel_returns_none(self, mock_is_handoff_active, email_handoff_request):
        """Test that non-SMS/EMAIL channels return None immediately."""
        chat_request = email_handoff_request
        chat_request.product = "resident_one_chat"

        result = await _handle_active_handoff(chat_request)

        assert result is None
        mock_is_handoff_active.assert_not_called()

    @pytest.mark.asyncio
    @patch("agent_leasing.server.is_handoff_active", new_callable=AsyncMock)
    async def test_handoff_not_active_returns_none(self, mock_is_handoff_active, sms_handoff_request):
        """Test that when handoff is not active, function returns None."""
        mock_is_handoff_active.return_value = False

        result = await _handle_active_handoff(sms_handoff_request)

        assert result is None
        mock_is_handoff_active.assert_called_once_with(
            sms_handoff_request.product,
            "21759",
            "12338",
            "5376834",
        )

    @pytest.mark.asyncio
    @patch("agent_leasing.server.handle_sms_consent_gate", new_callable=AsyncMock)
    @patch("agent_leasing.server.CachingMCPServer")
    @patch("agent_leasing.server.is_handoff_active", new_callable=AsyncMock)
    async def test_sms_consent_not_granted_returns_consent_message(
        self,
        mock_is_handoff_active,
        mock_caching_mcp_server_cls,
        mock_handle_gate,
        sms_handoff_request,
        mock_context,
    ):
        """Test that when SMS consent is not granted, consent message is returned."""
        mock_is_handoff_active.return_value = True
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.__aenter__ = AsyncMock(return_value=mock_mcp_instance)
        mock_mcp_instance.__aexit__ = AsyncMock(return_value=None)
        mock_caching_mcp_server_cls.return_value = mock_mcp_instance
        mock_handle_gate.return_value = GateResult(
            action="return_message",
            message="You are not opted in to SMS. To opt in, reply START or to opt out, reply STOP.",
        )

        result = await _handle_active_handoff(sms_handoff_request, mock_context)

        assert result is not None
        assert isinstance(result, AskResponse)
        assert result.flow_name == "SMS_CONSENT_FLOW"
        assert result.metadata == {"sms_consent_required": True}
        assert result.request_id == sms_handoff_request.request_id
        assert result.chat_session_id == sms_handoff_request.chat_session_id

        chat_content = json.loads(result.content.chat)
        assert "not opted in" in chat_content["response"]
        mock_handle_gate.assert_called_once()

    @pytest.mark.asyncio
    @patch("agent_leasing.server.handle_sms_consent_gate", new_callable=AsyncMock)
    @patch("agent_leasing.server.CachingMCPServer")
    @patch("agent_leasing.server.is_handoff_active", new_callable=AsyncMock)
    async def test_sms_consent_granted_returns_handoff_message(
        self,
        mock_is_handoff_active,
        mock_caching_mcp_server_cls,
        mock_handle_gate,
        sms_handoff_request,
        mock_context,
    ):
        """Test that when SMS consent is granted, normal handoff message is returned."""
        mock_is_handoff_active.return_value = True
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.__aenter__ = AsyncMock(return_value=mock_mcp_instance)
        mock_mcp_instance.__aexit__ = AsyncMock(return_value=None)
        mock_caching_mcp_server_cls.return_value = mock_mcp_instance
        mock_handle_gate.return_value = GateResult(action="proceed", message=None)

        result = await _handle_active_handoff(sms_handoff_request, mock_context)

        assert result is not None
        assert isinstance(result, AskResponse)
        assert result.flow_name == "HANDOFF_TO_HUMAN_FLOW"
        assert result.metadata == {"human_handoff": True}

        chat_content = json.loads(result.content.chat)
        assert "Thanks for reaching out" in chat_content["response"]
        assert "notified the property staff" in chat_content["response"]
        mock_handle_gate.assert_called_once()

    @pytest.mark.asyncio
    @patch("agent_leasing.server.handle_sms_consent_gate", new_callable=AsyncMock)
    @patch("agent_leasing.server.CachingMCPServer")
    @patch("agent_leasing.server.is_handoff_active", new_callable=AsyncMock)
    async def test_sms_consent_revoked_returns_opt_out_message(
        self,
        mock_is_handoff_active,
        mock_caching_mcp_server_cls,
        mock_handle_gate,
        sms_handoff_request,
        mock_context,
    ):
        """Test that when SMS consent is revoked, opt-out message is returned."""
        mock_is_handoff_active.return_value = True
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.__aenter__ = AsyncMock(return_value=mock_mcp_instance)
        mock_mcp_instance.__aexit__ = AsyncMock(return_value=None)
        mock_caching_mcp_server_cls.return_value = mock_mcp_instance
        mock_handle_gate.return_value = GateResult(
            action="return_message",
            message="You are opted out of SMS. To opt back in, reply START.",
        )

        result = await _handle_active_handoff(sms_handoff_request, mock_context)

        assert result is not None
        assert isinstance(result, AskResponse)
        assert result.flow_name == "SMS_CONSENT_FLOW"
        assert result.metadata == {"sms_consent_required": True}

        chat_content = json.loads(result.content.chat)
        assert "opted out" in chat_content["response"]
        assert "START" in chat_content["response"]

    @pytest.mark.asyncio
    @patch("agent_leasing.server.logger")
    @patch("agent_leasing.server.handle_sms_consent_gate", new_callable=AsyncMock)
    @patch("agent_leasing.server.CachingMCPServer")
    @patch("agent_leasing.server.is_handoff_active", new_callable=AsyncMock)
    async def test_sms_consent_check_exception_fails_closed(
        self,
        mock_is_handoff_active,
        mock_caching_mcp_server_cls,
        mock_handle_gate,
        mock_logger,
        sms_handoff_request,
        mock_context,
    ):
        """Test that when SMS consent check fails, no SMS content is sent (fail-closed)."""
        mock_is_handoff_active.return_value = True
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.__aenter__ = AsyncMock(return_value=mock_mcp_instance)
        mock_mcp_instance.__aexit__ = AsyncMock(return_value=None)
        mock_caching_mcp_server_cls.return_value = mock_mcp_instance
        mock_handle_gate.side_effect = Exception("MCP connection failed")

        result = await _handle_active_handoff(sms_handoff_request, mock_context)

        assert result is not None
        assert isinstance(result, AskResponse)
        assert result.content is None
        assert result.flow_name == "HANDOFF_TO_HUMAN_FLOW"
        assert result.metadata["human_handoff"] is True
        assert result.metadata["sms_consent_check_failed"] is True

        mock_logger.error.assert_called_once()
        call_args = mock_logger.error.call_args
        assert "Failed to check SMS consent for handoff" in call_args[0][0]
        assert call_args[1]["resident_id"] == "12338"

    @pytest.mark.asyncio
    @patch("agent_leasing.server.is_handoff_active", new_callable=AsyncMock)
    async def test_email_channel_skips_sms_consent_check(
        self,
        mock_is_handoff_active,
        email_handoff_request,
    ):
        """Test that EMAIL channel skips SMS consent check and returns handoff response."""
        mock_is_handoff_active.return_value = True

        result = await _handle_active_handoff(email_handoff_request)

        assert result is not None
        assert isinstance(result, AskResponse)
        assert result.flow_name == "HANDOFF_TO_HUMAN_FLOW"
        assert result.metadata == {"human_handoff": True, "email_route_back": True}
        assert result.content is None

    @pytest.mark.asyncio
    @patch("agent_leasing.server.handle_sms_consent_gate", new_callable=AsyncMock)
    @patch("agent_leasing.server.CachingMCPServer")
    @patch("agent_leasing.server.is_handoff_active", new_callable=AsyncMock)
    async def test_mcp_server_created_and_used_for_consent(
        self,
        mock_is_handoff_active,
        mock_caching_mcp_server_cls,
        mock_handle_gate,
        sms_handoff_request,
        mock_context,
    ):
        """Test that a CachingMCPServer is created and used for the consent check."""
        mock_is_handoff_active.return_value = True
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.__aenter__ = AsyncMock(return_value=mock_mcp_instance)
        mock_mcp_instance.__aexit__ = AsyncMock(return_value=None)
        mock_caching_mcp_server_cls.return_value = mock_mcp_instance
        mock_handle_gate.return_value = GateResult(action="proceed", message=None)

        await _handle_active_handoff(sms_handoff_request, mock_context)

        mock_caching_mcp_server_cls.assert_called_once()
        mock_handle_gate.assert_called_once_with(sms_handoff_request, mock_context, mock_mcp_instance)

    @pytest.mark.asyncio
    @patch("agent_leasing.server.handle_sms_consent_gate", new_callable=AsyncMock)
    @patch("agent_leasing.server.CachingMCPServer")
    @patch("agent_leasing.server.is_handoff_active", new_callable=AsyncMock)
    async def test_flow_id_generation(
        self,
        mock_is_handoff_active,
        mock_caching_mcp_server_cls,
        mock_handle_gate,
        sms_handoff_request,
        mock_context,
    ):
        """Test that flow_id is properly generated or reused."""
        mock_is_handoff_active.return_value = True
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.__aenter__ = AsyncMock(return_value=mock_mcp_instance)
        mock_mcp_instance.__aexit__ = AsyncMock(return_value=None)
        mock_caching_mcp_server_cls.return_value = mock_mcp_instance
        mock_handle_gate.return_value = GateResult(
            action="return_message",
            message="You are not opted in to SMS.",
        )

        sms_handoff_request.flow_id = "existing-flow-id-123"
        result = await _handle_active_handoff(sms_handoff_request, mock_context)
        assert result.flow_id == "existing-flow-id-123"

        sms_handoff_request.flow_id = None
        result = await _handle_active_handoff(sms_handoff_request, mock_context)
        assert result.flow_id is not None
        assert len(result.flow_id) == 36  # UUID format

    @pytest.mark.asyncio
    @patch("agent_leasing.server.logger")
    @patch("agent_leasing.server.handle_sms_consent_gate", new_callable=AsyncMock)
    @patch("agent_leasing.server.CachingMCPServer")
    @patch("agent_leasing.server.is_handoff_active", new_callable=AsyncMock)
    async def test_logging_for_consent_not_granted(
        self,
        mock_is_handoff_active,
        mock_caching_mcp_server_cls,
        mock_handle_gate,
        mock_logger,
        sms_handoff_request,
        mock_context,
    ):
        """Test that appropriate logging occurs when consent is not granted."""
        mock_is_handoff_active.return_value = True
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.__aenter__ = AsyncMock(return_value=mock_mcp_instance)
        mock_mcp_instance.__aexit__ = AsyncMock(return_value=None)
        mock_caching_mcp_server_cls.return_value = mock_mcp_instance
        mock_context.sms_consent_status = "revoked"
        mock_handle_gate.return_value = GateResult(
            action="return_message",
            message="You are opted out of SMS. To opt back in, reply START.",
        )

        await _handle_active_handoff(sms_handoff_request, mock_context)

        mock_logger.info.assert_called()
        log_calls = list(mock_logger.info.call_args_list)

        consent_log = next(
            (c for c in log_calls if "SMS consent not granted during handoff" in c[0][0]),
            None,
        )
        assert consent_log is not None
        assert consent_log[1]["sms_consent_status"] == "revoked"


class TestHandleActiveHandoffEmitsTaskActivity:
    """The short-circuit must publish an ALREADY_IN_HANDOFF TaskActivityEvent
    — the transfer tool isn't called in this branch, so this is the only
    place that produces the activity event."""

    @pytest.mark.asyncio
    @patch("agent_leasing.server.publish_task_activity")
    @patch("agent_leasing.server.is_handoff_active", new_callable=AsyncMock)
    async def test_email_short_circuit_emits_already_in_handoff(
        self, mock_is_handoff_active, mock_publish, email_handoff_request, mock_context
    ):
        from agent_leasing.api.model import HandoffReasonCode
        from agent_leasing.kafka.task_activity.extractors import extract_handoff_events

        mock_is_handoff_active.return_value = True

        result = await _handle_active_handoff(email_handoff_request, mock_context)

        assert result is not None
        mock_publish.assert_called_once()
        args, kwargs = mock_publish.call_args
        # Positional: extractor, prompt (transfer_message), context
        assert args[0] is extract_handoff_events
        assert args[1] == email_handoff_request.prompt
        assert args[2] is mock_context
        assert kwargs["reason"] == HandoffReasonCode.ALREADY_IN_HANDOFF
        # context.handoff_result is populated for the session-end task-event payload.
        hr = mock_context.handoff_result
        assert hr.tool == "_handle_active_handoff"
        assert hr.reason == "ALREADY_IN_HANDOFF"
        assert hr.routing_confirmed is True
        assert hr.summary == email_handoff_request.prompt

    @pytest.mark.asyncio
    @patch("agent_leasing.server.publish_task_activity")
    @patch("agent_leasing.server.handle_sms_consent_gate", new_callable=AsyncMock)
    @patch("agent_leasing.server.CachingMCPServer")
    @patch("agent_leasing.server.is_handoff_active", new_callable=AsyncMock)
    async def test_sms_consent_bounce_still_emits_already_in_handoff(
        self,
        mock_is_handoff_active,
        mock_caching_mcp_server_cls,
        mock_handle_gate,
        mock_publish,
        sms_handoff_request,
        mock_context,
    ):
        # The activity stream tracks "resident pinged during an active
        # handoff", which is true even when the response path bounces on
        # SMS consent. Emit must fire BEFORE the consent gate.
        from agent_leasing.api.model import HandoffReasonCode

        mock_is_handoff_active.return_value = True
        mock_mcp_instance = AsyncMock()
        mock_mcp_instance.__aenter__ = AsyncMock(return_value=mock_mcp_instance)
        mock_mcp_instance.__aexit__ = AsyncMock(return_value=None)
        mock_caching_mcp_server_cls.return_value = mock_mcp_instance
        mock_handle_gate.return_value = GateResult(
            action="return_message",
            message="You are not opted in to SMS. To opt in, reply START or to opt out, reply STOP.",
        )

        result = await _handle_active_handoff(sms_handoff_request, mock_context)

        # Response is the consent-bounce path (NOT the normal handoff message).
        assert result is not None
        assert result.flow_name == "SMS_CONSENT_FLOW"
        assert result.metadata == {"sms_consent_required": True}
        # But the activity emit STILL fires because the active-handoff state
        # is what the activity stream cares about.
        mock_publish.assert_called_once()
        _, kwargs = mock_publish.call_args
        assert kwargs["reason"] == HandoffReasonCode.ALREADY_IN_HANDOFF
        # And handoff_result is set so the session-end task-event picks it up.
        hr = mock_context.handoff_result
        assert hr is not None
        assert hr.reason == "ALREADY_IN_HANDOFF"

    @pytest.mark.asyncio
    @patch("agent_leasing.server.publish_task_activity")
    @patch("agent_leasing.server.is_handoff_active", new_callable=AsyncMock)
    async def test_no_emit_when_handoff_not_active(
        self, mock_is_handoff_active, mock_publish, sms_handoff_request, mock_context
    ):
        # When the handoff TTL has expired, there is no short-circuit and
        # therefore no event to emit.
        mock_is_handoff_active.return_value = False

        result = await _handle_active_handoff(sms_handoff_request, mock_context)

        assert result is None
        mock_publish.assert_not_called()

    @pytest.mark.asyncio
    @patch("agent_leasing.server.publish_task_activity")
    @patch("agent_leasing.server.is_handoff_active", new_callable=AsyncMock)
    async def test_no_emit_when_context_is_none(self, mock_is_handoff_active, mock_publish, email_handoff_request):
        # Defensive: legacy callers may pass context=None. We can't derive
        # a task_id without the SessionScope, so the emit is skipped.
        mock_is_handoff_active.return_value = True

        result = await _handle_active_handoff(email_handoff_request, None)

        assert result is not None
        mock_publish.assert_not_called()
