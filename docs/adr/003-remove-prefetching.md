# 3. Remove Prefetching

## Status

Accepted (2025-07-28)

## Context

We want property tools cached. The application depended on pre-fetching tools and broke if the tools weren't pre-fetched.

## Decision

Eliminate pre-fetching. If the cache needs to be warmed there is a new endpoint for that.

## Consequences

The application has been simplified and is less brittle. External services can warm the cache if required.