"""Force agents SDK trace-span sanitization on regional tracing endpoints.

The SDK's ``BackendSpanExporter`` only runs its trace-span sanitizer when the
configured trace endpoint exactly matches ``https://api.openai.com/v1/traces/ingest``.
We send traces to the US regional endpoint (``us.api.openai.com``) for data
residency, so the gate fails, sanitization is skipped, and ``usage.requests``
reaches the ingest API — which rejects it with ``400 unknown_parameter``.

Forcing the gate to ``True`` lets the SDK's existing sanitizer run, which moves
``usage.requests`` into ``usage.details``.

TEMPORARY: remove when the SDK's endpoint check supports regional URLs.
Tracking: KNCK-39718.
"""

from agents.tracing.processors import BackendSpanExporter


def apply_sanitizer_patch() -> None:
    """Force SDK trace-span sanitization on for regional tracing endpoints."""
    BackendSpanExporter._should_sanitize_for_openai_tracing_api = lambda self: True
