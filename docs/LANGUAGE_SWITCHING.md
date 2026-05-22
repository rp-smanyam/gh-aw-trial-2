# Language Handling

## Overview

The default language is English. On voice, transcription is pinned to English and the conversation stays in English unless the caller explicitly asks to switch. On non-voice channels, language is auto-detected from the user's first message.

## How It Works

### Voice

1. Input audio transcription is pinned to English (`language="en"` on `RealtimeInputAudioTranscriptionConfig`), so garbled audio is always transcribed as English rather than misidentified as another language.
2. The voice responder defaults to English and only switches when the caller explicitly asks (e.g., "Can you speak in Spanish?", "Habla español por favor").
3. When the caller requests a switch, the responder calls `set_conversation_language` with the new ISO 639-1 code, then responds in the new language.
4. The thinker (`INSTRUCTIONS.md`) follows the responder's language via `{{ context.language_code }}` in its prompt. It does not detect or switch language independently on voice.
5. Filler messages, guardrail system messages, and recovery messages use the current language via `_get_language_code()`, which reads from `SessionScope.language_code`.

### Explicit Language Switching (Voice)

If a caller explicitly asks to switch languages — in any language (e.g., "Can you speak in Spanish?", "日本語でいいですか？", "¿Puedes hablar en español?") — the responder:
1. Calls `set_conversation_language` with the new ISO 639-1 code
2. Only switches its spoken language after the tool returns `"ok"` — the tool is the gate, not a notification
3. All downstream components (thinker, fillers, guardrails) automatically follow via `SessionScope.language_code`

Accents, foreign names, and casual code-mixing do NOT trigger a switch.

### Chat / SMS / Email

1. The thinker reads `{{ context.language_code }}` from its prompt. On the first message this defaults to `"en"`.
2. If the user writes in a different language, the thinker detects it, responds in that language, and returns the new `language_code` in `ResidentResponderOutput`.
3. `server.py` writes the returned `language_code` back to `context.language_code`, which persists across messages via `SessionScope` caching.
4. On subsequent messages, the thinker uses the stored language. It only switches if the user explicitly asks (e.g., "Can you respond in Spanish?").

### Explicit Language Switching (Chat / SMS / Email)

If a user explicitly asks to switch languages — in any language (e.g., "Can you respond in Spanish?", "日本語でいいですか？") — the thinker:
1. Switches to the requested language
2. Returns the new `language_code` in `ResidentResponderOutput`
3. `server.py` persists the new code to `context.language_code` for subsequent messages

## Key Principles

- **Default English, switch only on explicit request**: The system defaults to English. Language only changes if the user explicitly requests a different language.
- **Responder is the authority on voice**: The responder hears the caller's audio directly and sets the language via `set_conversation_language`. The thinker follows it.
- **Accents are not language switches**: English words spoken with a foreign accent are still English. The prompt explicitly states that treating an accent as a language switch is offensive.
- **Foreign names are not language switches**: Names like "Anastasia" or "Amir" do not indicate a language change.
- **Personal data is not a language signal**: The resident's name, ethnicity, location, and phone number must never be used to infer language.
- **Garbled audio is not a language signal**: Unclear or unintelligible audio triggers the Out-of-Context workflow, not language detection.

## System Messages (Voice)

All system-injected messages reference `language_code` from `SessionScope` and automatically use the correct language once it's set by the responder:

| Message | Purpose |
|---------|---------|
| `GUARDRAIL_TRIPPED_MESSAGE` | Instructs agent to respond in `{language_code}` after a guardrail trips |
| `INPUT_AUDIO_TIMEOUT_MESSAGE` | Filler/keep-alive message sent in `{language_code}` |
| `RECOVERY_MESSAGE` | Crash recovery message instructs agent to respond in `{language_code}` |

## Guardrail Localization

Guardrail canned responses (`guardrails_responses.yaml`) are looked up by language code via `localize_guardrail_response()`. This is independent of the detection mechanism and continues to work as before.

## Per-Message Classification (Data Curation)

`realtime_util.py` classifies language per-message independently for data curation logging. This is separate from the session-level language detection and is unchanged.

## Key Files

| File | Role |
|------|------|
| `src/agent_leasing/twilio_handler.py` | System messages read `language_code` via `_get_language_code()`; pins transcription to English |
| `src/agent_leasing/agent/resident_one_agent/VOICE_RESPONDER.md` | Voice responder prompt — default English, switch on explicit request |
| `src/agent_leasing/agent/tools/confirm_language_change.py` | `set_conversation_language` tool — sets `SessionScope.language_code` |
| `src/agent_leasing/agent/resident_one_agent/realtime.py` | Registers `set_conversation_language` tool for the responder |
| `src/agent_leasing/agent/resident_one_agent/INSTRUCTIONS.md` | Thinker prompt — follows `context.language_code` on all channels |
| `src/agent_leasing/server.py` | Non-voice endpoint — writes returned `language_code` back to context for persistence |
| `src/agent_leasing/models/context.py` | `SessionScope` with `language_code` field |
| `src/agent_leasing/util/language_utils.py` | `localize_guardrail_response()` for guardrail localization |
| `src/agent_leasing/util/realtime_util.py` | Per-message language classification for data curation |
