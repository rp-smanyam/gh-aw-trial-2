"""Shared OpenAI client setup for examples.

Reads OPENAI_BASE_URL from the environment (loaded from .env) to support
regional endpoints (e.g. us.api.openai.com).  Configures both the default
Agents SDK client and the tracing exporter.
"""

import os

from agents import set_default_openai_client
from agents.tracing import default_exporter
from openai import AsyncOpenAI, OpenAI


def setup_openai() -> None:
    """Configure the Agents SDK default client and tracing for the regional endpoint."""
    base_url = os.getenv("OPENAI_BASE_URL") or None
    client = AsyncOpenAI(base_url=base_url)
    set_default_openai_client(client)

    if base_url:
        default_exporter().endpoint = f"{base_url.rstrip('/')}/traces/ingest"


def get_sync_client() -> OpenAI:
    """Return a sync OpenAI client configured for the regional endpoint."""
    base_url = os.getenv("OPENAI_BASE_URL") or None
    return OpenAI(base_url=base_url)


def get_async_client() -> AsyncOpenAI:
    """Return an async OpenAI client configured for the regional endpoint."""
    base_url = os.getenv("OPENAI_BASE_URL") or None
    return AsyncOpenAI(base_url=base_url)
