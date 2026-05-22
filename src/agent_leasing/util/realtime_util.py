import asyncio
import json
from collections.abc import Iterator
from typing import Any

import langsmith as ls
import structlog
from agents import (
    trace,
)
from agents.realtime import (
    AssistantMessageItem,
    RealtimeSession,
    RealtimeToolCallItem,
    UserMessageItem,
)

from agent_leasing.api.model import Author, Flow, Product
from agent_leasing.kafka.kafka_recorder import log_data_curation_event
from agent_leasing.kafka.task_activity.emit import publish_task_activity
from agent_leasing.kafka.task_activity.extractors import extract_frustrated_user_events
from agent_leasing.settings import settings
from agent_leasing.util.frustration_classifier import (
    DEFAULT_FRUSTRATION_CLASSIFICATION_OUTPUT,
    classify_frustration,
)
from agent_leasing.util.language_classifier import classify_language

logger = structlog.getLogger()

LANGUAGE_CLASSIFICATION_SPAN_NAME = "language_classification"


async def log_data_curation_event_for_realtime_events(session: RealtimeSession) -> None:
    """
    Log data curation event from history updated event.

    UserMessageItem is from the contact, AssistantMessageItem is from the bot.
    We get the transcript from the history item.
    Then log the data coming from the contact or the bot.
    """
    await log_data_curation_event_for_realtime_history(session._history, session._context_wrapper.context)


async def log_data_curation_event_for_realtime_history(
    event_history: list, context, *, transcript_cache: dict[str, str] | None = None
) -> None:
    """Log data curation events for a realtime conversation history.

    Args:
        event_history: List of realtime history items (UserMessageItem, AssistantMessageItem, RealtimeToolCallItem, ...)
        context: SessionScope-like object with required attributes (ask_request, property_id, persona, logging_metadata, ...)
        transcript_cache: Fallback transcripts accumulated from transcript_delta events (KNCK-38461 workaround)
    """
    trace_info = _build_trace_info(context)
    last_language = "en"
    with trace(workflow_name="Realtime Data Logging"):
        try:
            # Single walk feeds both classifiers and the frustration emit,
            # avoiding three separate passes over event_history.
            message_items = _collect_message_items(event_history, transcript_cache=transcript_cache)
            language_codes, frustration_result = await asyncio.gather(
                _classify_message_languages(message_items),
                _classify_conversation_frustration(message_items),
            )
            _publish_frustration_activity(frustration_result, message_items, context)
            last_language = await _log_message_events(
                event_history, context, iter(language_codes), trace_info, transcript_cache=transcript_cache
            )
        except Exception:
            logger.exception("Error during data curation message logging")
        finally:
            # END marker must always publish so calls appear in session viewer
            try:
                await _log_end_event(context, last_language, trace_info)
            except Exception:
                logger.exception("Error publishing END marker")


def realtime_history_to_input_list(history, include_item_id=False, transcript_cache=None):
    """Convert a realtime history to a list of input items.

    Args:
        history: The realtime history to convert
        include_item_id: Whether to include item_id fields (needed for realtime APIs,
                        but not for regular OpenAI API calls)
        transcript_cache: Fallback transcripts accumulated from transcript_delta events (KNCK-38461 workaround)
    """
    return [
        history_item
        for message in history
        if (
            history_item := realtime_history_to_input_item(
                message, include_item_id=include_item_id, transcript_cache=transcript_cache
            )
        )
    ]


def realtime_history_to_input_item(history_item, include_item_id=False, transcript_cache=None):
    """Convert a single realtime history item to an input dict, or None if not convertible.

    Args:
        history_item: The realtime history item to convert
        include_item_id: Whether to include the item_id field (needed for realtime APIs,
                        but not for regular OpenAI API calls)
        transcript_cache: Fallback transcripts accumulated from transcript_delta events (KNCK-38461 workaround)
    """
    if not isinstance(history_item, UserMessageItem | AssistantMessageItem):
        return None

    transcript = None
    if history_item.content and history_item.content[0].transcript:
        transcript = history_item.content[0].transcript

    # SDK bug workaround: fall back to our accumulated transcripts (KNCK-38461)
    if not transcript and transcript_cache:
        transcript = transcript_cache.get(history_item.item_id)
        if transcript:
            logger.debug(
                "Recovered transcript from cache",
                item_id=history_item.item_id,
                role=history_item.role,
            )

    if not transcript:
        return None

    result = {
        "role": history_item.role,
        "content": transcript,
    }

    if include_item_id:
        result["item_id"] = history_item.item_id

    return result


