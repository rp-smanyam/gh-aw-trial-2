# 7. Use Single Agent for Text Channels

## Status

Accepted (2025-10-30)

## Context

Responder/thinker is too slow for chat and SMS. Even with minimal models and prompts, responder/thinker is too slow with Agents SDK with the Responses API. Also, OpenAI suggests against it for non-realtime.

## Decision

Use a single agent for chat, SMS and email.

## Consequences

We will have to manage prompts in two places: voice and text.