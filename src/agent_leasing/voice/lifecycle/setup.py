"""Call setup utilities — staff transfer helpers.

Transfer logic covering both payload validation failure and background
full-agent init failure.  Both paths redirect the call via the Knock
transfer endpoint using the Twilio REST API.  The main setup orchestration
(agent creation, session entry, greeting trigger) lives in ``VoiceHandler``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import langsmith as ls
import structlog
from twilio.rest import Client as TwilioClient

from agent_leasing.agent.tools.transfer_to_staff.transfer_to_staff_voice import (
    _build_transfer_twiml,
)
from agent_leasing.settings import settings
from agent_leasing.util.tracing_utils import (
    build_validation_failure_marker_inputs,
    post_trace_marker,
)
from agent_leasing.util.twilio_util import get_twilio_credentials

logger = structlog.get_logger(__name__)


async def transfer_call(call_sid: str, reason: str) -> None:
    """Transfer the call to a human agent via the Knock transfer endpoint."""
    if not call_sid:
        logger.error("Cannot transfer: call_sid not available", reason=reason)
        return

    try:
        api_key, api_secret, account_sid = get_twilio_credentials()
        twilio_client = TwilioClient(api_key, api_secret, account_sid)
        base_url = settings.knock_internal_api_url

        call = await asyncio.to_thread(
            twilio_client.calls(call_sid).update,
            twiml=_build_transfer_twiml(base_url),
            status_callback=f"{base_url}/v1/relay/voice/clay/callback",
        )
        logger.info("Transferred call", call_sid=call_sid, reason=reason, status=call.status)
    except Exception as transfer_error:
        logger.error("Failed to transfer call", call_sid=call_sid, reason=reason, error=str(transfer_error))


async def transfer_call_on_validation_failure(
    error: Exception,
    payload: dict[str, Any],
    call_sid: str,
    *,
    root_run: ls.RunTree | None = None,
    variant: str = "v2",
) -> None:
    """Transfer the call when payload validation fails."""
    error_str = str(error)
    validation_reason = "missing_required_fields" if "Missing required fields" in error_str else "other"
    logger.warning(
        "AskRequest validation failed - transferring call",
        event_type="validation_failed",
        validation_reason=validation_reason,
        call_sid=call_sid,
        error=error_str,
        product=payload.get("product"),
        property_name=payload.get("product_info", {}).get("property_name"),
    )
    marker_inputs = build_validation_failure_marker_inputs(
        error_str=error_str,
        validation_reason=validation_reason,
        payload=payload,
        variant=variant,
    )
    post_trace_marker(
        root_run,
        "validation_failure",
        inputs=marker_inputs,
        message=f"Validation failed: {validation_reason}",
    )
    await transfer_call(call_sid, "validation_failure")


async def transfer_call_on_init_failure(call_sid: str) -> None:
    """Transfer the call when background full-agent init fails or times out."""
    logger.warning("Full agent init failed - transferring call to staff", call_sid=call_sid)
    await transfer_call(call_sid, "agent_init_failure")
