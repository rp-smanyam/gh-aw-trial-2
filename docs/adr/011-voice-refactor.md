# 11. Voice Handler Refactor

## Status

Accepted (2026-04-16)

## Context

`twilio_handler.py` is a 2,100-line monolithic class that manages the entire
lifecycle of a voice call: Twilio WebSocket I/O, OpenAI Realtime session
management, audio pacing, filler scheduling, LangSmith tracing, call
recording, error recovery, and cleanup.  The class has ~50 instance
variables, ~45 methods, and a circular dependency with `realtime.py`
through `ctx._session_handler`.  It has become too brittle to modify safely.

Several prior refactoring attempts were examined:
- **PR #598**: Component decomposition (AudioBuffer, AudioPacer, etc.) but
  the handler remained 892 lines with no transport abstraction.
- **VJ's branch**: Full parallel rewrite with transport ABC and deferred
  event queue, but too ambitious (no tests, no migration path).
- **Agentix/renter-ai-agent**: Production-stable modular architecture with
  stream merging and queue-based tool execution.
- **Nick Lackman's responder-thinker**: WebRTC + semantic VAD with explicit
  turn boundary control and multiple concurrent thinkers.

## Decision

Build a new modular voice package (`src/agent_leasing/voice/`) alongside the
existing code, gated by a `USE_VOICE_REFACTOR` feature flag.  The old
`twilio_handler.py` stays intact and functional.

Key architectural choices:
- **Transport protocol** (`VoiceTransport`) decouples Twilio specifics so
  WebRTC can be supported later without changing call logic.
- **VoiceCallbacks protocol** replaces `ctx._session_handler` — breaks the
  circular dependency with a clean contract.
- **Deferred event queue** (from VJ's branch) keeps audio latency-critical
  by processing non-audio events in a background task.
- **ResponseGate** (from Nick Lackman + Agentix) serializes `response.create`
  calls and provides monotonic `turn_id` for stale result detection.
- **InteractionPolicy** strategy pattern replaces boolean flags for greeting
  and ESR interrupt suppression.
- **VADProvider** protocol allows future swap to local semantic VAD.
- **FillerManager** as a standalone first-class component with single toggle.

## Consequences

- The refactored code can be deployed with the flag off (zero risk).
- Toggling the flag on routes voice calls through the new package.
- The old `twilio_handler.py` and `realtime.py` remain untouched — existing
  tests continue to pass against them.
- After validation in production, `twilio_handler.py` can be decommissioned
  and the `VOICE_*` settings can replace the legacy settings.
