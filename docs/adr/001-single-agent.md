# 1. Use Single Agent Architecture

## Status

Accepted (2025-07-04)

## Context

Applicant AI was starting to build Prospect Voice in Agents SDK. This repository, which was based on an Agents SDK POC, was created on June 16, 2025. It allowed single and multiple agent implementations. Developers needed guidance on how many agents should be used.

## Decision

Use a single agent until the prompt gets unwieldy or the number of tools grows beyond 15. This was done with the understanding that we may need to move to a multi-agent implementation at some point.

## Consequences

Better latency but a larger prompt.