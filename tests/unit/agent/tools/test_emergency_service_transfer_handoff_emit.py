"""Verify each emergency_service_transfer_* variant emits a handoff
TaskActivityEvent with reason=EMERGENCY only on confirmed-success paths.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_leasing.agent.tools.emergency_service_transfer.advanced import emergency_service_transfer_advanced as adv
from agent_leasing.agent.tools.emergency_service_transfer.basic import emergency_service_transfer_basic as basic
from agent_leasing.agent.tools.emergency_service_transfer.rpcc import emergency_service_transfer_rpcc as rpcc
from agent_leasing.api.model import HandoffReasonCode
from agent_leasing.kafka.task_activity.extractors import extract_handoff_events

# A known-valid US E.164 phone — phonenumbers.is_valid_number requires NANP
# format with a real area code; "+15555555555" looks valid but isn't.
VALID_US_PHONE = "+12025551234"


def _ctx(channel="VOICE"):
    ctx = MagicMock()
    ctx.context.disabled_modules = []
    ctx.context.call_management_in_progress = False
    ctx.context.handoff_in_progress = False
    ctx.context.esr_initiated = False
    ctx.context.esr_phone_retry_attempted = False
    ctx.context.ask_request.product = f"resident_one_{channel.lower()}"
    ctx.context.ask_request.product_info.uc_company_id.id = "uc-co"
    ctx.context.ask_request.product_info.uc_property_id.id = "uc-pr"
    ctx.context.ask_request.product_info.call_sid = "CA-1"
    ctx.context.ask_request.product_info.emerg_phone = VALID_US_PHONE
    ctx.context.ask_request.product_info.lo_property_id = "lo-1"
    ctx.context.ask_request.product_info.caller = VALID_US_PHONE
    ctx.context.ask_request.product_info.callee = VALID_US_PHONE
    return ctx


class TestEsrBasicEmit:
    @pytest.mark.asyncio
    @patch.object(basic, "redirect_to_number_via_twilio", new_callable=AsyncMock)
    @patch.object(basic, "get_call_state_from_context", return_value=None)
    @patch.object(basic, "get_channel_from_context", return_value="VOICE")
    @patch.object(basic, "publish_task_activity")
    async def test_voice_emits_emergency_with_llm_summary(self, mock_publish, _channel, _call_state, _twilio):
        ctx = _ctx("VOICE")
        result = await basic._emergency_service_transfer_basic_impl(
            ctx,
            already_created_emergency_service_request=True,
            service_request_summary="Water leaking through ceiling in unit 304",
        )

        assert "redirected" in result
        mock_publish.assert_called_once()
        args, kwargs = mock_publish.call_args
        assert args[0] is extract_handoff_events
        # The LLM-provided summary flows through to the activity event AND
        # to handoff_result (mirrors ESR Advanced/RPCC).
        assert args[1] == "Water leaking through ceiling in unit 304"
        assert kwargs["reason"] == HandoffReasonCode.EMERGENCY
        hr = ctx.context.handoff_result
        assert hr.tool == "emergency_service_transfer_basic"
        assert hr.reason == "EMERGENCY"
        assert hr.routing_confirmed is True
        assert hr.summary == "Water leaking through ceiling in unit 304"

    @pytest.mark.asyncio
    @patch.object(basic, "get_emergency_number", new_callable=AsyncMock, return_value=None)
    @patch.object(basic, "get_books_ids", new_callable=AsyncMock, return_value=("c", "p"))
    @patch.object(basic, "get_channel_from_context", return_value="VOICE")
    @patch.object(basic, "publish_task_activity")
    async def test_no_emit_when_emergency_number_missing(self, mock_publish, _channel, _ids, _emerg):
        ctx = _ctx("VOICE")
        ctx.context.ask_request.product_info.emerg_phone = None

        result = await basic._emergency_service_transfer_basic_impl(
            ctx,
            already_created_emergency_service_request=True,
            service_request_summary="Water leak",
        )

        assert result == basic.EMERGENCY_NOT_FOUND_ERROR
        mock_publish.assert_not_called()


class TestEsrAdvancedEmit:
    @pytest.mark.asyncio
    @patch.object(adv, "publish_task_activity")
    @patch.object(adv, "dispatch_to_emergency_service_transfer", new_callable=AsyncMock)
    @patch.object(adv, "get_channel_from_context", return_value="VOICE")
    async def test_emits_on_dispatch_success(self, _channel, mock_dispatch, mock_publish):
        mock_dispatch.return_value = adv.DISPATCH_SUCCESS_MESSAGE
        ctx = _ctx("VOICE")

        result = await adv._emergency_service_transfer_advanced_impl(
            ctx,
            called_create_service_request=True,
            already_played_voice_channel_transfer_message=True,
            resident_phone=VALID_US_PHONE,
            service_request_summary="Water leaking through ceiling",
        )

        assert result == adv.DISPATCH_SUCCESS_MESSAGE
        mock_publish.assert_called_once()
        args, kwargs = mock_publish.call_args
        assert args[0] is extract_handoff_events
        assert args[1] == "Water leaking through ceiling"
        assert kwargs["reason"] == HandoffReasonCode.EMERGENCY

    @pytest.mark.asyncio
    @patch.object(adv, "publish_task_activity")
    @patch.object(adv, "dispatch_to_emergency_service_transfer", new_callable=AsyncMock)
    @patch.object(adv, "get_channel_from_context", return_value="VOICE")
    async def test_no_emit_on_dispatch_error(self, _channel, mock_dispatch, mock_publish):
        mock_dispatch.return_value = adv.DISPATCH_ERROR_MESSAGE
        ctx = _ctx("VOICE")

        result = await adv._emergency_service_transfer_advanced_impl(
            ctx,
            called_create_service_request=True,
            already_played_voice_channel_transfer_message=True,
            resident_phone=VALID_US_PHONE,
            service_request_summary="Maybe an emergency",
        )

        assert result == adv.DISPATCH_ERROR_MESSAGE
        mock_publish.assert_not_called()


class TestEsrRpccEmit:
    @pytest.mark.asyncio
    @patch.object(rpcc, "_get_twilio_client")
    @patch.object(rpcc, "get_call_state_from_context", return_value=None)
    @patch.object(rpcc, "get_channel_from_context", return_value="VOICE")
    @patch.object(rpcc, "publish_task_activity")
    async def test_voice_emits_on_sip_transfer(self, mock_publish, _channel, _call_state, mock_twilio_client):
        mock_twilio_client.return_value.calls.return_value.update = MagicMock()
        ctx = _ctx("VOICE")
        with patch.object(rpcc.settings, "rpcc_sip_endpoint", "sip:rpcc@example"):
            result = await rpcc._emergency_service_transfer_rpcc_impl(ctx, service_request_summary="Gas smell in unit")

        assert result == rpcc.VOICE_TRANSFER_SUCCESS
        mock_publish.assert_called_once()
        args, kwargs = mock_publish.call_args
        assert args[0] is extract_handoff_events
        assert kwargs["reason"] == HandoffReasonCode.EMERGENCY
