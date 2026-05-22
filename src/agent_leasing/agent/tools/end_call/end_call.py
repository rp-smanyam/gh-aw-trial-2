import asyncio
import os
from typing import Annotated, Any

import structlog
from agents import RunContextWrapper, function_tool
from langsmith import traceable
from twilio.rest import Client as TwilioClient

from agent_leasing.settings import settings
from agent_leasing.util.call_state_manager import (
    get_call_state_from_context,
)

logger = structlog.getLogger()

# Read the description file from the correct path
description_path = os.path.join(os.path.dirname(__file__), "END_CALL_DESCRIPTION.md")
with open(description_path, encoding="utf-8") as f:
    END_CALL_DESCRIPTION = f.read()

CALL_MANAGEMENT_GUARD_MESSAGE = (
    "Another call management tool is already in progress. "
    "Do not call this tool again. DO NOT ACKNOWLEDGE THIS MESSAGE."
)


@traceable(run_type="tool", name="end_call")
async def _end_call_impl(
    ctx: RunContextWrapper[Any],
    message: Annotated[str, "Message to log when ending the call"],
    tool_use_reason: Annotated[str, "Reason for using this tool"],
    user_confirmation: Annotated[bool, "Whether user confirmed the action"],
):
    # Reject concurrent invocations — a second end_call (or any other call management
    # tool) racing with an in-flight transfer would fire duplicate Twilio calls
    # (KNCK-39358).
    if settings.call_management_concurrency_guard_enabled and ctx.context.call_management_in_progress:
        logger.warning(
            "Call management tool already in progress, skipping concurrent invocation",
            tool="end_call",
        )
        return CALL_MANAGEMENT_GUARD_MESSAGE

    ctx.context.call_management_in_progress = True
    try:
        call_sid = ctx.context.ask_request.product_info.call_sid
        call_state = get_call_state_from_context(ctx)
        if call_state is not None:
            playback_result = await call_state.wait_for_message_playback(
                "goodbye",
                tool_name="end_call",
            )
            if not playback_result.completed:
                logger.warning("Timed out waiting for goodbye message playback to complete", call_sid=call_sid)

        # Get call information from the context
        # Get Twilio credentials from settings
        account_sid = settings.knock_twilio_account_sid
        api_key = settings.knock_twilio_api_key
        api_secret = settings.knock_twilio_api_secret

        _validate_twilio_credentials(api_key, api_secret, account_sid)

        # Create Twilio client and end the call
        twilio_client = TwilioClient(api_key, api_secret, account_sid)
        call = twilio_client.calls(call_sid).update(status="completed")

        logger.info(f"Successfully ended call {call_sid} with message: {message}")
        logger.info(f"Call status updated to: {call.status}")

        ctx.context.call_ended_by_agent = True

        result = f"Call ended successfully. Status: {call.status}"
        return result

    except asyncio.CancelledError:
        # Safe to suppress: this task is cancelled by _cancel_background_tasks(), which uses
        # asyncio.gather(..., return_exceptions=True) and ignores normal returns. The session
        # is already tearing down, so the task will end naturally regardless.
        # Setting call_ended_by_agent here prevents a spurious call_hangup log entry.
        ctx.context.call_ended_by_agent = True
        logger.info("end_call cancelled: call ended by user during playback", call_sid=call_sid)
        return "Call ended by user."

    except Exception as e:
        logger.error(f"Error ending call {call_sid}: {e}")
        raise

    finally:
        ctx.context.call_management_in_progress = False


@function_tool(
    description_override=END_CALL_DESCRIPTION,
)
async def end_call(
    ctx: RunContextWrapper[Any],
    message: Annotated[str, "Message to log when ending the call"],
    tool_use_reason: Annotated[str, "Reason for using this tool"],
    user_confirmation: Annotated[bool, "Whether user confirmed the action"],
) -> str:
    """
    End the Twilio call by updating the call status to 'completed'.

    Args:
        ctx: The run context containing request information
        message: Message to log when ending the call
        tool_use_reason: Reason for using this tool
        user_confirmation: Whether user confirmed the action

    Returns:
        str: Success message or raises exception on error
    """
    return await _end_call_impl(ctx, message, tool_use_reason, user_confirmation)


def _validate_twilio_credentials(api_key: str, api_secret: str, account_sid: str):
    """
    Validate that the Twilio credentials exist.
    """
    if not all([api_key, api_secret, account_sid]):
        raise ValueError(
            f"Twilio credentials are not configured: "
            f"api_key='{api_key}', "
            f"api_secret='{api_secret}', "
            f"account_sid='{account_sid}'"
        )
