"""
Locust load tests for the agent-leasing server.

Targets:
    - /v1/agent/ask (HTTP POST) — chat/SMS/email products
    - /media-stream/websocket (WSS) — Twilio voice media stream

Usage:
    # Web UI (recommended for interactive tuning):
    uv run locust -f tests/load/locustfile.py

    # Headless — quick smoke test (5 users, 1 user/sec, 30 seconds):
    uv run locust -f tests/load/locustfile.py --headless -u 5 -r 1 -t 30s

    # Headless — moderate load (50 users, 5 users/sec, 2 minutes):
    uv run locust -f tests/load/locustfile.py --headless -u 50 -r 5 -t 2m

    # Headless — heavy load (200 users, 20 users/sec, 5 minutes):
    uv run locust -f tests/load/locustfile.py --headless -u 200 -r 20 -t 5m

    # Voice-only load test:
    uv run locust -f tests/load/locustfile.py --headless -u 10 -r 2 -t 2m TwilioVoiceUser

    # Chat-only load test:
    uv run locust -f tests/load/locustfile.py --headless -u 20 -r 5 -t 2m AgentAskUser

    # HTTPS endpoints (beta, alpha, prod): use scripts/run_locust.sh wrapper.
    # pip_system_certs is incompatible with gevent's SSL monkey-patching.
    LOAD_TEST_PAYLOAD=data/payloads/beta-chat.json ./scripts/run_locust.sh \
        --host https://beta-agent-leasing.knocktest.com --headless -u 10 -r 2 -t 2m AgentAskUser

Environment variables:
    LOCUST_HOST              Target host (default: http://localhost:8000)
    LOAD_TEST_PAYLOAD        Path to a JSON file with the AskRequest payload for chat/SMS/email.
                             Defaults to the SMS example payload in the repo.
    LOAD_TEST_VOICE_PAYLOAD  Path to a JSON file with the AskRequest payload for voice.
                             Defaults to the voice example payload in the repo.
    VOICE_CALL_DURATION      Simulated call duration in seconds (default: 30)

Dial up/down:
    -u, --users          Number of concurrent users
    -r, --spawn-rate     Users spawned per second
    -t, --run-time       Duration (e.g., 30s, 2m, 1h)

    The web UI at http://localhost:8089 also allows changing user count live.
"""

import base64
import json
import os
import time
import uuid
from pathlib import Path

import websocket
from locust import FastHttpUser, User, between, events, task

# ---------------------------------------------------------------------------
# Payload loading
# ---------------------------------------------------------------------------

EXAMPLE_DATA_DIR = Path(__file__).resolve().parents[2] / "src" / "agent_leasing" / "api" / "example_data" / "resident"

DEFAULT_ASK_PAYLOAD_PATH = str(EXAMPLE_DATA_DIR / "sms" / "example_ask_request_knck.json")
DEFAULT_VOICE_PAYLOAD_PATH = str(EXAMPLE_DATA_DIR / "voice" / "example_ask_request_knck.json")


def _load_payload(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"Payload at {path} must be a JSON object")
    return payload


ASK_PAYLOAD_PATH = os.environ.get("LOAD_TEST_PAYLOAD", DEFAULT_ASK_PAYLOAD_PATH)
VOICE_PAYLOAD_PATH = os.environ.get("LOAD_TEST_VOICE_PAYLOAD", DEFAULT_VOICE_PAYLOAD_PATH)
VOICE_CALL_DURATION = int(os.environ.get("VOICE_CALL_DURATION", "30"))

ASK_PAYLOAD = _load_payload(ASK_PAYLOAD_PATH)
VOICE_PAYLOAD = _load_payload(VOICE_PAYLOAD_PATH)

PRODUCT = ASK_PAYLOAD.get("product", "unknown")

PROMPTS = [
    "hello",
    "What amenities are available?",
    "How do I submit a maintenance request?",
    "What are your office hours?",
    "I need to pay my rent",
    "Can you help me with parking?",
    "Tell me about the community",
    "I have a question about my lease",
    "What events are coming up?",
    "How do I contact the front desk?",
]


def _build_ask_payload(prompt: str, chat_session_id: str) -> dict:
    payload = json.loads(json.dumps(ASK_PAYLOAD))
    payload["prompt"] = prompt
    payload["chat_session_id"] = chat_session_id
    payload["request_id"] = str(uuid.uuid4())
    payload["is_load_test"] = True
    return payload


# ---------------------------------------------------------------------------
# HTTP users — /v1/agent/ask
# ---------------------------------------------------------------------------


