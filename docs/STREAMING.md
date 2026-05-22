# Streaming

The streaming endpoint is `/v1/agent/stream`.

Ensure the local server is running; see `README.md` for start instructions.
 
In the example cURL below:

```shell
curl -X POST --location "http://127.0.0.1:8000/v1/agent/stream" \
    -H "Content-Type: application/json" \
    -H "Accept: text/event-stream" \
    --data-binary "@src/agent_leasing/api/example_data/resident/chat/example_ask_request_ll.json"
```

The streamed data appears as:

```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive

data: {"content": "", "phase": "thinking", "status": "active", "elapsed": 50}

data: {"content": "", "phase": "searching", "status": "active", "elapsed": 2500}

data: {"content": "Based ", "phase": "generating", "status": "active", "elapsed": 3200}

data: {"content": "on the search results...", "phase": "generating", "status": "done", "done": true}

data: [DONE]
```

## Streaming Protocol

### Chunk Format

Each streaming chunk should be a JSON object with the following structure:

```json
{
  "content": "string (text chunk to append)",
  "status": "active | done | error | cancelled",
  "phase": "thinking | generating | waiting | searching | calling_tools",
  "elapsed": "number (optional - milliseconds since request start)",
  "done": "boolean (true if this is the final chunk)"
}
```

The done marker is `[DONE]\n\n`

Each event is sent as a Server-Sent Event (SSE) line prefixed with `data:` and terminated by a blank line.

Statuses:
- `active` - Streaming is in progress
- `done` - Streaming completed successfully
- `error` - An error occurred during streaming
- `cancelled` - Stream was cancelled by user

Phases:
- `thinking` (~0–1s) - Initial AI processing
- `generating` (1s+) - Generating response with tokens
- `waiting` (3s+) - Waiting for AI response
- `searching` (5s+) - AI is searching for information
- `calling_tools` (8s+) - AI is calling external tools

Note: Timings are approximate and may vary.

### Errors During Streaming

If an error occurs during streaming, send a final chunk with error status:

```
data: {"content": "Error message here", "status": "error", "done": true}

data: [DONE]
```

## CLI Streaming Script

The `cli-streaming` script provides an interactive command-line interface for testing the streaming endpoint with multi-turn conversations.

There is also a `cli-text` script for non-streaming request/response interaction described in the [README](README.md).

### Usage

The script is installed as part of the project dependencies.

**Start with default context:**

```shell
uv run cli-streaming
```

**Start with a payload file for context:**

```shell
uv run cli-streaming src/agent_leasing/api/example_data/resident/chat/example_ask_request_ll.json
```

The script will prompt you to enter messages. The conversation continues with context maintained across turns.

### Options

- `payload` - (Optional) JSON file containing the request payload for context setup. See example payloads in `src/agent_leasing/api/example_data/`.
- `--url` - Base URL of the agent service. Default: `http://localhost:8000`
- `--timeout` - Request timeout in seconds. Default: `120`

### Commands

- Type your message and press Enter to send
- Type `exit`, `quit`, or `q` to end the conversation
- Press Ctrl+C to interrupt at any time

### Examples

**Interactive conversation with payload context:**

```shell
uv run cli-streaming src/agent_leasing/api/example_data/resident/chat/example_ask_request_ll.json
```

**Test against alpha environment:**

```shell
uv run cli-streaming /path/to/alpha-payload.json --url https://alpha-agent-leasing.knocktest.com
```

When running against alpha or other environments, ensure your payload file contains the correct identifiers and
that you aren't trampling on a real resident's session.

### Output

The script displays:
- Connection information (first turn only)
- Phase indicators (e.g., `[thinking]`, `[searching]`)
- Streaming response content in real-time
- Completion status

Example session:
```
Loaded context from: src/agent_leasing/api/example_data/resident/chat/example_ask_request_ll.json
Type 'exit', 'quit', or 'q' to end the conversation.

You: Hello
Connecting to: http://localhost:8000/v1/agent/stream
--------------------------------------------------------------------------------
[thinking] Hello! How can I assist you today?
--------------------------------------------------------------------------------
Stream complete.

You: What are the office hours?
[thinking] The office hours are Monday through Friday, 9am to 6pm.
--------------------------------------------------------------------------------
Stream complete.

You: exit

Goodbye!
```

### Payload File Format

The payload file sets the conversation context (product, session IDs, product_info). See example payloads in `src/agent_leasing/api/example_data/resident/chat/example_ask_request_ll.json`.