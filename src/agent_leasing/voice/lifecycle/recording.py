"""Call recording via Twilio REST API.

Recording logic from ``twilio_handler.py:_start_recording``.

Starts dual-channel recording as a background task so it doesn't block
the voice startup critical path.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from twilio.rest import Client as TwilioClient

from agent_leasing.settings import settings
from agent_leasing.util.twilio_util import get_twilio_credentials

logger = structlog.get_logger(__name__)


async def _create_recording(twilio_client: TwilioClient, call_sid: str, callback_url: str) -> None:
    """Create the recording in a thread, catching Twilio errors here."""
    try:
        await asyncio.to_thread(
            twilio_client.calls(call_sid).recordings.create,
            recording_status_callback=callback_url,
            recording_channels="dual",
        )
        logger.info(f"Recording created for call {call_sid}")
    except Exception as e:
        logger.warning(f"Recording failed for call {call_sid}: {e}")


async def start_recording(payload: dict[str, Any], call_sid: str) -> asyncio.Task[None] | None:
    """Start Twilio recording if the payload requests it.

    Returns the background task (for cleanup tracking) or None.
    """
    if not payload.get("product_info", {}).get("should_record", False):
        return None

    try:
        api_key, api_secret, account_sid = get_twilio_credentials()
        twilio_client = TwilioClient(api_key, api_secret, account_sid)
        base_url = settings.knock_internal_api_url

        task = asyncio.create_task(
            _create_recording(twilio_client, call_sid, f"{base_url}/v1/relay/voice/handlers/hangup-with-recording")
        )
        return task
    except Exception as e:
        logger.error(f"Error starting recording for call {call_sid}: {e}")
        return None
