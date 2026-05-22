# 5. Implement Responder/Thinker Pattern

## Status

Superseded by [ADR 008](008-unified-voice.md) (2025-12-20)

## Context

Implement the Responder/Thinker pattern following OpenAI's suggestion. It has been successful in Renter AI prospect implementation. We need a better separation of concerns between communication with users and specific domains, and we need more natural voice conversations.

## Decision

Implement the Responder/Thinker pattern for resident real-time voice to start. Eventually apply to other channels and personas.

## Consequences

The tradeoff is slightly more complexity, but there is a nice separation of concerns between communication with users and specific domains.