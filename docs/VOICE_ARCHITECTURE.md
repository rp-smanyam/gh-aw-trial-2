# Voice Architecture

> **Rollout model**: Two WebSocket endpoints coexist; per-call routing
> is driven upstream by cai-genai-service via a feature flag, bucketed by
> `property_id`.
>
> - `WS /media-stream/websocket` → v1 (`twilio_handler.py`, always).
> - `WS /media-stream/websocket/v2` → refactored `src/agent_leasing/voice/`
>   package (when `USE_VOICE_REFACTOR=true`; otherwise silently falls
>   back to v1 as an agent-leasing-side kill-switch).
>
> The old implementation is documented in [VOICE_INTERACTION.md](VOICE_INTERACTION.md)
> and remains fully functional regardless of flag state.

## Why

`twilio_handler.py` grew to 2,100+ lines — a single class managing the
full lifecycle of a voice call.  It became too brittle to modify safely.
KNCK-39531 refactors it into a modular package that is easier to reason
about, test, and extend.

## Package overview

```
src/agent_leasing/voice/
├── config.py              VoiceConfig — typed settings snapshot
├── callbacks.py           VoiceCallbacks protocol (agent ↔ handler contract)
├── handler.py             VoiceHandler orchestrator + VoiceHandlerManager
├── agent.py               VoiceAgent — wraps ResidentRealtimeResponderAgent
│
├── transport/
│   ├── protocol.py        VoiceTransport + PlaybackNotifier protocols
│   ├── twilio.py          TwilioTransport implementation
│   └── types.py           TransportEvent, CallMetadata
│
├── audio/
│   ├── pacer.py           Frame-level output timing
│   ├── buffer.py          Input buffering + flush to OpenAI
│   ├── playback.py        Mark-based playback tracking
│   └── noise_reduction.py Thin wrapper for input noise reduction
│
├── session/
│   ├── manager.py         OpenAI RealtimeSession lifecycle
│   ├── recovery.py        Crash recovery with history replay
│   └── response_gate.py   Serializes response.create + stale detection
│
├── vad/
│   ├── protocol.py        VADProvider protocol (pluggable)
│   └── openai_vad.py      Default server-side VAD
│
├── filler/
│   ├── manager.py         Scheduling, selection, delivery, dead-line
│   └── messages.py        Three-tier message templates
│
├── coordination/
│   ├── call_state.py      Async Event-based state machine
│   ├── event_dispatcher.py Inline audio + deferred non-audio
│   ├── interrupt.py       Orchestrated barge-in sequence
│   └── interaction_policy.py Greeting / ESR / Default policies
│
├── lifecycle/
│   ├── setup.py           Transfer on validation failure
│   ├── recording.py       Twilio dual-channel recording
│   ├── cleanup.py         Task cancellation + shielded close
│   └── data_curation.py   Kafka event logging
│
├── tracing/
│   └── langsmith.py       LangSmith child runs with playback timestamps
│
└── thinker/
    └── tool.py            Thinker tool factory using VoiceCallbacks
```

## Key design decisions

### Transport abstraction

`VoiceTransport` is a protocol implemented by `TwilioTransport` today.
A future `WebRTCTransport` would implement the same protocol with aiortc.
Playback tracking is abstracted as `request_playback_notification` /
`on_playback_milestone` — Twilio uses marks, WebRTC would use drain events.

### No circular dependencies

The old path coupled handler ↔ agent through `ctx._session_handler`.
The new path uses `VoiceCallbacks` — a protocol the handler implements and
the thinker tool receives at construction.  No back-references through
context objects.

### Deferred event queue

Audio events (latency-critical) are processed inline.  Non-audio events
(history, guardrails, tracing) go through an asyncio.Queue to a background
task, preventing slow handlers from blocking audio delivery.

### Response serialization

`ResponseGate` uses a lock + event + monotonic `turn_id` to prevent
overlapping `response.create` calls and detect stale thinker results
after user interrupts.

### Filler as first-class component

`FillerManager` owns scheduling (Gaussian-distributed), message selection
(three-tier escalation), delivery, and dead-line detection.  Single toggle:
`VOICE_FILLERS_ENABLED`.  No filler logic in the handler or agent.

### Interaction policies

Strategy pattern replaces boolean flags (`_is_initial_greeting`,
`_esr_suppression_active`) with composable policy objects that transition
on playback completion.

### VAD pluggability

`VADProvider` protocol allows swapping OpenAI's server-side VAD with a
local implementation (e.g. TEN VAD for Nick Lackman's approach) without
changing the audio pipeline.

## Settings

New settings use a `VOICE_` prefix and default to `None` (falling back
to the existing setting).  For example:

| New setting | Falls back to |
|---|---|
| `VOICE_FILLERS_ENABLED` | `send_filler_messages` |
| `VOICE_FILLER_DELAY_MEAN_SECONDS` | `filler_delay_mean_seconds` |
| `VOICE_PACER_TICK_SECONDS` | (default 0.04) |
| `VOICE_NOISE_REDUCTION_ENABLED` | `twilio_input_audio_noise_reduction_enabled` |

The full list is in `settings.py` under the `Voice refactor (KNCK-39531)` section.

## Testing

```shell
# New voice package tests
uv run pytest tests/unit/voice/

# Existing tests (must still pass — flag defaults to off)
uv run pytest tests/unit/test_twilio_handler*.py
uv run pytest tests/unit/test_filler_escalation.py
```

## Enabling

```shell
USE_VOICE_REFACTOR=true uv run server
```

This activates the `/media-stream/websocket/v2` endpoint.  Route a test
call to the v2 path (either hit it directly or flip the upstream
feature flag) and verify: greeting plays, user can speak and get responses,
fillers fire, interrupts work, call ends cleanly, LangSmith trace
appears.  When the flag is off, v2 exists but silently routes incoming
WebSockets to the v1 manager.
