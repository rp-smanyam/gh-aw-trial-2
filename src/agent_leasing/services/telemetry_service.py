import structlog
from opentelemetry import metrics

logger = structlog.getLogger()

# Metrics setup
meter = metrics.get_meter("llm.token_usage")
input_token_counter = meter.create_counter("tokens.input", unit="1", description="Input tokens")
output_token_counter = meter.create_counter("tokens.output", unit="1", description="Output tokens")


async def emit_metrics(result, chat_session_id: str):
    """Capture the metrics from the responses.

    Extracts token usage information from the result's raw responses and
    records them as OpenTelemetry metrics.

    Args:
        result: The run result containing raw responses with usage information
        chat_session_id: The session ID to associate with the metrics
    """
    input_tokens: int = 0
    output_tokens: int = 0
    if result.raw_responses:
        for response in result.raw_responses:
            input_tokens += response.usage.input_tokens
            output_tokens += response.usage.output_tokens

    logger.debug(f"Input tokens: {input_tokens}. Output tokens: {output_tokens}")
    input_token_counter.add(input_tokens, {"agent_leasing.session_id": chat_session_id})
    output_token_counter.add(output_tokens, {"agent_leasing.session_id": chat_session_id})
