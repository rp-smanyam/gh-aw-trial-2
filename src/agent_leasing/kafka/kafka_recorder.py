from datetime import datetime

import fastavro
import structlog

from agent_leasing.api.model import Author, Channel, Flow
from agent_leasing.kafka.kafka_context import kafka_application_context
from agent_leasing.settings import settings

logger = structlog.getLogger()


def get_conversation_type(text) -> str:
    """
    Returns the conversation type based on the text.

    Only supports CHAT.
    """
    return Channel.CHAT.value


def str_number_or_zero(value) -> str:
    """
    Converts a value to an integer and then to a str, returning "0" if the value is None or not a number.

    Args:
        value: The input value to convert.

    Returns:
        string: The initial value or "0" if conversion fails.
    """
    try:
        return str(int(value))
    except (ValueError, TypeError):
        return "0"


def build_data_curation_event(
    chat_session_id: str,
    conversation_type: Channel,
    body: str,
    call_sid: str,
    property_id: str,
    applicant_id: str,
    bot_type: str,
    author: Author,
    flows: list[Flow],
    timestamp: datetime,
    language: str,
    metadata: list,
    openai_trace_url: str = None,
    langsmith_trace_url: str = None,
) -> dict:
    """Build data curation event."""

    # this is a fix for the bug with missing conversation_id for the resident voice calls
    # https://github.com/RealPage/cai-session-viewer/issues/27
    conversation_id = chat_session_id
    if conversation_type in (Channel.VOICE, "VOICE") and not chat_session_id:
        conversation_id = call_sid

    transformed_record = {
        "conversation_id": conversation_id,
        "call_sid": call_sid,
        "property_id": str_number_or_zero(property_id),
        "prospect_id": str_number_or_zero(applicant_id),
        "conversation_type": conversation_type,
        "bot_type": bot_type,
        "language": language,
        "transcript": {
            "author": author.value,
            "body": body,
            # timestamp is in seconds
            "timestamp": int(timestamp.timestamp() * 1000),
            "metadata": str(metadata),
            "openai_trace_url": openai_trace_url,
            "langsmith_trace_url": langsmith_trace_url,
        },
        "intent": {
            "name": flows[0].name,
            "display_name": flows[0].display_name,
            "language": language,
        },
    }

    return _order_record_keys(transformed_record)


def _order_record_keys(unordered_dict: dict) -> dict:
    """Order record keys."""
    # Desired sequence of keys
    desired_keys = [
        "conversation_id",
        "call_sid",
        "property_id",
        "prospect_id",
        "conversation_type",
        "bot_type",
        "language",
        "transcript",
        "intent",
    ]
    return {key: unordered_dict[key] for key in desired_keys}


async def log_data_curation_event(
    *,
    chat_session_id: str,
    conversation_type: Channel,
    body: str,
    call_sid: str | None,
    property_id: str,
    applicant_id: str,
    bot_type: str,
    author: Author,
    flows: list[Flow],
    timestamp: datetime | None = None,
    language: str = "en",
    metadata: list = [],
    validate_record: bool = False,
    openai_trace_url: str = None,
    langsmith_trace_url: str = None,
):
    """Send data curation event to Kafka."""
    conversation_type = conversation_type.upper()
    bot_type = bot_type.upper()

    if not settings.is_kafka_reporting_configured():
        return

    if not timestamp:
        timestamp = datetime.now()

    try:
        transformed_record = build_data_curation_event(
            chat_session_id,
            conversation_type,
            body,
            call_sid,
            property_id,
            applicant_id,
            bot_type,
            author,
            flows,
            timestamp,
            language,
            metadata,
            openai_trace_url,
            langsmith_trace_url,
        )

        logger.debug(f"Kafka record: {transformed_record}")

        def ack(err, msg):
            if err:
                logger.warning(f"Kafka error on ack: {err} {msg} {transformed_record}")

        if validate_record:
            logger.debug(f"Transformed Kafka record: {transformed_record}")
            parsed_schema = fastavro.parse_schema(settings.data_curation_schema)
            fastavro.validate(transformed_record, parsed_schema)
            logger.debug("Validated Kafka record")

        kafka_application_context.reporting_data_kafka_producer.produce(transformed_record, on_delivery=ack)

    except Exception:  # noqa
        logger.exception("Kafka reporting error")
