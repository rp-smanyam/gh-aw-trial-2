"""Tests for voice/lifecycle/setup.py — validation-failure transfer + trace marker."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from agent_leasing.voice.lifecycle.setup import transfer_call_on_validation_failure


class TestTransferCallOnValidationFailure:
    @pytest.fixture(autouse=True)
    def _patch_twilio(self):
        with (
            patch("agent_leasing.voice.lifecycle.setup.get_twilio_credentials", return_value=("k", "s", "a")),
            patch("agent_leasing.voice.lifecycle.setup.TwilioClient") as mock_client,
            patch("agent_leasing.voice.lifecycle.setup._build_transfer_twiml", return_value=""),
        ):
            mock_client.return_value.calls.return_value.update = AsyncMock()
            yield

    async def test_posts_trace_marker_with_source_signals(self):
        """Validation failure posts a `validation_failure` trace marker with source signals (issue #1567)."""
        mock_root_run = Mock()
        mock_child = Mock()
        mock_root_run.create_child.return_value = mock_child

        error = ValueError(
            "Value error, Missing required fields for resident persona: "
            "product_info.uc_company_id, product_info.ab_resident_id [type=value_error]"
        )
        payload = {
            "product": "resident_one_voice",
            "call_sid": "CAv2test",
            "product_info": {"call_sid": "CAv2test", "caller": "+15551112222", "account_sid": "ACv2"},
        }

        await transfer_call_on_validation_failure(error, payload, "CAv2test", root_run=mock_root_run, variant="v2")

        mock_root_run.create_child.assert_called_once()
        kwargs = mock_root_run.create_child.call_args.kwargs
        assert kwargs["name"] == "validation_failure"
        inputs = kwargs["inputs"]
        assert inputs["validation_reason"] == "missing_required_fields"
        assert inputs["missing_fields"] == ["product_info.uc_company_id", "product_info.ab_resident_id"]
        assert inputs["call_sid"] == "CAv2test"
        assert inputs["caller"] == "+15551112222"
        assert inputs["account_sid"] == "ACv2"
        assert inputs["voice_handler_variant"] == "v2"
        mock_child.post.assert_called_once()

    async def test_no_root_run_skips_marker(self):
        """When root_run is None, marker is silently skipped (no crash)."""
        error = ValueError("Missing required fields: product_info.uc_company_id")
        payload = {"product": "resident_one_voice", "product_info": {}}

        await transfer_call_on_validation_failure(error, payload, "CAtest", root_run=None, variant="v2")
