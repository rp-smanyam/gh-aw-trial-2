# 8. Unified Voice Architecture

## Status

Accepted (2025-12-20)

## Context

Simplify the application. Responder/Thinker, with separate agent/thinker tools, is too complex for our use case; maintaining separate agents and prompts for both voice and text is fraught.

## Decision

Put a realtime responder in front of the already "unified" resident agent and eventually decommission the Responder/Thinker with multiple thinkers. The solution is **responder/thinkers** → **responder/resident thinker** for voice. Text channels use **resident thinker** standalone and do not require a realtime responder agent.

## Consequences

Business logic for all channels will live in one location, greatly simplifying the application.