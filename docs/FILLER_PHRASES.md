# Filler Phrases in Voice Interactions

## Overview

Filler phrases are automated messages that the voice agent sends during extended silence to keep users engaged and informed. They serve as audio feedback that the agent is still processing, preventing users from thinking the call has disconnected or the system has frozen.

## Purpose

In voice interactions, silence longer than a few seconds creates a poor user experience. Filler phrases address this by:

1. **Maintaining Engagement**: Letting users know the agent is still working
2. **Setting Expectations**: Indicating that processing is taking longer than usual
3. **Preventing Hangups**: Reducing the likelihood users disconnect due to perceived inactivity
4. **Professional UX**: Creating a more human-like conversational experience

## How Filler Phrases Work

### Triggering Mechanism

The filler phrase system monitors audio activity and triggers messages after a configurable period of silence:

1. **Activity Tracking**: The system tracks audio activity from both user and agent
2. **Timeout Calculation**: When activity occurs, a random delay is calculated using a Gaussian distribution
3. **Scheduled Send**: If no new activity occurs before the timeout, a filler message is sent
4. **Rescheduling**: After any audio event (user speech, agent response, filler), a new timeout is scheduled

**Implementation**: `src/agent_leasing/twilio_handler.py`

```python
def _schedule_next_filler(self) -> None:
    """Schedule the next filler message based on configured timing."""
    if not settings.send_filler_messages:
        self._next_filler_time = None
        return

    self._last_audio_time = time.time()

    # Random delay with Gaussian distribution
    mean = max(settings.filler_delay_mean_seconds, 0.0)
    std = max(settings.filler_delay_std_seconds, 0.0)
    delay = max(random.gauss(mean, std), 1)

    self._next_filler_time = self._last_audio_time + delay
```

### Three-Tier Filler Messages

Filler message selection is deterministic based on `thinker_running` state and escalation threshold:

| Condition | Message | Purpose |
|-----------|---------|---------|
| `thinker_running=True` | `FILLER_THINKER_ACTIVE_MESSAGE` | "Still working on it" — thinker is processing |
| `thinker_running=False`, below threshold | `FILLER_IDLE_MESSAGE` | "Still here" + soft nudge to call thinker if there's an unaddressed request |
| `thinker_running=False`, at/above threshold | `FILLER_ESCALATION_MESSAGE` | **CRITICAL** — force the model to review conversation and call thinker |

The model never sees the wrong message — Python selects it based on known state.

**Constants**: `src/agent_leasing/twilio_handler.py`

#### 1. Thinker Active (thinker is processing)

```python
FILLER_THINKER_ACTIVE_MESSAGE = """
**IMPORTANT**: Do not acknowledge this message.
Just send a short, natural filler line in {language_code}.
Vary the tone and word choice each time so it does not sound scripted.
We are waiting for a tool or internal action to finish. Deliver a fresh paraphrase of:
`I'm still working on that—it'll just be a little bit longer.`
"""
```

#### 2. Idle (thinker not running, below escalation threshold)

```python
FILLER_IDLE_MESSAGE = """
**IMPORTANT**: Do not acknowledge this message.
Just send a short, natural filler line in {language_code}.
Vary the tone and word choice each time so it does not sound scripted, and reference any relevant context when it helps.
1) If the resident asked a question or made a request that hasn't been addressed yet, call `resident_thinker_tool` with a summary of their request instead of sending a filler.
2) Otherwise, deliver a new paraphrase of:
`I'm still here for you—let me know if there's anything else I can help with.`
"""
```

#### 3. Escalation (thinker not running, at/above threshold)

```python
FILLER_ESCALATION_MESSAGE = """
**CRITICAL**: You have been sending filler messages for a while. Review the conversation NOW.
Respond in {language_code}.
- If the resident provided information, asked a question, or made a request that hasn't been fully resolved
  with a tool call, you MUST call `resident_thinker_tool` NOW with a summary of their request.
- If the resident's last request was already completed and you are waiting for them to speak, say:
  "I'm still here — is there anything else I can help you with?"
Do NOT send another "please wait" filler unless a tool is actively processing.
"""
```

