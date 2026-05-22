import os

import structlog
from agents import set_default_openai_client
from agents.tracing import default_exporter
from langsmith.wrappers import wrap_openai
from openai import AsyncOpenAI

from agent_leasing.settings import settings
from agent_leasing.util.agents_tracing_patch import apply_sanitizer_patch

logger = structlog.getLogger(__name__)

_openai_client: AsyncOpenAI | None = None


def initialize_openai_client() -> AsyncOpenAI:
    """Initialize and return the shared OpenAI client."""
    global _openai_client
    if _openai_client is not None:
        return _openai_client

    # Remove empty OPENAI_BASE_URL from the environment so the OpenAI SDK
    # doesn't pick it up as an invalid base URL when we pass base_url=None.
    if os.environ.get("OPENAI_BASE_URL", None) == "":
        del os.environ["OPENAI_BASE_URL"]

    base_url = settings.openai_base_url or None
    if base_url:
        logger.info("Initializing OpenAI client with custom base_url", base_url=base_url)
    else:
        logger.info("Initializing OpenAI client with default base_url")

    _openai_client = AsyncOpenAI(
        api_key=settings.openai_api_key or None,
        base_url=base_url,
        timeout=settings.openai_request_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
    _openai_client = wrap_openai(_openai_client)  # Add LangSmith spans for all OpenAI API calls
    set_default_openai_client(_openai_client)

    if settings.openai_base_url:
        # Keep tracing in the same region as model calls.
        base_url_stripped = settings.openai_base_url.rstrip("/")
        tracing_endpoint = f"{base_url_stripped}/traces/ingest"
        default_exporter().endpoint = tracing_endpoint
        logger.info("Configured OpenAI tracing endpoint", endpoint=tracing_endpoint)
        apply_sanitizer_patch()

    return _openai_client


def get_openai_client() -> AsyncOpenAI:
    """Return the current OpenAI client, initializing if needed.

    Prefer this over the module-level ``openai_client`` so that test fixtures
    can swap the singleton without leaving stale references.
    """
    if _openai_client is None:
        return initialize_openai_client()
    return _openai_client


# Shared OpenAI client for the entire application.
# Configures the Agents SDK default and is importable for direct API calls
# (e.g. moderations, completions) that bypass the SDK.
openai_client = initialize_openai_client()
