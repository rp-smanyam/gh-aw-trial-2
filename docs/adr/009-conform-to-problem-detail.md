# 9. Conform to RFC 9457 Problem Details

## Status

Accepted (2026-01-09)

## Context

Errors should be consistently formatted. Errors are not consistently formatted and unprocessable content is absorbed rather than rejected.

## Decision

Conform to [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html). Validate on persona.

## Consequences

Clients can expect consistent format for errors. 