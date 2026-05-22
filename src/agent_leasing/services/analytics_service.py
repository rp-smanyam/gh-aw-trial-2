import ast
import json
from typing import Any

import structlog
from agents import RunResult

from agent_leasing.agent.util import SessionScope
from agent_leasing.api.model import Channel, Flow
from agent_leasing.kafka.kafka_recorder import Author, log_data_curation_event  # noqa: F401
from agent_leasing.settings import settings

logger = structlog.getLogger()


async def log_conversation_exchange(
    *,
    chat_session_id: str,
    conversation_type: Channel,
    user_message: str,
    bot_message: str,
    call_sid: str | None,
    property_id: str,
    applicant_id: str,
    bot_type: str,
    flows: list[Flow],
    language: str = "en",
    bot_metadata: list = [],
    openai_trace_url: str = None,
    langsmith_trace_url: str = None,
):
    """Log a complete conversation exchange (user message + bot response).

    This helper function logs both the contact's message and the bot's response.

    Args:
        chat_session_id: Unique identifier for the chat session
        conversation_type: Type of conversation (e.g., CHAT, SMS)
        user_message: The message from the user/contact
        bot_message: The response from the bot
        call_sid: Twilio call SID (if applicable)
        property_id: Property identifier
        applicant_id: Applicant/resident identifier
        bot_type: Type/persona of the bot
        flows: List of flows executed
        language: Language code (default: "en")
        bot_metadata: Additional metadata for the bot response (default: [])
        openai_trace_url: OpenAI trace URL for debugging (optional)
        langsmith_trace_url: LangSmith trace URL for debugging (optional)
    """
    # Log the contact's message
    await log_data_curation_event(
        chat_session_id=chat_session_id,
        conversation_type=conversation_type,
        body=user_message,
        call_sid=call_sid,
        property_id=property_id,
        applicant_id=applicant_id,
        bot_type=bot_type,
        author=Author.CONTACT,
        flows=flows,
        language=language,
        openai_trace_url=openai_trace_url,
        langsmith_trace_url=langsmith_trace_url,
    )

    # Log the bot's response
    await log_data_curation_event(
        chat_session_id=chat_session_id,
        conversation_type=conversation_type,
        body=bot_message,
        call_sid=call_sid,
        property_id=property_id,
        applicant_id=applicant_id,
        bot_type=bot_type,
        author=Author.BOT,
        flows=flows,
        language=language,
        metadata=bot_metadata,
        openai_trace_url=openai_trace_url,
        langsmith_trace_url=langsmith_trace_url,
    )


def _extract_sr_metadata_legacy(item, metadata, metadata_key):
    raw_output = item.get("output", "{}")
    # Upstream sometimes hands us an already-deserialized dict or list (and sometimes
    # a JSON-encoded string). json.loads() only accepts str/bytes, so passing a list
    # would raise TypeError and silently drop the metadata. Accept both shapes.
    if isinstance(raw_output, dict | list):
        item_output = raw_output
    elif isinstance(raw_output, str | bytes | bytearray):
        try:
            item_output = json.loads(raw_output)
        except json.JSONDecodeError:
            return
    else:
        return

    tool_outputs = []
    if isinstance(item_output, dict):
        tool_outputs = [item_output]
    elif isinstance(item_output, list):
        tool_outputs = item_output
    else:
        return

    for output in tool_outputs:
        raw_text = output.get("text", "{}")
        if isinstance(raw_text, dict | list):
            tool_output_text = raw_text
        elif isinstance(raw_text, str | bytes | bytearray):
            try:
                tool_output_text = json.loads(raw_text)
            except json.JSONDecodeError:
                # If the text part is not JSON that's fine.
                # It could be a tool failure and the `text` may not be JSON
                logger.info(f"Skip saving tool output in logging metadata because it is not JSON: {output}")
                continue
        else:
            continue

        if isinstance(tool_output_text, dict):
            tool_output_texts = [tool_output_text]
        elif isinstance(tool_output_text, list):
            tool_output_texts = tool_output_text
        else:
            continue
        for sr in tool_output_texts:
            if sr.get("service_request_created"):
                sr_id = sr.get("service_request_id")
                priority_number = sr.get("priority_number")
                priority_name = sr.get("priority_name")
                metadata[metadata_key] = {
                    "service_request": [
                        "create_service_request",
                        {
                            "created": True,
                            "sr_id": sr_id,
                            "priority_number": priority_number,
                            "priority_name": priority_name,
                        },
                    ]
                }


