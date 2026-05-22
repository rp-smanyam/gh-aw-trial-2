"""Voice interactive test client.

Simulates a Twilio media stream client and exposes a sidecar HTTP server
so Claude (or a human) can drive voice conversations via curl.

Pipeline: text -> OpenAI TTS -> G.711 mu-law -> Twilio WS -> agent -> mu-law -> Whisper STT -> text

Usage:
    uv run python tests/voice_test_client.py --port 8100
    # Then in another terminal:
    curl -s localhost:9090/send -d '{"text": "What is my rent balance?"}'
    curl -s localhost:9090/history
    curl -s localhost:9090/close
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import math
import re
import struct
import time
import uuid
from pathlib import Path

import numpy as np
import orjson
import websockets
from aiohttp import web
from openai import AsyncOpenAI
from scipy.signal import resample_poly

# mu-law compression lookup table (PCM16 -> mu-law byte)
_MULAW_BIAS = 0x84
_MULAW_CLIP = 32635


def _pcm16_sample_to_ulaw(sample: int) -> int:
    """Convert a single signed 16-bit PCM sample to mu-law byte."""
    sign = 0
    if sample < 0:
        sign = 0x80
        sample = -sample
    sample = min(sample, _MULAW_CLIP)
    sample += _MULAW_BIAS
    exponent = 7
    for exp_val in (0x4000, 0x2000, 0x1000, 0x0800, 0x0400, 0x0200, 0x0100):
        if sample >= exp_val:
            break
        exponent -= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def pcm16_to_ulaw(pcm_bytes: bytes) -> bytes:
    """Convert PCM16 LE bytes to G.711 mu-law bytes."""
    samples = struct.unpack(f"<{len(pcm_bytes) // 2}h", pcm_bytes)
    return bytes(_pcm16_sample_to_ulaw(s) for s in samples)


# mu-law decompression table
_ULAW_DECOMPRESS = []
for _b in range(256):
    _inv = ~_b & 0xFF
    _sign = _inv & 0x80
    _exponent = (_inv >> 4) & 0x07
    _mantissa = _inv & 0x0F
    _sample = ((2 * _mantissa + 33) << (_exponent + 2)) - _MULAW_BIAS
    if _sign:
        _sample = -_sample
    _ULAW_DECOMPRESS.append(_sample)


def ulaw_to_pcm16(ulaw_bytes: bytes) -> bytes:
    """Convert G.711 mu-law bytes to PCM16 LE bytes."""
    samples = [_ULAW_DECOMPRESS[b] for b in ulaw_bytes]
    return struct.pack(f"<{len(samples)}h", *samples)


def encode_object(obj: dict) -> str:
    """Base64-encode a dict using orjson, matching twilio_handler.encode_object."""
    return base64.b64encode(orjson.dumps(obj)).decode("utf-8")


FRAME_BYTES = 160  # 20ms at 8kHz mono mu-law
FRAME_DURATION_S = 0.020
TWILIO_SAMPLE_RATE = 8000

# Patterns from VOICE_RESPONDER.md — filler phrases the agent says while waiting for tools
_FILLER_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"still working",
        r"stand by",
        r"(little|just a) bit longer",
        r"appreciate your patience",
        r"thanks for your patience",
        r"let me (check|look|help|get|take care)",
        r"i'll (check|look|get|take care)",
        r"(one|just a) moment",
        r"hold on",
        r"^(got it|on it|sure thing|absolutely|no problem)[.!]?$",
    ]
]

# Patterns that signal the agent finished delivering a response
_COMPLETE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"how can i (assist|help)",
        r"what can i help",
        r"(is there |)anything else",
        r"can i help you with anything",
        r"would you like .* to (help|connect|transfer)",
        r"have a (great|good|wonderful) (day|evening|night)",
    ]
]


class VoiceTestClient:
    """Simulates a Twilio media stream client with HTTP sidecar for testing."""

    def __init__(
        self,
        server_port: int,
        http_port: int = 9090,
        payload_path: str | None = None,
        turn_timeout: float = 30.0,
        debug: bool = False,
    ):
        self.server_port = server_port
        self.http_port = http_port
        self.payload_path = payload_path
        self.turn_timeout = turn_timeout
        self._debug = debug

        self.ws: websockets.ClientConnection | None = None
        self.stream_sid = f"MZ{uuid.uuid4().hex[:30]}"
        self.call_sid = f"CA{uuid.uuid4().hex[:30]}"

        self.openai_client = AsyncOpenAI()

        # Conversation history
        self.history: list[dict[str, str]] = []

        # Segment-based audio collection from server.
        # Each burst of audio separated by _segment_gap silence = one "segment".
        # A background monitor transcribes segments incrementally.
        # Turn is done when the segment monitor detects a complete response.
        self._segments: list[bytearray] = []
        self._current_segment: bytearray = bytearray()
        self._segment_gap: float = 2.0  # silence to finalize a segment
        self._turn_done = asyncio.Event()  # set by segment monitor on complete response
        self._response_texts: list[str] = []  # transcribed text per segment
        self._mark_received = asyncio.Event()
        self._last_media_time: float = 0.0
        self._receiving = False
        # Timestamp watermark: only accumulate audio arriving after this time.
        # Set to 0.0 means "collect everything" (used for greeting).
        # Set to float('inf') means "collect nothing" (used while sending).
        self._turn_start_time: float = 0.0

        # Background tasks
        self._receiver_task: asyncio.Task | None = None
        self._segment_monitor_task: asyncio.Task | None = None

        # HTTP sidecar
        self._http_runner: web.AppRunner | None = None

        # Shutdown flag
        self._closed = False

    def _dbg(self, msg: str) -> None:
        if self._debug:
            print(f"[debug] {msg}", flush=True)  # noqa: T201

    def _is_turn_complete(self) -> bool:
        """Check if the agent's turn is complete based on transcribed segments."""
        if not self._response_texts:
            return False
        last = self._response_texts[-1]
        # Complete check first — a closing question at the end of a segment is a
        # stronger signal than a filler at the start (handles mixed segments like
        # "Let me check... anything else?")
        for pat in _COMPLETE_PATTERNS:
            if pat.search(last):
                return True
        # If last segment is only a filler phrase -> agent is mid-tool-call, keep waiting
        for pat in _FILLER_PATTERNS:
            if pat.search(last):
                return False
        # Ambiguous — fall back to segment count heuristic:
        # 2+ segments and the last one has substantive content (>50 chars)
        # means the real answer likely arrived after a filler. Done.
        if len(self._response_texts) >= 2 and len(last) > 50:
            return True
        # Single segment with no clear signal — not done yet
        return False

    def _load_payload(self) -> dict:
        """Load the AskRequest payload."""
        if self.payload_path:
            return json.loads(Path(self.payload_path).read_text())
        # Default: use the example voice payload
        example_path = Path(__file__).parent.parent / (
            "src/agent_leasing/api/example_data/resident/voice/example_ask_request_knck.json"
        )
        return json.loads(example_path.read_text())

    async def _connect(self) -> None:
        """Connect to the WebSocket and send connected + start events."""
        url = f"ws://localhost:{self.server_port}/media-stream/websocket"
        print(f"Connecting to {url}...", flush=True)  # noqa: T201

        self.ws = await websockets.connect(url, max_size=10 * 1024 * 1024)
        print("WebSocket connected", flush=True)  # noqa: T201

        # Send connected event
        await self.ws.send(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))

        # Build and send start event with payload
        payload = self._load_payload()
        payload["call_sid"] = self.call_sid
        payload["product_info"]["call_sid"] = self.call_sid
        # Disable recording for test sessions
        payload["product_info"]["should_record"] = False

        start_event = {
            "event": "start",
            "sequenceNumber": "1",
            "start": {
                "streamSid": self.stream_sid,
                "accountSid": "AC_test",
                "callSid": self.call_sid,
                "tracks": ["inbound"],
                "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
                "customParameters": {"payload": encode_object(payload)},
            },
            "streamSid": self.stream_sid,
        }
        await self.ws.send(json.dumps(start_event))
        print("Sent connected + start events", flush=True)  # noqa: T201

    async def _receiver_loop(self) -> None:
        """Background task: receive messages from the WebSocket."""
        assert self.ws is not None
        try:
            async for message in self.ws:
                if self._closed:
                    break
                try:
                    data = json.loads(message)
                except (json.JSONDecodeError, TypeError):
                    continue

                event = data.get("event")

                if event == "media":
                    # Server is sending us audio (agent response)
                    if time.monotonic() < self._turn_start_time:
                        continue
                    media = data.get("media", {})
                    audio_payload = media.get("payload", "")
                    if audio_payload:
                        audio_bytes = base64.b64decode(audio_payload)
                        # Filter out pure silence frames (mu-law 0xFF)
                        # The pacer sends silence to maintain cadence even when idle
                        is_silence = all(b == 0xFF for b in audio_bytes)
                        if not is_silence:
                            self._current_segment.extend(audio_bytes)
                            self._last_media_time = time.monotonic()
                            if not self._receiving:
                                self._dbg(f"First non-silence audio arrived ({len(audio_bytes)} bytes)")
                            self._receiving = True

                elif event == "mark":
                    # Echo mark back to server — Twilio does this when audio playback
                    # completes. The server's pacer tracks playback via marks; without
                    # echoing them, is_agent_speaking gets stuck True.
                    mark_name = data.get("mark", {}).get("name", "?")
                    self._dbg(f"Mark received: {mark_name} (echoing back)")
                    echo = {
                        "event": "mark",
                        "streamSid": self.stream_sid,
                        "mark": {"name": mark_name},
                    }
                    try:
                        await self.ws.send(json.dumps(echo))
                    except Exception:
                        pass
                    self._mark_received.set()

                elif event == "clear":
                    # Server is clearing audio (interruption) — discard in-progress
                    # segment only; already-finalized segments are kept
                    self._dbg("Clear event received")
                    self._current_segment.clear()

        except websockets.exceptions.ConnectionClosed:
            if not self._closed:
                print("WebSocket connection closed unexpectedly", flush=True)  # noqa: T201
        except Exception as e:
            if not self._closed:
                print(f"Receiver error: {e}", flush=True)  # noqa: T201

    async def _finalize_segment(self, label: str = "finalized") -> None:
        """Snapshot current segment, transcribe it, and check for turn completion."""
        segment = bytearray(self._current_segment)
        self._current_segment.clear()
        self._segments.append(segment)
        seg_idx = len(self._segments) - 1
        self._dbg(f"Segment {seg_idx} {label}: {len(segment)} bytes ({len(segment) / TWILIO_SAMPLE_RATE:.1f}s)")
        text = await self._transcribe_audio(bytes(segment))
        if text:
            self._response_texts.append(text)
            print(f'>> Agent: "{text}"', flush=True)  # noqa: T201
            if self._is_turn_complete():
                self._dbg("Turn complete (detected from transcript)")
                self._turn_done.set()

    async def _segment_monitor(self) -> None:
        """Background task: finalize and transcribe segments as they complete."""
        try:
            while not self._closed:
                await asyncio.sleep(0.2)
                if (
                    self._current_segment
                    and self._last_media_time > 0
                    and (time.monotonic() - self._last_media_time) >= self._segment_gap
                ):
                    await self._finalize_segment()
        except asyncio.CancelledError:
            pass

    async def _wait_for_response(self) -> str:
        """Wait for the agent's full response (all segments), return joined text."""
        start = time.monotonic()

        # Phase 1: Wait for first non-silence audio to arrive
        while not self._receiving and (time.monotonic() - start) < self.turn_timeout:
            await asyncio.sleep(0.1)

        if not self._receiving:
            return ""

        # Phase 2: Wait for turn completion.
        # The segment monitor sets _turn_done when it detects a complete response.
        # Safety silence timeout catches cases where patterns don't match.
        safety_silence = 30.0  # no audio for 30s = definitely done
        grace_done = False
        while (time.monotonic() - start) < self.turn_timeout:
            # Check if segment monitor flagged completion
            if self._turn_done.is_set() and not self._current_segment:
                if not grace_done:
                    # One-time grace period for trailing audio
                    await asyncio.sleep(1.0)
                    grace_done = True
                    if not self._current_segment:
                        break
                    continue
                else:
                    break
            # Safety: if silence exceeds threshold, bail
            elapsed_since_last = time.monotonic() - self._last_media_time
            if not self._current_segment and elapsed_since_last >= safety_silence:
                self._dbg(f"Safety silence timeout ({safety_silence}s) — ending turn")
                break
            await asyncio.sleep(0.3)

        # Flush: if there's still leftover audio in the current segment, finalize it
        if self._current_segment:
            await self._finalize_segment(label="flushed")

        return " ".join(self._response_texts)

    async def _text_to_ulaw(self, text: str) -> bytes:
        """Convert text to G.711 mu-law audio via OpenAI TTS."""
        response = await self.openai_client.audio.speech.create(
            model="tts-1",
            voice="alloy",
            input=text,
            response_format="pcm",  # 24kHz 16-bit PCM
        )
        pcm_24k = response.content

        # Downsample 24kHz -> 8kHz
        pcm_array = np.frombuffer(pcm_24k, dtype=np.int16)
        gcd = math.gcd(24000, TWILIO_SAMPLE_RATE)
        down_ratio = 24000 // gcd
        up_ratio = TWILIO_SAMPLE_RATE // gcd
        pcm_8k = resample_poly(pcm_array.astype(np.float32), up=up_ratio, down=down_ratio)
        pcm_8k = np.clip(pcm_8k, -32768, 32767).astype(np.int16)

        # PCM16 -> mu-law
        pcm_bytes = pcm_8k.tobytes()
        return pcm16_to_ulaw(pcm_bytes)

    async def _send_audio_frames(self, ulaw_audio: bytes, trailing_silence_s: float = 1.0) -> None:
        """Send mu-law audio as Twilio media events, followed by trailing silence.

        Real Twilio streams send continuous frames even when the user isn't talking.
        The trailing silence gives the server's VAD a clean end-of-speech signal.
        """
        assert self.ws is not None
        seq = 2  # start after the start event
        for offset in range(0, len(ulaw_audio), FRAME_BYTES):
            chunk = ulaw_audio[offset : offset + FRAME_BYTES]
            if len(chunk) < FRAME_BYTES:
                # Pad last frame with mu-law silence (0xFF)
                chunk = chunk + bytes([0xFF]) * (FRAME_BYTES - len(chunk))

            media_event = {
                "event": "media",
                "sequenceNumber": str(seq),
                "media": {
                    "track": "inbound",
                    "chunk": str(seq),
                    "timestamp": str(int((seq - 2) * FRAME_DURATION_S * 1000)),
                    "payload": base64.b64encode(chunk).decode("utf-8"),
                },
                "streamSid": self.stream_sid,
            }
            await self.ws.send(json.dumps(media_event))
            seq += 1
            # Pace at roughly real-time to avoid overwhelming buffers
            await asyncio.sleep(FRAME_DURATION_S)  # 1x realtime pacing

        # Send trailing silence frames so VAD detects end-of-speech
        silence_frame = bytes([0xFF]) * FRAME_BYTES
        n_silence = int(trailing_silence_s / FRAME_DURATION_S)
        for _ in range(n_silence):
            media_event = {
                "event": "media",
                "sequenceNumber": str(seq),
                "media": {
                    "track": "inbound",
                    "chunk": str(seq),
                    "timestamp": str(int((seq - 2) * FRAME_DURATION_S * 1000)),
                    "payload": base64.b64encode(silence_frame).decode("utf-8"),
                },
                "streamSid": self.stream_sid,
            }
            await self.ws.send(json.dumps(media_event))
            seq += 1
            await asyncio.sleep(FRAME_DURATION_S)

    async def _transcribe_audio(self, ulaw_audio: bytes) -> str:
        """Transcribe mu-law audio via OpenAI Whisper."""
        if not ulaw_audio:
            return ""

        # mu-law -> PCM16
        pcm_bytes = ulaw_to_pcm16(ulaw_audio)

        # Build WAV in memory for Whisper
        wav_buffer = io.BytesIO()
        data_size = len(pcm_bytes)
        # WAV header
        wav_buffer.write(b"RIFF")
        wav_buffer.write(struct.pack("<I", 36 + data_size))
        wav_buffer.write(b"WAVE")
        wav_buffer.write(b"fmt ")
        wav_buffer.write(struct.pack("<I", 16))  # chunk size
        wav_buffer.write(struct.pack("<H", 1))  # PCM format
        wav_buffer.write(struct.pack("<H", 1))  # mono
        wav_buffer.write(struct.pack("<I", TWILIO_SAMPLE_RATE))  # sample rate
        wav_buffer.write(struct.pack("<I", TWILIO_SAMPLE_RATE * 2))  # byte rate
        wav_buffer.write(struct.pack("<H", 2))  # block align
        wav_buffer.write(struct.pack("<H", 16))  # bits per sample
        wav_buffer.write(b"data")
        wav_buffer.write(struct.pack("<I", data_size))
        wav_buffer.write(pcm_bytes)
        wav_buffer.seek(0)

        # Send to Whisper
        transcript = await self.openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=("audio.wav", wav_buffer, "audio/wav"),
        )
        return transcript.text.strip()

    async def send_message(self, text: str) -> dict:
        """Send a text message: TTS -> mu-law -> WS, then collect + transcribe response."""
        start_time = time.monotonic()

        # Block collection while we reset + send (inf = collect nothing)
        self._turn_start_time = float("inf")
        self._segments.clear()
        self._current_segment = bytearray()
        self._response_texts.clear()
        self._mark_received.clear()
        self._turn_done.clear()
        self._receiving = False

        # TTS -> mu-law
        self._dbg(f"TTS converting: '{text}'")
        ulaw_audio = await self._text_to_ulaw(text)
        self._dbg(f"TTS done: {len(ulaw_audio)} bytes mu-law ({len(ulaw_audio) / TWILIO_SAMPLE_RATE:.1f}s)")

        # Send audio frames (includes trailing silence for VAD end-of-speech).
        # Set the watermark right after so receiver starts collecting response audio.
        await self._send_audio_frames(ulaw_audio)
        self._turn_start_time = time.monotonic()
        self._dbg("Audio + trailing silence sent, collecting response...")

        # Wait for response — segments print incrementally via _segment_monitor
        self._dbg("Waiting for response audio...")
        response_text = await self._wait_for_response()

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        # Record in history
        self.history.append({"role": "user", "text": text})
        if response_text:
            self.history.append({"role": "assistant", "text": response_text})

        return {"response": response_text, "timing_ms": elapsed_ms}

    async def wait_for_agent(self, timeout: float | None = None) -> dict:
        """Wait for the next agent utterance without sending audio.

        Simulates a silent caller: the WebSocket stays open and the server's
        inactivity loop (8s default `filler_delay_mean_seconds`) eventually
        fires a filler/handoff prompt that triggers the agent to speak.
        """
        start_time = time.monotonic()
        prior_timeout = self.turn_timeout
        if timeout is not None:
            self.turn_timeout = timeout

        # Reset collection state (same dance as send_message), then mark "collect from now"
        self._segments.clear()
        self._current_segment = bytearray()
        self._response_texts.clear()
        self._mark_received.clear()
        self._turn_done.clear()
        self._receiving = False
        self._turn_start_time = time.monotonic()

        try:
            response_text = await self._wait_for_response()
        finally:
            self.turn_timeout = prior_timeout

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        if response_text:
            self.history.append({"role": "assistant", "text": response_text})
        return {"response": response_text, "timing_ms": elapsed_ms}

    async def close(self) -> None:
        """Send stop event and close everything."""
        self._closed = True

        if self.ws and self.ws.close_code is None:
            try:
                await self.ws.send(json.dumps({"event": "stop", "streamSid": self.stream_sid}))
                await asyncio.sleep(0.5)
                await self.ws.close()
            except Exception:
                pass

        if self._receiver_task and not self._receiver_task.done():
            self._receiver_task.cancel()
            try:
                await self._receiver_task
            except (asyncio.CancelledError, Exception):
                pass

        if self._segment_monitor_task and not self._segment_monitor_task.done():
            self._segment_monitor_task.cancel()
            try:
                await self._segment_monitor_task
            except (asyncio.CancelledError, Exception):
                pass

        if self._http_runner:
            await self._http_runner.cleanup()

    # --- HTTP Sidecar ---

    def _build_http_app(self) -> web.Application:
        """Build the aiohttp sidecar app."""
        app = web.Application()
        app.router.add_post("/send", self._handle_send)
        app.router.add_post("/wait", self._handle_wait)
        app.router.add_get("/history", self._handle_history)
        app.router.add_post("/close", self._handle_close)
        return app

    async def _handle_send(self, request: web.Request) -> web.Response:
        """POST /send — send a message and return the response."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON body"}, status=400)

        text = body.get("text", "")
        if not text:
            return web.json_response({"error": "Missing 'text' field"}, status=400)

        try:
            result = await self.send_message(text)
            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_wait(self, request: web.Request) -> web.Response:
        """POST /wait — wait for the next agent utterance without sending audio.

        Body: `{"timeout": <seconds, optional>}`. Used to simulate a silent caller.
        """
        timeout: float | None = None
        try:
            body = await request.json()
            if "timeout" in body:
                timeout = float(body["timeout"])
        except Exception:
            pass

        try:
            result = await self.wait_for_agent(timeout=timeout)
            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_history(self, request: web.Request) -> web.Response:
        """GET /history — return conversation history."""
        return web.json_response(self.history)

    async def _handle_close(self, request: web.Request) -> web.Response:
        """POST /close — close the session."""
        asyncio.get_event_loop().call_soon(lambda: asyncio.create_task(self._shutdown()))
        return web.json_response({"status": "closed"})

    async def _shutdown(self) -> None:
        """Graceful shutdown."""
        await self.close()

    async def start(self) -> None:
        """Connect to WS, collect greeting, start HTTP sidecar."""
        # Connect and send protocol events
        await self._connect()

        # Start background receiver + segment monitor, begin collecting greeting audio
        self._turn_start_time = 0.0
        self._turn_done.clear()
        self._receiver_task = asyncio.create_task(self._receiver_loop())
        self._segment_monitor_task = asyncio.create_task(self._segment_monitor())

        # Wait for initial greeting — segments print incrementally via _segment_monitor
        print("Waiting for greeting...", flush=True)  # noqa: T201
        greeting_text = await self._wait_for_response()

        if greeting_text:
            self.history.append({"role": "assistant", "text": greeting_text})
            print(f'READY -- greeting: "{greeting_text}"', flush=True)  # noqa: T201
        else:
            print("READY -- (no greeting detected)", flush=True)  # noqa: T201

        # Start HTTP sidecar
        app = self._build_http_app()
        self._http_runner = web.AppRunner(app)
        await self._http_runner.setup()
        site = web.TCPSite(self._http_runner, "localhost", self.http_port)
        await site.start()
        print(f"Sidecar HTTP server on http://localhost:{self.http_port}", flush=True)  # noqa: T201

        # Keep running until closed
        try:
            while not self._closed:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.close()


def main():
    # Load .env from project root (same OPENAI_API_KEY the server uses)
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent / ".env")

    parser = argparse.ArgumentParser(description="Voice interactive test client")
    parser.add_argument("--port", type=int, required=True, help="Server port (e.g. 8100)")
    parser.add_argument("--http-port", type=int, default=9090, help="Sidecar HTTP port (default: 9090)")
    parser.add_argument("--payload", type=str, default=None, help="Path to custom AskRequest JSON payload")
    parser.add_argument("--timeout", type=float, default=30.0, help="Turn timeout in seconds (default: 30)")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    args = parser.parse_args()

    client = VoiceTestClient(
        server_port=args.port,
        http_port=args.http_port,
        payload_path=args.payload,
        turn_timeout=args.timeout,
        debug=args.debug,
    )

    try:
        asyncio.run(client.start())
    except KeyboardInterrupt:
        print("\nShutting down...", flush=True)  # noqa: T201


if __name__ == "__main__":
    main()
