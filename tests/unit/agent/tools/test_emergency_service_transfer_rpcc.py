"""Unit tests for emergency_service_transfer_rpcc tool."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import unquote

import pytest
from agents import RunContextWrapper
from agents.tool_context import ToolContext
from openai.types.responses import ResponseFunctionToolCall

from agent_leasing.agent.tools.emergency_service_transfer.rpcc import (
    emergency_service_transfer_rpcc as estr_module,
)
from agent_leasing.agent.tools.emergency_service_transfer.rpcc.emergency_service_transfer_rpcc import (
    DISPATCH_ERROR_MESSAGE,
    MAX_SIP_SUMMARY_LENGTH,
    NONVOICE_TRANSFER_SUCCESS,
    VOICE_TRANSFER_SUCCESS,
    _build_sip_transfer_twiml,
    _build_sip_uri,
    _sanitize_summary_for_sip_header,
    parse_and_validate_phone_number,
)
from agent_leasing.util.call_state_manager import PlaybackWaitResult

MODULE_PATH = "agent_leasing.agent.tools.emergency_service_transfer.rpcc.emergency_service_transfer_rpcc"

SIP_ENDPOINT = "sip:+12002220040@sip-trunk-rpcc-gc-umatilla.byoc.usw2.pure.cloud"


def _build_rpcc_context(
    channel,
    call_sid="CA1234567890",
    lo_property_id="27504",
    callback_number="+12025551234",
    callee="+18005550100",
    caller="+18643866590",
):
    """Build a minimal context for RPCC tool tests."""
    CHANNEL_TO_PRODUCT = {
        "VOICE": "resident_one_voice",
        "SMS": "resident_one_sms",
        "EMAIL": "resident_one_email",
        "CHAT": "resident_one_chat",
    }

    mock_context = Mock()
    mock_context.ask_request.product = CHANNEL_TO_PRODUCT.get(channel, "resident_one_chat")
    mock_context.ask_request.product_info.lo_property_id = lo_property_id
    mock_context.ask_request.product_info.call_sid = call_sid
    mock_context.ask_request.product_info.callee = callee
    mock_context.ask_request.product_info.caller = caller
    mock_context.ask_request.callback_number = callback_number
    mock_context.langsmith_run_tree = None
    mock_context.disabled_modules = []
    mock_context.call_state_manager = None
    mock_context.esr_phone_retry_attempted = False

    tool_call = ResponseFunctionToolCall(
        arguments="{}",
        call_id="test-call-id",
        name="emergency_service_transfer_rpcc",
        type="function_call",
    )
    mock_tool_ctx = ToolContext.from_agent_context(
        RunContextWrapper(context=mock_context),
        tool_call_id="test-call-id",
        tool_call=tool_call,
    )

    json_input = json.dumps({"service_request_summary": "Emergency: Water leak in unit 204"})

    return mock_context, mock_tool_ctx, json_input


def _build_rpcc_context_with_phone(channel, resident_phone="+12025559999", **kwargs):
    """Build context and json_input that includes resident_phone for non-voice."""
    mock_context, mock_tool_ctx, _ = _build_rpcc_context(channel, **kwargs)
    json_input = json.dumps(
        {
            "service_request_summary": "Emergency: Water leak in unit 204",
            "resident_phone": resident_phone,
        }
    )
    return mock_context, mock_tool_ctx, json_input


class TestBuildSipUri:
    """Tests for SIP URI builder."""

    def test_basic_uri_structure(self):
        uri = _build_sip_uri(SIP_ENDPOINT, "27504", "8643866590", "9728203231", "Test summary")
        assert uri.startswith(SIP_ENDPOINT + "?X-User-to-User=")

    def test_pipe_delimited_fields(self):
        uri = _build_sip_uri(SIP_ENDPOINT, "27504", "8643866590", "9728203231", "Test summary")
        # Decode the query value to check the pipe-delimited fields
        encoded_value = uri.split("X-User-to-User=")[1]
        decoded = unquote(encoded_value)
        assert decoded == "27504|8643866590|9728203231|Test summary;encoding=ascii"

    def test_special_characters_encoded(self):
        uri = _build_sip_uri(
            SIP_ENDPOINT, "27504", "8643866590", "9728203231", "My refrigerator is making a terrible sound"
        )
        encoded_value = uri.split("X-User-to-User=")[1]
        # Pipes, spaces, and semicolons should be URL-encoded
        assert "%7C" in encoded_value  # pipes
        assert "%20" in encoded_value or "+" in encoded_value  # spaces

    def test_matches_kevins_qa_example(self):
        """Verify output matches the exact QA example from Kevin Keehan."""
        uri = _build_sip_uri(
            SIP_ENDPOINT,
            "27504",
            "8643866590",
            "9728203231",
            "My refrigerator is making a terrible sound and driving me crazy",
        )
        expected = (
            "sip:+12002220040@sip-trunk-rpcc-gc-umatilla.byoc.usw2.pure.cloud"
            "?X-User-to-User=27504%7C8643866590%7C9728203231%7C"
            "My%20refrigerator%20is%20making%20a%20terrible%20sound%20and%20driving%20me%20crazy"
            "%3Bencoding%3Dascii"
        )
        assert uri == expected

    def test_encoding_ascii_suffix(self):
        uri = _build_sip_uri(SIP_ENDPOINT, "1", "2", "3", "test")
        decoded = unquote(uri.split("X-User-to-User=")[1])
        assert decoded.endswith(";encoding=ascii")

    def test_summary_sanitizes_reserved_delimiters(self):
        uri = _build_sip_uri(SIP_ENDPOINT, "27504", "8643866590", "9728203231", "Leak | kitchen; urgent")
        decoded = unquote(uri.split("X-User-to-User=")[1])
        assert decoded == "27504|8643866590|9728203231|Leak / kitchen, urgent;encoding=ascii"
        assert decoded.count("|") == 3

    def test_summary_normalizes_ascii_and_whitespace(self):
        sanitized = _sanitize_summary_for_sip_header("  Fuite\nà la cuisine 🚨\t ")
        assert sanitized == "Fuite a la cuisine"

    def test_summary_truncated_before_encoding(self):
        uri = _build_sip_uri(
            SIP_ENDPOINT,
            "27504",
            "8643866590",
            "9728203231",
            "a" * (MAX_SIP_SUMMARY_LENGTH + 25),
        )
        decoded = unquote(uri.split("X-User-to-User=")[1])
        summary = decoded.removesuffix(";encoding=ascii").split("|", maxsplit=3)[3]
        assert len(summary) == MAX_SIP_SUMMARY_LENGTH


class TestBuildSipTransferTwiml:
    """Tests for SIP TwiML builder."""

    def test_contains_dial_sip(self):
        twiml = _build_sip_transfer_twiml("sip:test@example.com")
        assert "<Dial><Sip>sip:test@example.com</Sip></Dial>" in twiml

    def test_contains_pause(self):
        twiml = _build_sip_transfer_twiml("sip:test@example.com")
        assert '<Pause length="1"/>' in twiml

    def test_wrapped_in_response(self):
        twiml = _build_sip_transfer_twiml("sip:test@example.com")
        assert twiml.startswith("<Response>")
        assert twiml.endswith("</Response>")


class TestParseAndValidatePhoneNumber:
    """Tests for phone validation helper."""

    def test_valid_e164(self):
        parsed = parse_and_validate_phone_number("+12025551234")
        assert str(parsed.national_number) == "2025551234"
        assert parsed.country_code == 1

    def test_valid_10_digit(self):
        parsed = parse_and_validate_phone_number("2025551234")
        assert str(parsed.national_number) == "2025551234"

    def test_invalid_number_raises(self):
        with pytest.raises(ValueError):
            parse_and_validate_phone_number("12345")

    def test_garbage_input_raises(self):
        with pytest.raises(ValueError):
            parse_and_validate_phone_number("not-a-phone")


class TestVoiceUnparseableMetadataDoesNotBlockTransfer:
    """Voice path: caller/callee in the SIP header are informational; parse failures shouldn't block."""

    @pytest.mark.asyncio
    async def test_voice_transfers_when_callee_unparseable(self):
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context("VOICE", callee="not-a-phone")

        mock_twilio_client = Mock()
        mock_twilio_client.calls.return_value = Mock()

        with (
            patch(f"{MODULE_PATH}.get_call_state_from_context", return_value=None),
            patch(f"{MODULE_PATH}.get_twilio_credentials", return_value=("k", "s", "a")),
            patch(f"{MODULE_PATH}.TwilioClient", return_value=mock_twilio_client),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            result = await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert result == VOICE_TRANSFER_SUCCESS
        # Raw value falls through into the SIP header
        twiml = mock_twilio_client.calls.return_value.update.call_args.kwargs["twiml"]
        assert "not-a-phone" in unquote(twiml)

    @pytest.mark.asyncio
    async def test_voice_transfers_when_caller_unparseable(self):
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context("VOICE", caller="garbage")

        mock_twilio_client = Mock()
        mock_twilio_client.calls.return_value = Mock()

        with (
            patch(f"{MODULE_PATH}.get_call_state_from_context", return_value=None),
            patch(f"{MODULE_PATH}.get_twilio_credentials", return_value=("k", "s", "a")),
            patch(f"{MODULE_PATH}.TwilioClient", return_value=mock_twilio_client),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            result = await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert result == VOICE_TRANSFER_SUCCESS


class TestVoiceTransfer:
    """Tests for voice path — SIP transfer on live call."""

    @pytest.mark.asyncio
    async def test_voice_transfers_via_sip(self):
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context("VOICE")

        mock_twilio_client = Mock()
        mock_calls_instance = Mock()
        mock_twilio_client.calls.return_value = mock_calls_instance

        with (
            patch(f"{MODULE_PATH}.get_call_state_from_context", return_value=None),
            patch(f"{MODULE_PATH}.get_twilio_credentials", return_value=("k", "s", "a")),
            patch(f"{MODULE_PATH}.TwilioClient", return_value=mock_twilio_client),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            result = await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert result == VOICE_TRANSFER_SUCCESS
        mock_twilio_client.calls.assert_called_once_with("CA1234567890")
        twiml = mock_calls_instance.update.call_args.kwargs["twiml"]
        assert "<Dial><Sip>" in twiml
        assert SIP_ENDPOINT in twiml

    @pytest.mark.asyncio
    async def test_voice_twiml_contains_sip_uri_with_metadata(self):
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context(
            "VOICE", lo_property_id="99999", caller="+18643866590", callee="+19728203231"
        )

        mock_twilio_client = Mock()
        mock_calls_instance = Mock()
        mock_twilio_client.calls.return_value = mock_calls_instance

        with (
            patch(f"{MODULE_PATH}.get_call_state_from_context", return_value=None),
            patch(f"{MODULE_PATH}.get_twilio_credentials", return_value=("k", "s", "a")),
            patch(f"{MODULE_PATH}.TwilioClient", return_value=mock_twilio_client),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        twiml = mock_calls_instance.update.call_args.kwargs["twiml"]
        # Decode the SIP URI from inside the TwiML and verify fields
        sip_start = twiml.index("<Sip>") + len("<Sip>")
        sip_end = twiml.index("</Sip>")
        sip_uri = twiml[sip_start:sip_end]
        decoded = unquote(sip_uri.split("X-User-to-User=")[1])
        parts = decoded.replace(";encoding=ascii", "").split("|")
        assert parts[0] == "99999"  # PropertyID
        assert parts[1] == "8643866590"  # Caller (resident) national number
        assert parts[2] == "9728203231"  # Callee (property) national number

    @pytest.mark.asyncio
    async def test_voice_missing_call_sid_returns_error(self):
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context("VOICE", call_sid=None)

        with patch(f"{MODULE_PATH}.get_call_state_from_context", return_value=None):
            result = await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert result == DISPATCH_ERROR_MESSAGE

    @pytest.mark.asyncio
    async def test_voice_no_sip_endpoint_returns_error(self):
        """RPCC SIP endpoint must be explicitly configured per environment."""
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context("VOICE")

        with (
            patch(f"{MODULE_PATH}.get_call_state_from_context", return_value=None),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = ""
            result = await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert result == DISPATCH_ERROR_MESSAGE

    @pytest.mark.asyncio
    async def test_voice_missing_caller_returns_error(self):
        """Caller identifies the resident to RPCC — required for the transfer."""
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context("VOICE", caller=None)

        with patch(f"{MODULE_PATH}.get_call_state_from_context", return_value=None):
            result = await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert result == DISPATCH_ERROR_MESSAGE

    @pytest.mark.asyncio
    async def test_voice_missing_callee_still_transfers_with_empty_marker(self):
        """Callee is informational SIP-header metadata; missing value -> "empty" but transfer proceeds."""
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context("VOICE", callee=None)

        mock_twilio_client = Mock()
        mock_twilio_client.calls.return_value = Mock()

        with (
            patch(f"{MODULE_PATH}.get_call_state_from_context", return_value=None),
            patch(f"{MODULE_PATH}.get_twilio_credentials", return_value=("k", "s", "a")),
            patch(f"{MODULE_PATH}.TwilioClient", return_value=mock_twilio_client),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            result = await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert result == VOICE_TRANSFER_SUCCESS
        twiml = mock_twilio_client.calls.return_value.update.call_args.kwargs["twiml"]
        decoded = unquote(twiml)
        assert "empty" in decoded

    @pytest.mark.asyncio
    async def test_voice_twilio_failure_returns_error(self):
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context("VOICE")

        mock_twilio_client = Mock()
        mock_twilio_client.calls.return_value.update.side_effect = Exception("Twilio API error")

        with (
            patch(f"{MODULE_PATH}.get_call_state_from_context", return_value=None),
            patch(f"{MODULE_PATH}.get_twilio_credentials", return_value=("k", "s", "a")),
            patch(f"{MODULE_PATH}.TwilioClient", return_value=mock_twilio_client),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            result = await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert "Failed to transfer to RPCC maintenance team" in result
        assert "escalate" in result.lower()
        # esr_initiated must be reset on failure so interrupt suppression isn't stuck on
        assert mock_context.esr_initiated is False
        # The failed handoff is recorded for the session-end task-event payload.
        assert mock_context.handoff_result is not None
        assert mock_context.handoff_result.tool == "emergency_service_transfer_rpcc"
        assert mock_context.handoff_result.reason == "EMERGENCY"
        assert mock_context.handoff_result.routing_confirmed is False

    @pytest.mark.asyncio
    async def test_voice_success_resets_esr_initiated(self):
        """After a successful transfer, esr_initiated must reset so later turns aren't suppressed."""
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context("VOICE")

        mock_twilio_client = Mock()
        mock_twilio_client.calls.return_value = Mock()

        with (
            patch(f"{MODULE_PATH}.get_call_state_from_context", return_value=None),
            patch(f"{MODULE_PATH}.get_twilio_credentials", return_value=("k", "s", "a")),
            patch(f"{MODULE_PATH}.TwilioClient", return_value=mock_twilio_client),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert mock_context.esr_initiated is False

    @pytest.mark.asyncio
    async def test_voice_sets_call_ended_by_agent(self):
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context("VOICE")

        mock_twilio_client = Mock()
        mock_twilio_client.calls.return_value = Mock()

        with (
            patch(f"{MODULE_PATH}.get_call_state_from_context", return_value=None),
            patch(f"{MODULE_PATH}.get_twilio_credentials", return_value=("k", "s", "a")),
            patch(f"{MODULE_PATH}.TwilioClient", return_value=mock_twilio_client),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert mock_context.call_ended_by_agent is True

    @pytest.mark.asyncio
    async def test_voice_sets_esr_initiated_during_playback_wait(self):
        """Voice path should set esr_initiated=True before the playback wait so suppression activates."""
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context("VOICE")
        mock_context.esr_initiated = False

        # Capture the state of esr_initiated at the moment wait_for_message_playback is called
        observed = {}

        async def capture_state(*args, **kwargs):
            observed["esr_initiated"] = mock_context.esr_initiated
            return PlaybackWaitResult(success=True, started=True, completed=True)

        call_state = AsyncMock()
        call_state.wait_for_message_playback = AsyncMock(side_effect=capture_state)

        mock_twilio_client = Mock()
        mock_twilio_client.calls.return_value = Mock()

        with (
            patch(f"{MODULE_PATH}.get_call_state_from_context", return_value=call_state),
            patch(f"{MODULE_PATH}.get_twilio_credentials", return_value=("k", "s", "a")),
            patch(f"{MODULE_PATH}.TwilioClient", return_value=mock_twilio_client),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert observed["esr_initiated"] is True

    @pytest.mark.asyncio
    async def test_voice_does_not_use_calls_create(self):
        """Voice path should update existing call, not create a new one."""
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context("VOICE")

        mock_twilio_client = Mock()
        mock_twilio_client.calls.return_value = Mock()

        with (
            patch(f"{MODULE_PATH}.get_call_state_from_context", return_value=None),
            patch(f"{MODULE_PATH}.get_twilio_credentials", return_value=("k", "s", "a")),
            patch(f"{MODULE_PATH}.TwilioClient", return_value=mock_twilio_client),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        mock_twilio_client.calls.create.assert_not_called()


class TestNonVoiceTransfer:
    """Tests for non-voice path — outbound call to resident via calls.create() with SIP TwiML."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("channel", ["SMS", "EMAIL", "CHAT"])
    async def test_initiates_outbound_call_with_sip_twiml(self, channel):
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context_with_phone(channel)

        mock_twilio_client = Mock()

        with (
            patch(f"{MODULE_PATH}.get_twilio_credentials", return_value=("k", "s", "a")),
            patch(f"{MODULE_PATH}.TwilioClient", return_value=mock_twilio_client),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            result = await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert result == NONVOICE_TRANSFER_SUCCESS
        mock_twilio_client.calls.create.assert_called_once()
        create_kwargs = mock_twilio_client.calls.create.call_args.kwargs
        assert create_kwargs["to"] == "+12025559999"
        assert create_kwargs["from_"] == "+18005550100"  # callee from payload
        assert "twiml" in create_kwargs
        assert "<Dial><Sip>" in create_kwargs["twiml"]
        # Should NOT use url= anymore
        assert "url" not in create_kwargs

    @pytest.mark.asyncio
    @pytest.mark.parametrize("channel", ["SMS", "EMAIL", "CHAT"])
    async def test_nonvoice_sip_uri_uses_resident_phone_as_resident_number(self, channel):
        """ResidentNumber in SIP URI should be the validated callback phone, not product_info.caller."""
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context_with_phone(
            channel, resident_phone="+12025559999", callee="+19728203231"
        )

        mock_twilio_client = Mock()

        with (
            patch(f"{MODULE_PATH}.get_twilio_credentials", return_value=("k", "s", "a")),
            patch(f"{MODULE_PATH}.TwilioClient", return_value=mock_twilio_client),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        twiml = mock_twilio_client.calls.create.call_args.kwargs["twiml"]
        sip_start = twiml.index("<Sip>") + len("<Sip>")
        sip_end = twiml.index("</Sip>")
        sip_uri = twiml[sip_start:sip_end]
        decoded = unquote(sip_uri.split("X-User-to-User=")[1])
        parts = decoded.replace(";encoding=ascii", "").split("|")
        assert parts[1] == "2025559999"  # resident_phone national number

    @pytest.mark.asyncio
    @pytest.mark.parametrize("channel", ["SMS", "EMAIL", "CHAT"])
    async def test_does_not_update_existing_call(self, channel):
        """Non-voice should create a new call, not update an existing one."""
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context_with_phone(channel)

        mock_twilio_client = Mock()
        mock_calls_instance = Mock()
        mock_twilio_client.calls.return_value = mock_calls_instance

        with (
            patch(f"{MODULE_PATH}.get_twilio_credentials", return_value=("k", "s", "a")),
            patch(f"{MODULE_PATH}.TwilioClient", return_value=mock_twilio_client),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        # calls(sid).update() should NOT be called — only calls.create()
        mock_calls_instance.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_context_callback_number(self):
        """If resident_phone not provided, use callback_number from context."""
        mock_context, mock_tool_ctx, _ = _build_rpcc_context("SMS", callback_number="+12025557777")
        json_input = json.dumps({"service_request_summary": "Emergency: Gas leak"})

        mock_twilio_client = Mock()

        with (
            patch(f"{MODULE_PATH}.get_twilio_credentials", return_value=("k", "s", "a")),
            patch(f"{MODULE_PATH}.TwilioClient", return_value=mock_twilio_client),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            result = await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert result == NONVOICE_TRANSFER_SUCCESS
        create_kwargs = mock_twilio_client.calls.create.call_args.kwargs
        assert create_kwargs["to"] == "+12025557777"

    @pytest.mark.asyncio
    async def test_missing_phone_returns_error(self):
        """If no phone provided and no callback in context, return error."""
        mock_context, mock_tool_ctx, _ = _build_rpcc_context("SMS", callback_number=None)
        json_input = json.dumps({"service_request_summary": "Emergency"})

        result = await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert "callback phone number" in result.lower()

    @pytest.mark.asyncio
    async def test_missing_callee_returns_error(self):
        """If callee is missing from product_info, return dispatch error."""
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context_with_phone("SMS", callee=None)

        result = await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert result == DISPATCH_ERROR_MESSAGE

    @pytest.mark.asyncio
    async def test_invalid_phone_first_attempt_asks_to_retry(self):
        """First invalid phone should ask the agent to re-ask the resident."""
        mock_context, mock_tool_ctx, _ = _build_rpcc_context("SMS")
        json_input = json.dumps(
            {
                "service_request_summary": "Emergency",
                "resident_phone": "12345",  # Invalid
            }
        )

        result = await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert "not valid" in result.lower()
        assert "repeat" in result.lower()
        assert mock_context.esr_phone_retry_attempted is True
        assert mock_context.call_ended_by_agent is False

    @pytest.mark.asyncio
    async def test_invalid_phone_second_attempt_escalates(self):
        """Second invalid phone should escalate to a human teammate."""
        mock_context, mock_tool_ctx, _ = _build_rpcc_context("SMS")
        mock_context.esr_phone_retry_attempted = True  # First attempt already failed
        json_input = json.dumps(
            {
                "service_request_summary": "Emergency",
                "resident_phone": "12345",  # Invalid again
            }
        )

        result = await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert "failed twice" in result.lower()
        assert "escalate" in result.lower()

    @pytest.mark.asyncio
    async def test_nonvoice_twilio_failure_returns_error(self):
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context_with_phone("SMS")

        mock_twilio_client = Mock()
        mock_twilio_client.calls.create.side_effect = Exception("Twilio error")

        with (
            patch(f"{MODULE_PATH}.get_twilio_credentials", return_value=("k", "s", "a")),
            patch(f"{MODULE_PATH}.TwilioClient", return_value=mock_twilio_client),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            result = await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert "Failed to transfer to RPCC maintenance team" in result

    @pytest.mark.asyncio
    async def test_nonvoice_sets_call_ended_by_agent(self):
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context_with_phone("SMS")

        mock_twilio_client = Mock()

        with (
            patch(f"{MODULE_PATH}.get_twilio_credentials", return_value=("k", "s", "a")),
            patch(f"{MODULE_PATH}.TwilioClient", return_value=mock_twilio_client),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert mock_context.call_ended_by_agent is True


class TestRpccVoicePlayback:
    """Tests for voice-specific playback wait behavior."""

    @pytest.mark.asyncio
    async def test_voice_playback_incomplete_still_transfers(self):
        call_state = AsyncMock()
        call_state.wait_for_message_playback = AsyncMock(
            return_value=PlaybackWaitResult(success=True, started=True, completed=False)
        )
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context("VOICE")

        mock_twilio_client = Mock()
        mock_twilio_client.calls.return_value = Mock()

        with (
            patch(f"{MODULE_PATH}.get_call_state_from_context", return_value=call_state),
            patch(f"{MODULE_PATH}.get_twilio_credentials", return_value=("k", "s", "a")),
            patch(f"{MODULE_PATH}.TwilioClient", return_value=mock_twilio_client),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            result = await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert result == VOICE_TRANSFER_SUCCESS

    @pytest.mark.asyncio
    async def test_voice_no_call_state_still_transfers(self):
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context("VOICE")

        mock_twilio_client = Mock()
        mock_twilio_client.calls.return_value = Mock()

        with (
            patch(f"{MODULE_PATH}.get_call_state_from_context", return_value=None),
            patch(f"{MODULE_PATH}.get_twilio_credentials", return_value=("k", "s", "a")),
            patch(f"{MODULE_PATH}.TwilioClient", return_value=mock_twilio_client),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            result = await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert result == VOICE_TRANSFER_SUCCESS


class TestCancelledError:
    """Test CancelledError handling (call ended by user mid-transfer)."""

    @pytest.mark.asyncio
    async def test_cancelled_error_returns_message(self):
        mock_context, mock_tool_ctx, json_input = _build_rpcc_context("VOICE")

        with (
            patch(f"{MODULE_PATH}.get_call_state_from_context", return_value=None),
            patch(f"{MODULE_PATH}.get_twilio_credentials", side_effect=asyncio.CancelledError()),
            patch(f"{MODULE_PATH}.settings") as mock_settings,
        ):
            mock_settings.rpcc_sip_endpoint = SIP_ENDPOINT
            result = await estr_module.emergency_service_transfer_rpcc.on_invoke_tool(mock_tool_ctx, json_input)

        assert "cancelled" in result.lower()
        # esr_initiated must be reset on cancel (try/finally guarantee)
        assert mock_context.esr_initiated is False


class TestGetEmergencyServiceTransferRpccFxn:
    """Tests for the factory function."""

    def test_returns_function_tool_with_rendered_description(self):
        tool = estr_module.get_emergency_service_transfer_rpcc_fxn(context=None)
        assert tool.description == estr_module._DESCRIPTION_TEMPLATE

    def test_returns_copy_not_original(self):
        tool = estr_module.get_emergency_service_transfer_rpcc_fxn(context=None)
        assert tool is not estr_module.emergency_service_transfer_rpcc