def _parse_json(raw: Any) -> Any:
    # Shared helper for both _extract_sr_metadata and _extract_self_service_metadata.
    # Callers hand in tool-call `output` / `arguments` — sometimes a JSON string,
    # sometimes an already-deserialized dict/list. Without the dict/list pass-through,
    # json.loads(non-string) raises TypeError, escapes the JSONDecodeError catch,
    # and is swallowed by the broad except in add_metadata_into_context — silently
    # dropping every metadata field for the turn. See #1541.
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict | list):
        return raw
    if not isinstance(raw, str | bytes | bytearray):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return None


def _extract_sr_metadata(item, metadata, metadata_key):
    raw_output = item.get("output", "{}")
    tool_output = _parse_json(raw_output)
    if not isinstance(tool_output, dict):
        return
    # Example tool_output:
    # {
    #   "self_service_available": false,
    #   "service_request_numbers": [{"sr_id": "4216-1", "priority_number": "1", "priority_name": "Emergency"}],
    #   "instructions": "I've created service request 4216-1 for 'Gas leakage in stove' with 1 - Emergency priority. Our maintenance team has been notified and will address the issue."
    # }
    sr_numbers = tool_output.get("service_request_numbers") or []
    action_taken = tool_output.get("action_taken") or None
    if isinstance(sr_numbers, list) and sr_numbers and action_taken == "service_request_created":
        service_request_metadata = ["create_service_request"]
        for sr in sr_numbers:
            sr_id = sr.get("sr_id")
            priority_number = sr.get("priority_number")
            priority_name = sr.get("priority_name")
            service_request_metadata.append(
                {"created": True, "sr_id": sr_id, "priority_number": priority_number, "priority_name": priority_name}
            )

        sr_meta = metadata.setdefault(metadata_key, {}).setdefault("service_request", [])
        sr_meta.extend(service_request_metadata)


def _extract_self_service_metadata(item, metadata, metadata_key):
    arguments = _parse_json(item.get("arguments", "{}"))
    if not isinstance(arguments, dict):
        logger.error(f"Cannot parse function_call arguments into dict: {item.get('arguments', '')}")
        return
    issue_resolved = arguments.get("issue_resolved_with_self_service")
    steps_requested = arguments.get("self_service_steps_requested")
    if issue_resolved is None and steps_requested is None:
        return

    service_request_metadata = ["self_service"]
    if issue_resolved is not None:
        service_request_metadata.append({"issue_resolved_with_self_service": issue_resolved})
    if steps_requested is not None:
        service_request_metadata.append({"self_service_steps_requested": steps_requested})

    sr_meta = metadata.setdefault(metadata_key, {}).setdefault("service_request", [])
    sr_meta.extend(service_request_metadata)


def add_metadata_into_context(context: SessionScope, result: RunResult, user_input: str | None = None) -> None:
    """Add metadata from the run result into the session context.

    For now, it only extracts service request metadata from function calls
    and their outputs, tracking whether service requests were created and their IDs.

    Args:
        context: The session context to update with metadata
        result: The run result containing function call information
        user_input: The user's input message. To match the metadata
            with the correct message coming from the user for `voice`.
            See: log_data_curation_event_for_realtime_events

    Returns:
        None
    """
    try:
        metadata = {}
        result_input_list = result.to_input_list()
        logger.debug(f"Analytics: Adding metadata into context: Input item: {result_input_list}")
        for item in result_input_list:
            metadata_key = user_input or item.get("call_id")
            item_type = item.get("type")
            if item_type == "function_call":
                if item["name"] == "call_facilities_thinker_via_api" or item["name"] == "queue_resolution_ack":
                    _extract_self_service_metadata(item, metadata, metadata_key)
                if item["name"] == "create_service_request":
                    metadata[metadata_key] = {"service_request": ["create_service_request", {"created": False}]}
            elif item_type == "function_call_output":
                if settings.facilities_thinker_api_enabled is False:
                    _extract_sr_metadata_legacy(item, metadata, metadata_key)
                    continue

                _extract_sr_metadata(item, metadata, metadata_key)

        context.logging_metadata.update(metadata)
    except Exception as exc:
        logger.exception(f"Adding metadata into context: Error while extracting metadata: {exc}")


# Re-export for convenience
__all__ = ["add_metadata_into_context", "log_data_curation_event", "log_conversation_exchange"]