### Filler Escalation

The escalation mechanism prevents infinite filler loops when the Realtime model fails to call the thinker tool after a user request. This was observed in production where the responder would send filler messages for 3-9 minutes with zero backend work.

**Root cause**: The OpenAI Realtime model sometimes decides not to emit a `resident_thinker_tool` function call after delivering a previous response. Each filler triggers another filler, creating an infinite loop.

**How escalation works**:

1. Fillers 1 through `threshold-1`: normal filler (thinker active or idle with soft nudge)
2. Fillers at `threshold` and above (when `thinker_running=False`): escalation message that asks the model to review the conversation and call the thinker
3. Fillers at `max_consecutive_fillers_without_user_audio` (default 5): dead line detection terminates the call

**The `thinker_running` flag** lives on `SessionScope` and is set/cleared by Python code in `create_thinker_tool()` — not by audio events. This makes it reliable regardless of background noise or VAD false positives.

```python
# In _send_input_audio_timeout_message():
thinker_running = getattr(self.ctx, "thinker_running", False)
should_escalate = (
    settings.filler_escalation_enabled
    and self._consecutive_fillers_without_user_audio >= settings.filler_escalation_threshold
    and not thinker_running
)

if should_escalate:
    message = FILLER_ESCALATION_MESSAGE.format(language_code=...)
elif thinker_running:
    message = FILLER_THINKER_ACTIVE_MESSAGE.format(language_code=...)
else:
    message = FILLER_IDLE_MESSAGE.format(language_code=...)
```

## Configuration

### Basic Settings

**File**: `src/agent_leasing/settings.py`

```python
# Enable/disable filler messages
send_filler_messages: bool = True

# Timeout before sending filler (Gaussian distribution)
filler_delay_mean_seconds: float = 8.0   # First filler after ~8s silence
filler_delay_std_seconds: float = 1.5    # Tight variance for predictable 4.5-7.5s range

# Dead line detection — terminate call after N consecutive fillers without user audio
max_consecutive_fillers_without_user_audio: int = 5
```

### Filler Escalation Settings

```python
# Filler Escalation - nudge responder to call thinker when stuck in filler loop
filler_escalation_enabled: bool = True   # Kill switch for escalation behavior
filler_escalation_threshold: int = 2     # Escalate after N fillers with no thinker running
```

Threshold of 2 means: filler 1 is normal, filler 2+ escalates (if thinker not running). The counter increments before comparison, so the second filler already hits the threshold. With 8s filler mean, escalation hits ~16s into a stall.

**Infra env vars**: `FILLER_ESCALATION_ENABLED`, `FILLER_ESCALATION_THRESHOLD` (in all environments)

### Filler Handling Strategies

When the thinker agent completes while a filler is still playing, the system needs to handle the overlap. Three strategies are available:

```python
# Strategy selection
filler_handling_strategy: Literal["cancel", "wait", "hybrid"] = "hybrid"

# Configuration for wait-based strategies
filler_wait_timeout_seconds: float = 1.5
```

## Handling Strategies

### 1. Cancel Strategy

**Behavior**: Immediately force-cancels any active filler when thinker completes.

**Use Case**: Low-latency environments where speed is prioritized over smooth transitions.

**Tradeoff**: May cut off filler mid-sentence, creating a jarring experience.

### 2. Wait Strategy

**Behavior**: Waits for filler to complete naturally before sending thinker response (up to 5s safety timeout).

**Use Case**: High-quality voice experiences where natural conversation flow is critical.

**Tradeoff**: May add 2-5 seconds of latency to thinker responses.

### 3. Hybrid Strategy (Default)

**Behavior**: Waits for filler completion up to `filler_wait_timeout_seconds`, then force-cancels if still active.

**Use Case**: Production environments requiring balance between smoothness and responsiveness.

**Tradeoff**: Short fillers complete naturally; long fillers get cancelled.

## Architecture

### Component Overview

