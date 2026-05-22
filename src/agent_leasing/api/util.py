from typing import Any
from urllib.parse import urljoin

import aiohttp
import structlog

from agent_leasing.api.auth.auth_helper import get_auth_token

logger = structlog.getLogger()


def build_full_url(host: str, endpoint: str, path_params: dict[str, str] | None = None) -> str:
    if path_params:
        endpoint = endpoint.format(**path_params)
    return urljoin(host, endpoint)


async def prepare_request_headers(auth_server: str, extra_headers: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
    }
    try:
        auth_token = await get_auth_token(auth_server)
        headers["Authorization"] = f"Bearer {auth_token}"
    except Exception as e:
        logger.error(f"Error obtaining auth token for server {auth_server}: {e}")
    if extra_headers:
        headers.update(extra_headers)
    return headers


def prepare_request_parameters(
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    query_params: dict[str, str] | None = None,
) -> dict[str, Any]:
    headers = headers or {}
    content_type = headers.get("Content-Type", "").lower()
    request_kwargs = {"headers": headers}
    if query_params is not None:
        request_kwargs["params"] = query_params
    if payload is not None:
        if "application/json" in content_type:
            request_kwargs["json"] = payload
        else:
            request_kwargs["data"] = payload
    return request_kwargs


async def execute_api_request(
    url: str,
    method: str,
    request_kwargs: dict[str, Any],
    timeout_seconds: int = 300,
) -> dict | None:
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:  # noqa: SIM117
        async with session.request(method, url, **request_kwargs) as response:
            logger.debug(
                "Sending %s request to %s with request_kwargs: %s",
                method,
                url,
                request_kwargs,
            )
            if not (200 <= response.status < 300):  # noqa: PLR2004
                raise aiohttp.ClientResponseError(
                    request_info=response.request_info,
                    history=response.history,
                    status=response.status,
                    message=f"{method.upper()} {url} failed with {response.status}: {response.reason}; Response Body: {await response.text()}",  # noqa: E501
                    headers=response.headers,
                )
            # For responses with no content (e.g., 204), return None
            if response.status == 204:
                return None
            logger.debug("Response from %s: %s", url, await response.text())
            return await response.json()


async def perform_api_call(
    host: str,
    endpoint: str,
    method: str,
    auth_server: str,
    payload: dict | None = None,
    path_params: dict[str, str] | None = None,
    query_params: dict[str, str] | None = None,
    timeout_seconds: int = 300,
    extra_headers: dict[str, str] | None = None,
) -> dict | None:
    url = build_full_url(host, endpoint, path_params)
    headers = await prepare_request_headers(auth_server, extra_headers=extra_headers)
    request_kwargs = prepare_request_parameters(headers, payload, query_params)
    return await execute_api_request(url, method, request_kwargs, timeout_seconds=timeout_seconds)