def _build_trace_info(context) -> dict:
    """Extract trace URLs and workflow metadata from context."""
    product_name = getattr(context, "ask_request", None) and getattr(context.ask_request, "product", None)
    workflow_name = product_name.upper() if product_name else Product.RESIDENT_ONE_VOICE.value.upper()
    return {
        "openai_trace_url": getattr(context, "openai_trace_url", None),
        "langsmith_trace_url": getattr(context, "langsmith_trace_url", None),
        "workflow_name": workflow_name,
        "default_flows": [Flow(name=workflow_name)],
    }


def _collect_message_items(event_history: list, *, transcript_cache: dict[str, str] | None = None) -> list[dict]:
    """One pass over event_history rendering every UserMessageItem /
    AssistantMessageItem that has a transcript. Each entry is the same
    `{role, content}` dict `realtime_history_to_input_item` returns.
    """
    items: list[dict] = []
    for item in event_history:
        if not isinstance(item, UserMessageItem | AssistantMessageItem):
            continue
        input_item = realtime_history_to_input_item(item, transcript_cache=transcript_cache)
        if input_item and input_item.get("content"):
            items.append(input_item)
    return items


async def _classify_message_languages(message_items: list[dict]) -> list[str]:
    """Classify each rendered message's language in parallel."""
    message_texts = [item["content"] for item in message_items]
    try:
        return await _classify_language_codes_in_order(message_texts)
    except asyncio.CancelledError:
        # KNCK-38864: anyio bug #695 can inject CancelledError during teardown,
        # killing the entire data curation task. Fall back to "en" so Kafka
        # events are still published and calls appear in session viewer.
        # Designed graceful path — not an application error.
        logger.info("Language classification cancelled — falling back to 'en' for all messages")
        return ["en"] * len(message_texts)


async def _classify_conversation_frustration(message_items: list[dict]):
    """Conversation-level frustration classification. Soft-fails to
    not-frustrated; gated by `frustration_classifier_enabled` (see
    `settings.py`).
    """
    if not settings.frustration_classifier_enabled:
        return DEFAULT_FRUSTRATION_CLASSIFICATION_OUTPUT
    if not message_items:
        return DEFAULT_FRUSTRATION_CLASSIFICATION_OUTPUT
    transcript = "\n".join(
        f"{'Resident' if item['role'] == 'user' else 'Assistant'}: {item['content']}" for item in message_items
    )
    try:
        return await asyncio.wait_for(
            classify_frustration(transcript),
            timeout=settings.frustration_classifier_timeout_seconds,
        )
    except TimeoutError:
        # Designed graceful fallback — not an application error.
        logger.info("Frustration classification timed out — falling back to not-frustrated")
        return DEFAULT_FRUSTRATION_CLASSIFICATION_OUTPUT
    except asyncio.CancelledError:
        # Mirror the language-classifier behavior under anyio teardown
        # cancellation — fall back rather than killing the whole flow.
        logger.info("Frustration classification cancelled — falling back to not-frustrated")
        return DEFAULT_FRUSTRATION_CLASSIFICATION_OUTPUT


def _publish_frustration_activity(frustration_result, message_items: list[dict], context) -> None:
    """Publish a FRUSTRATED_USER TaskActivityEvent when the classifier
    flagged the conversation. Dedup is delivery-time —
    `SessionScope.frustrated_user_emitted` flips only after the publish
    is confirmed. The classifier's `trigger_message` (verbatim quote of
    the most escalated turn) rides along as `user_message` for context,
    falling back to the LAST user message if the classifier didn't
    supply one (the last message is the most likely trigger; the first
    is usually a polite greeting).
    """
    if not getattr(frustration_result, "is_frustrated", False):
        return
    user_message = getattr(frustration_result, "trigger_message", "") or next(
        (item["content"] for item in reversed(message_items) if item["role"] == "user"),
        None,
    )

    def _flip_dedup() -> None:
        context.frustrated_user_emitted = True

    publish_task_activity(
        extract_frustrated_user_events,
        True,
        context,
        user_message=user_message,
        on_success=_flip_dedup,
    )


async def _classify_language_codes_in_order(texts: list[str]) -> list[str]:
    """Classify languages for a list of texts concurrently, preserving order."""
    if not texts:
        return []

    max_concurrency = max(1, settings.realtime_language_classification_max_concurrency)
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _classify_one(text: str) -> str:
        async with semaphore:
            result = await classify_language(text)
            return result.language_code or "en"

    with ls.trace(
        name=LANGUAGE_CLASSIFICATION_SPAN_NAME,
        run_type="chain",
        inputs={"message_count": len(texts), "max_concurrency": max_concurrency},
    ) as run:
        language_codes = await asyncio.gather(*(_classify_one(text) for text in texts))
        run.end(outputs={"language_codes": language_codes})
        return language_codes


