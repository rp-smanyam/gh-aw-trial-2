"""Shared HTTP retry helpers for emergency_service_transfer tools.

Extracted from emergency_service_transfer_basic and emergency_service_transfer_advanced,
which had byte-for-byte duplicate copies with two silent divergences:
  * error logging — basic used an f-string, advanced used structured kwargs
  * empty body handling — advanced returned success on empty body, basic raised
The advanced (more defensive) variant is preserved here.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
import structlog

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30

_TRANSIENT_ERRORS = (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError, ConnectionResetError)
_MAX_RETRIES = 1
_RETRY_BACKOFF_SECONDS = 1.0


async def _make_api_call(url: str, payload: dict, headers: dict, api_name: str, method: str = "GET") -> dict[str, Any]:
    """Make an API call with retry on transient connection errors.

    Retries up to _MAX_RETRIES times with linear backoff on ClientConnectorError,
    ServerDisconnectedError, and ConnectionResetError — the errors seen when
    internalapi.realpage.com:443 intermittently resets TCP connections.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 2):  # 1-indexed, total = _MAX_RETRIES + 1 attempts
        try:
            return await _make_api_call_once(url, payload, headers, api_name, method)
        except _TRANSIENT_ERRORS as exc:
            last_exc = exc
            logger.warning(
                f"{api_name} API call failed with transient error (attempt {attempt}/{_MAX_RETRIES + 1})",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            if attempt <= _MAX_RETRIES:
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)
    raise last_exc  # type: ignore[misc]


async def _make_api_call_once(
    url: str, payload: dict, headers: dict, api_name: str, method: str = "GET"
) -> dict[str, Any]:
    """Single-attempt API call with consistent error handling."""
    timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method=method, url=url, json=payload, headers=headers) as response:
            body_text = await response.text()

            if response.status >= 400:
                logger.error(
                    f"{api_name} API call failed",
                    status=response.status,
                    body=body_text,
                )
                raise RuntimeError(f"{api_name} API returned status {response.status}")

            # Handle empty response body (Content-Length: 0)
            if not body_text or body_text.strip() == "":
                logger.info(f"{api_name} API returned empty body, treating as success")
                return {"success": True, "status": 200}

            try:
                parsed = await response.json(content_type=None)
            except aiohttp.ContentTypeError:
                logger.warning(f"{api_name} API response not JSON, attempting manual parse")
                try:
                    parsed = json.loads(body_text)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"{api_name} API returned a non-JSON response") from exc

            return parsed