class AgentAskUser(FastHttpUser):
    """Simulates a user sending messages to /v1/agent/ask.

    Each user gets a unique chat_session_id and cycles through prompts,
    simulating a multi-turn conversation.
    """

    host = "http://localhost:8000"
    wait_time = between(1, 5)
    insecure = True

    def on_start(self) -> None:
        self.chat_session_id = str(uuid.uuid4())
        self.prompt_index = 0

    @task
    def ask_agent(self) -> None:
        prompt = PROMPTS[self.prompt_index % len(PROMPTS)]
        self.prompt_index += 1
        payload = _build_ask_payload(prompt, self.chat_session_id)

        with self.client.post(
            "/v1/agent/ask",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            name=f"/v1/agent/ask [{PRODUCT}]",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Status {response.status_code}: {(response.text or '')[:200]}")


class SingleTurnUser(FastHttpUser):
    """Simulates users sending isolated single-turn requests.

    Each request gets a fresh chat_session_id — useful for testing
    cold-start / session-creation overhead.
    """

    host = "http://localhost:8000"
    wait_time = between(2, 8)
    weight = 1
    insecure = True

    @task
    def ask_single(self) -> None:
        prompt = PROMPTS[hash(uuid.uuid4()) % len(PROMPTS)]
        payload = _build_ask_payload(prompt, str(uuid.uuid4()))

        with self.client.post(
            "/v1/agent/ask",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            name=f"/v1/agent/ask [single-turn {PRODUCT}]",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Status {response.status_code}: {(response.text or '')[:200]}")


# ---------------------------------------------------------------------------
# WebSocket user — /media-stream/websocket (Twilio voice)
# ---------------------------------------------------------------------------

# μ-law silence: 0xFF is digital silence in G.711 μ-law encoding.
# 8kHz mono, 20ms per frame = 160 samples = 160 bytes.
MULAW_SILENCE_FRAME = base64.b64encode(b"\xff" * 160).decode("utf-8")


def _build_voice_payload() -> tuple[str, str, str]:
    """Build a voice payload and return (encoded_payload, call_sid, stream_sid)."""
    call_sid = f"CA{uuid.uuid4().hex}"
    stream_sid = f"MZ{uuid.uuid4().hex}"
    payload = json.loads(json.dumps(VOICE_PAYLOAD))
    payload["chat_session_id"] = str(uuid.uuid4())
    payload["flow_id"] = str(uuid.uuid4())
    payload["call_sid"] = call_sid
    payload.setdefault("product_info", {})
    payload["product_info"]["call_sid"] = call_sid
    payload["product_info"]["thread_id"] = f"LOAD-{uuid.uuid4().hex[:8]}"
    encoded = base64.b64encode(json.dumps(payload).encode()).decode("utf-8")
    return encoded, call_sid, stream_sid


def _ws_url_from_host(host: str) -> str:
    """Convert an HTTP host URL to a WebSocket URL."""
    if host.startswith("https://"):
        return host.replace("https://", "wss://", 1)
    return host.replace("http://", "ws://", 1)


class TwilioVoiceUser(User):
    """Simulates a Twilio voice call over the /media-stream/websocket endpoint.

    Each task represents one full phone call:
      1. WebSocket connect
      2. Send Twilio 'start' event with base64-encoded AskRequest payload
      3. Stream silence frames for VOICE_CALL_DURATION seconds (simulating an open call)
      4. Send Twilio 'stop' event
      5. Disconnect

    Adjust VOICE_CALL_DURATION env var to control call length.
    Audio frames are μ-law silence (0xFF bytes) sent every 20ms.
    """

    host = "http://localhost:8000"
    # Time between calls (after one call ends, wait before starting another)
    wait_time = between(2, 10)

    _langsmith_acknowledged = False

    def on_start(self) -> None:
        if not TwilioVoiceUser._langsmith_acknowledged:
            print(  # noqa: T201
                "\n"
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
                "!!  WARNING: VOICE LOAD TEST + LANGSMITH TRACING ENABLED   !!\n"
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
                "\n"
                "LangSmith tracing suppression is NOT implemented for voice.\n"
                "Every call will be traced and BILLED regardless of is_load_test.\n"
                "This can cost up to $750/day in tracing costs.\n"
                "\n"
                "Consider using text channels (chat/SMS/email) instead,\n"
                "which have tracing suppression for load tests.\n"
            )
            response = input("Type YES to continue anyway, or anything else to abort: ")
            if response.strip() != "YES":
                raise SystemExit("Aborted.")
            TwilioVoiceUser._langsmith_acknowledged = True

    @task
    def simulate_voice_call(self) -> None:
        ws_host = _ws_url_from_host(self.host)
        url = f"{ws_host}/media-stream/websocket"
        encoded_payload, call_sid, stream_sid = _build_voice_payload()

        start_time = time.time()
        ws = None
        exception = None
        try:
            # Connect
            ws = websocket.create_connection(url, timeout=10)

            # Send Twilio "start" event
            start_event = json.dumps(
                {
                    "event": "start",
                    "start": {
                        "streamSid": stream_sid,
                        "callSid": call_sid,
                        "customParameters": {"payload": encoded_payload},
                    },
                }
            )
            ws.send(start_event)

            # Stream silence frames for the call duration.
            # 20ms per frame = 50 frames/sec.
            frames_to_send = VOICE_CALL_DURATION * 50
            for _ in range(frames_to_send):
                if ws.connected:
                    media_event = json.dumps(
                        {
                            "event": "media",
                            "media": {"payload": MULAW_SILENCE_FRAME},
                        }
                    )
                    ws.send(media_event)
                    # Drain any incoming messages without blocking
                    ws.settimeout(0)
                    try:
                        while ws.recv():
                            pass
                    except (websocket.WebSocketTimeoutException, websocket.WebSocketConnectionClosedException):
                        pass
                    ws.settimeout(10)
                    time.sleep(0.02)  # 20ms pacing
                else:
                    break

            # Send Twilio "stop" event
            if ws.connected:
                stop_event = json.dumps({"event": "stop"})
                ws.send(stop_event)

        except Exception as e:
            exception = e
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass

        elapsed_ms = (time.time() - start_time) * 1000

        # Report to locust
        events.request.fire(
            request_type="WSS",
            name=f"/media-stream/websocket [voice {VOICE_CALL_DURATION}s]",
            response_time=elapsed_ms,
            response_length=0,
            exception=exception,
            context={},
        )