```
TwilioHandler (twilio_handler.py)
├── _schedule_next_filler()              # Schedules next filler timeout
├── _input_audio_inactivity_loop()       # Monitors for silence
└── _send_input_audio_timeout_message()  # Selects and sends filler message
    ├── thinker_running=True  → FILLER_THINKER_ACTIVE_MESSAGE
    ├── thinker_running=False → FILLER_IDLE_MESSAGE (with soft nudge)
    └── above threshold       → FILLER_ESCALATION_MESSAGE

ResidentRealtimeResponderAgent (realtime.py)
└── create_thinker_tool()
    └── resident_thinker_tool()
        ├── context.thinker_running = True   (before processing)
        ├── context.thinker_running = False  (in finally block)
        └── _handle_filler_before_thinker_response()
            ├── _cancel_active_filler()       # Cancel strategy
            ├── _wait_for_filler_completion()  # Wait strategy
            └── Hybrid logic                  # Hybrid strategy

SessionScope (models/context.py)
└── thinker_running: bool = False  # Set by thinker tool, read by twilio_handler
```

### State Tracking

The `TwilioHandler` maintains several state variables:

```python
# Timing
self._last_audio_time: float          # Last audio activity timestamp
self._next_filler_time: float | None  # When next filler should trigger

# Filler counter
self._consecutive_fillers_without_user_audio: int  # Resets on user audio

# Speaking state (via CallStateManager)
self.is_agent_speaking: bool          # True when agent audio is playing
self.is_user_speaking: bool           # True during user speech

# Thinker state (on SessionScope)
self.ctx.thinker_running: bool        # True while thinker tool is processing

# Session reference (for thinker tool access)
self.ctx._session_handler = self      # Set in _agent_setup()
```

### Event Flow

1. **Audio Activity** → `_schedule_next_filler()` → Sets `_next_filler_time`
2. **Inactivity Loop** → Checks `time.time() >= _next_filler_time`
3. **Timeout Reached** → `_send_input_audio_timeout_message()`
4. **Message Selected** → Based on `thinker_running` and `consecutive_fillers`
5. **Filler Sent** → `session.send_message(message)`
6. **Audio Starts** → `is_agent_speaking = True` (in `_handle_realtime_audio_event`)
7. **Audio Completes** → `is_agent_speaking = False` (in `_on_response_completed`)

### Thinker Integration

When the thinker tool completes, it must coordinate with any active filler:

```python
# BEFORE sending thinker response, handle any active filler based on configured strategy
if hasattr(run_context.context, "_session_handler"):
    handler = run_context.context._session_handler
    await _handle_filler_before_thinker_response(handler, logger)
```

## Examples

### Scenario 1: No Filler Needed (Fast Response)

```
User: "What's my rent balance?"
→ Thinker starts (0.5s processing)
→ No filler triggered (< 8s threshold)
→ Response: "Your balance is $1,200"
```

### Scenario 2: Filler During Thinker Processing

```
User: "What service requests are open?"
→ Thinker starts (12s total)
→ Filler triggers at ~8s: "I'm still working on that..." (FILLER_THINKER_ACTIVE_MESSAGE)
→ Thinker completes at 12s, filler handled via hybrid strategy
→ Response: "You have 2 open service requests..."
```

### Scenario 3: Filler Escalation (Bug — Thinker Never Called)

```
User: "Can you check my packages?"
→ Responder delivers previous answer but doesn't call thinker for new request
→ Filler 1 at ~8s: "I'm still here for you..." (FILLER_IDLE_MESSAGE with soft nudge)
→ Filler 2 at ~16s: "**CRITICAL** Review conversation..." (FILLER_ESCALATION_MESSAGE)
→ Model reconsiders, calls thinker → "Let me look into your packages..."
```

Without escalation, this would loop for 3-9 minutes (observed in prod).

### Scenario 4: Normal Silence (User Thinking)

