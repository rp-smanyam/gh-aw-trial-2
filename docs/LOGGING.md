# Logging

## Structured Logging

[structlog](https://www.structlog.org/en/stable/) is used for logging.

The following items are logged in every request:

- Channel `channel`
- Request ID `request_id`
- Trace ID `openai_trace_id`
- Chat Session ID `chat_session_id`
- Property ID `property_id`
- Prospect ID `prospect_id`

An example:

```text
Input items: [{'role': 'user', 'content': 'hello'}] [agent-leasing] channel=renter_ai_prospect_chat chat_session_id=1 openai_trace_id=2 property_id=3 prospect_id=4 request_id=5
```

To log as JSON:

```shell
source .venv/bin/activate
LOG_JSON_FORMAT=true uvicorn server:app --host 0.0.0.0
```

To add suppression of `uvicorn` server logs use `--log-config` with `util/uvicorn_disable_logging.json`:

```shell
LOG_JSON_FORMAT=true uvicorn server:app --host 0.0.0.0 --log-config util/uvicorn_disable_logging.json
```

See the `start.sh` and adjust to your taste.

## Producing Data Curation Events to Kafka

This section explains how we log conversation messages to the Kafka topic `conversation-ai-data-curation`, the event schema, and how these logs are processed downstream. The logs are consumed by the `reporting-event` service, aggregated, published to the `conversation-ai-report` topic, and later displayed in the Session Viewer.

To log data curation events to Kafka ensure that the `DATA_CURATION_*` and `KAFKA_REPORTING_DATA_BOOTSTRAP_SERVERS_*` 
environment variables are set in the environment. Events are emitted when requests are made of the agent (inbound)
and when responses are received (outbound). The target topic is defined by `KAFKA_REPORTING_DATA_TOPIC`.

**Useful links**

* [Kafka UI](https://rei-devtools.realpage.com/kafka-ui) (to view raw logs on Alpha)
* [Session Viewer](https://alpha-session-viewer.knocktest.com) (to view aggregated logs on Alpha)

Assuming you have your environment variables set up correctly, you can publish to Kafka with 
[sample_producer.py](../src/agent_leasing/kafka/sample_producer.py).

The development topic for data curation is 
[conversation-ai-data-curation-dev](https://confluent.cloud/environments/env-mvk6px/clusters/lkc-j5wnvw/topics/conversation-ai-data-curation-dev/message-viewer).

---

### Event Schema

Each event written to `conversation-ai-data-curation` must contain the following fields:

* **conversation\_id**: A unique identifier for the conversation. Used to group conversation messages during aggregation.
* **call\_sid**: Call identifier for the voice channel. This is used to retrieve call recordings. `null` for non-voice channels.
* **property\_id**: Identifier for the property related to the conversation.
* **prospect\_id**: Identifier for the prospect (or `resident_id` when applicable).
* **conversation\_type**: The type of channel: `chat`, `email`, `SMS`, or `voice`.
* **bot\_type**: Indicates whether the bot represents a `resident`, `applicant`, or `prospect`.
* **author**: The source of the message, either `bot` or `contact`.
* **body**: The actual message text (from either the contact or the bot).
* **flows**: The flows executed as part of the response. These correspond to tools used (for example, if the “community thinker” is used, the flow would be `community`).
* **timestamp**: The exact time when the message was generated.
* **language**: The language detected in the conversation.
  * For non-realtime models, this is calculated in the structured output from the Responder Agent (e.g., {"response": str, "language_code": str=len(2)}
  * For realtime models, there is no structured output, so we call a classifier function when writing to the Kafka queue

---

### Aggregation Rules

The `reporting-event` service consumes these logs and groups them into conversations before publishing them to `conversation-ai-report`.

* Use the same `conversation_id` for the duration of a conversation. This ensures correct grouping during aggregation.
* Provide `call_sid` only for voice channels so that downstream services can retrieve call recordings.
* **Chat, SMS, and Email:** A conversation is considered complete if no new message is received within one hour of the last message for a given `conversation_id`. Any new message received after this one-hour gap will be treated as a new conversation.
* **Voice:** Conversations are considered complete when the call ends. This is determined by a final log message with `body` set to `"END"`. All preceding events for the same `conversation_id` are aggregated up until this termination marker.
* Use standardized language codes (for example: `en`, `es`).

---

### Voice Transcript Cache (KNCK-38461)

The OpenAI Agents SDK (0.6.9+) sends `conversation.item.truncate` on every user interrupt. This triggers a bug in the SDK's `session.py` where `turn_ended` clears `_item_transcripts` before `item_updated` can use them to preserve transcripts on history items. The result: `session._history` has empty transcripts at Kafka snapshot time, causing ~50% of assistant messages to be dropped.

**Workaround:** `TwilioWebsocketHandler` maintains its own `_transcript_cache` dict, populated from `transcript_delta` raw model events in `_handle_raw_model_event`. This cache is independent of the SDK's `_item_transcripts` and cannot be cleared by the SDK's `turn_ended` handler.

At Kafka logging time (`_schedule_data_curation_logging`), the cache is passed to `realtime_util.log_data_curation_event_for_realtime_history`. In `realtime_history_to_input_item`, the cache serves as a fallback when `content[0].transcript` is empty. Recovered messages log `"Recovered transcript from cache"`.

**Key files:**

| File | Role |
|------|------|
| `src/agent_leasing/twilio_handler.py` | Accumulates `transcript_delta` events into `_transcript_cache`, passes to Kafka function |
| `src/agent_leasing/util/realtime_util.py` | Uses `transcript_cache` as fallback in `realtime_history_to_input_item` |

This workaround does **not** affect `realtime_history_to_input_list` (used for prompt context / `self.history`), LangSmith tracing, or the SDK itself.

---

### Summary

1. Every request and response is logged to the Kafka topic `conversation-ai-data-curation`.
2. The logs can be viewed in the Kafka UI.
3. The `reporting-event` service consumes and aggregates these logs, publishing them to the `conversation-ai-report` topic.
4. Aggregated conversations are then available in the Session Viewer.

This process ensures that all conversations, across different channels, are consistently logged, aggregated, and available for reporting and analysis.

Return to the main [README](../README.md).