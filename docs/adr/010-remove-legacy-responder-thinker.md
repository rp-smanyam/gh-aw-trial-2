# 10. Remove Legacy Responder/Thinker Architecture

## Status

Accepted (2026-02-06)

## Context

The legacy Responder/Thinker architecture (ADR 005) used a nimble responder agent that delegated to multiple specialized "thinker" agents (facilities, packages, community, guest parking, policy & ledger, etc.). This was complex to maintain: each thinker had its own prompts, tools, and tests, and keeping them synchronized with the unified text agent was error-prone.

ADR 008 introduced a simpler unified voice architecture where the realtime responder uses the same `ResidentAgent` (used for text channels) as its single thinker tool. This kept all business logic in one place.

## Decision

Remove the legacy Responder/Thinker implementation entirely:

- Delete `resident_responder` agent folder
- Delete individual `thinkers` folders (facilities, packages, community, etc.)
- Remove legacy product names (`renter_ai_resident_voice`, `renter_ai_resident_chat`, etc.)
- Keep only the unified `resident_one_agent` with `ResidentAgent` and `ResidentRealtimeResponderAgent`

## Consequences

- **Simplified codebase**: One agent implementation for all channels instead of parallel implementations
- **Easier maintenance**: Changes to business logic only need to be made in one place
- **Reduced test surface**: Fewer agent/thinker combinations to test
- **Breaking change**: Legacy product names are no longer supported; clients must use `resident_one_*` products