```
→ Responder delivered answer, waiting for user
→ Filler 1: "I'm still here..." (FILLER_IDLE_MESSAGE — soft nudge, model sees nothing pending)
→ Filler 2: Escalation fires — model checks, sees nothing pending, says "Is there anything else?"
→ User speaks → counter resets
```

## Testing

### Testing Different Strategies

Create a `.env` file with different configurations:

```bash
# Test cancel strategy
FILLER_HANDLING_STRATEGY=cancel

# Test wait strategy
FILLER_HANDLING_STRATEGY=wait

# Test hybrid with different timeouts
FILLER_HANDLING_STRATEGY=hybrid
FILLER_WAIT_TIMEOUT_SECONDS=2.0  # More patient
# OR
FILLER_WAIT_TIMEOUT_SECONDS=1.0  # More aggressive

# Test escalation
FILLER_ESCALATION_ENABLED=true
FILLER_ESCALATION_THRESHOLD=2
```

### Monitoring in Production

Search logs for filler activity:

```bash
# Check filler message types
grep "Sending filler message" logs.txt

# Monitor escalation events
grep "Filler escalation triggered" logs.txt

# Monitor strategy execution
grep "Handling filler with strategy" logs.txt

# Monitor natural completions vs cancellations
grep "Filler completed naturally" logs.txt | wc -l
grep "forcing cancel" logs.txt | wc -l
```

## Tuning Guidelines

### Production Recommendations

1. **Start with defaults**:
   - `filler_handling_strategy = "hybrid"`
   - `filler_wait_timeout_seconds = 1.5`
   - `filler_escalation_enabled = True`
   - `filler_escalation_threshold = 2`

2. **Monitor metrics**:
   - Track filler completion ratio
   - Track escalation frequency (should be low — high frequency means the model is frequently not calling thinker)
   - Measure average thinker response times

3. **Adjust based on data**:
   - If escalation fires too often → investigate why model isn't calling thinker
   - If escalation fires too rarely → threshold might be too high
   - If most fillers are short (< 1s), decrease wait timeout to 1.0s
   - If most fillers are long (> 2s), increase wait timeout to 2.0s or switch to "wait"

## Related Files

| File | Purpose |
|------|---------|
| `src/agent_leasing/settings.py` | Filler and escalation configuration settings |
| `src/agent_leasing/twilio_handler.py` | Filler scheduling, message selection, sending |
| `src/agent_leasing/agent/resident_one_agent/realtime.py` | Thinker tool (`thinker_running` flag), filler handling strategies |
| `src/agent_leasing/models/context.py` | `SessionScope.thinker_running` field |
| `tests/unit/test_filler_escalation.py` | Three-tier filler message selection tests |
| `tests/unit/agent/resident_one_agent/test_realtime.py` | Filler handling strategy tests |
| `tests/unit/agent/resident_one_agent/test_thinker_concurrency.py` | Thinker concurrency guard tests |

## Troubleshooting

### Fillers Not Triggering

**Check**:
1. `send_filler_messages = True` in settings
2. Session is initialized (`_session_ready.is_set()`)
3. User is not currently speaking (`is_user_speaking = False`)
4. Silence duration exceeds `filler_delay_mean_seconds`

### Filler Loop (Model Not Calling Thinker)

**Symptom**: Logs show many consecutive fillers with `thinker_running=False`, eventually escalation or dead line detection fires.

**Diagnosis**:
1. Check for "Filler escalation triggered" in logs
2. Check if escalation successfully prompted the model to call thinker
3. If escalation fails repeatedly, the dead line detector (5 fillers) terminates the call

**Solutions**:
- Lower `filler_escalation_threshold` for earlier intervention
- Review `VOICE_RESPONDER.md` prompt for anti-patterns
- Check if a specific conversation pattern consistently triggers the bug

### Fillers Always Get Cancelled

**Possible Causes**:
1. `filler_wait_timeout_seconds` is too low (< 1s)
2. Fillers are consistently long (> 2s)
3. Strategy is set to "cancel"

**Solutions**:
- Increase `filler_wait_timeout_seconds`
- Switch to "wait" strategy
- Optimize filler prompt for shorter responses