async def _log_message_events(
    event_history: list,
    context,
    language_codes_iter,
    trace_info: dict,
    *,
    transcript_cache: dict[str, str] | None = None,
) -> str:
    """Log a data curation event for each user/assistant message. Returns the last language code."""
    default_flows = trace_info["default_flows"]
    openai_trace_url = trace_info["openai_trace_url"]
    langsmith_trace_url = trace_info["langsmith_trace_url"]

    last_language_code = "en"
    flows: list[Flow] = []
    logging_metadata: list = []

    for i, history_item in enumerate(event_history):
        last_language_code, flows, logging_metadata = await _log_message_event_for_history_item(
            i=i,
            history_item=history_item,
            event_history=event_history,
            context=context,
            language_codes_iter=language_codes_iter,
            last_language_code=last_language_code,
            flows=flows,
            logging_metadata=logging_metadata,
            default_flows=default_flows,
            openai_trace_url=openai_trace_url,
            langsmith_trace_url=langsmith_trace_url,
            transcript_cache=transcript_cache,
        )

    return last_language_code


async def _log_message_event_for_history_item(
    *,
    i: int,
    history_item: object,
    event_history: list[object],
    context: Any,
    language_codes_iter: Iterator[str],
    last_language_code: str,
    flows: list[Flow],
    logging_metadata: list[object],
    default_flows: list[Flow],
    openai_trace_url: str | None,
    langsmith_trace_url: str | None,
    transcript_cache: dict[str, str] | None = None,
) -> tuple[str, list[Flow], list[object]]:
    logger.debug(f"Event history for {context.ask_request.chat_session_id}: {i}, {history_item}")

    history_input_item = None
    author = None

    if isinstance(history_item, UserMessageItem):
        author = Author.CONTACT
        history_input_item = realtime_history_to_input_item(history_item, transcript_cache=transcript_cache)
    elif isinstance(history_item, AssistantMessageItem):
        author = Author.BOT
        history_input_item = realtime_history_to_input_item(history_item, transcript_cache=transcript_cache)
        if (
            i + 1 < len(event_history)
            and isinstance((next_item := event_history[i + 1]), RealtimeToolCallItem)
            and next_item.name == "transfer_to_staff_voice"
        ):
            flows = [Flow(name=next_item.name)]
    elif isinstance(history_item, RealtimeToolCallItem) and history_item.name != "transfer_to_staff_voice":
        try:
            flows = [Flow(name=history_item.name)]
            tool_call_input = json.loads(history_item.arguments).get("input")
            if logging_metadata_item := context.logging_metadata.get(tool_call_input):
                logging_metadata.append(logging_metadata_item)
        except Exception as exc:
            logger.debug(f"Analytics: Error while matching metadata/flows: {exc}")

    if not history_input_item or author is None:
        return last_language_code, flows, logging_metadata

    text_content = history_input_item["content"]
    language_code = next(language_codes_iter, "en") or "en"
    last_language_code = language_code

    is_assistant = isinstance(history_item, AssistantMessageItem)
    event_flows = list(flows) if flows and is_assistant else default_flows
    event_metadata = logging_metadata if is_assistant else []

    await log_data_curation_event(
        chat_session_id=context.ask_request.chat_session_id,
        conversation_type=context.ask_request.conversation_type,
        body=text_content,
        call_sid=context.ask_request.product_info.call_sid,
        property_id=context.property_id,
        applicant_id=context.ask_request.resident_id,
        bot_type=context.persona,
        author=author,
        flows=event_flows,
        language=language_code,
        metadata=event_metadata,
        openai_trace_url=openai_trace_url,
        langsmith_trace_url=langsmith_trace_url,
    )

    if is_assistant:
        return last_language_code, [], []

    return last_language_code, flows, logging_metadata


async def _log_end_event(context, last_language_code: str, trace_info: dict) -> None:
    """Log the END marker event."""
    await log_data_curation_event(
        chat_session_id=context.ask_request.chat_session_id,
        conversation_type=context.ask_request.conversation_type,
        body="END",
        call_sid=context.ask_request.product_info.call_sid,
        property_id=context.property_id,
        applicant_id=context.ask_request.resident_id,
        bot_type=context.persona,
        author=Author.BOT,
        flows=[Flow(name="END")],
        language=last_language_code,
        openai_trace_url=trace_info["openai_trace_url"],
        langsmith_trace_url=trace_info["langsmith_trace_url"],
    )
