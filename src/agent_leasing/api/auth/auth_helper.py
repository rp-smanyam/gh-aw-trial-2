import asyncio
import time
from typing import Dict, Tuple

import httpx
import structlog

from agent_leasing.settings import settings

_http_client = httpx.AsyncClient(
    transport=httpx.AsyncHTTPTransport(retries=3),
    timeout=2.0,
)


logger = structlog.getLogger()

# Auth token cache
_auth_cache: Dict[str, Tuple[str, float]] = {}  # {auth_server: (token, expiry_time)}
TOKEN_EXPIRY_BUFFER = 300  # 5 minutes buffer before token expires

REQUIRED_KEYS = ["client_id", "client_secret", "token_endpoint"]
AUTH_SETTINGS = {
    "facilities": {
        "auth_enabled": settings.facilities_mcp_auth_enabled,
        "token_endpoint": settings.facilities_mcp_auth_token_endpoint,
        "client_id": settings.facilities_mcp_auth_client_id,
        "client_secret": settings.facilities_mcp_auth_client_secret,
        "scope": settings.facilities_mcp_auth_scopes,
    },
    "knock_mcp": {
        "auth_enabled": settings.knock_mcp_auth_enabled,
        "token_endpoint": settings.knock_mcp_auth_token_endpoint,
        "client_id": settings.knock_mcp_auth_client_id,
        "client_secret": settings.knock_mcp_auth_client_secret,
        "scope": settings.knock_mcp_auth_scopes,
    },
    "loft_mcp": {
        "auth_enabled": settings.loft_mcp_auth_enabled,
        "token_endpoint": settings.loft_mcp_auth_token_endpoint,
        "client_id": settings.loft_mcp_auth_client_id,
        "client_secret": settings.loft_mcp_auth_client_secret,
        "scope": settings.loft_mcp_auth_scopes,
    },
    "one_site_mcp": {
        "auth_enabled": settings.onesite_mcp_auth_enabled,
        "token_endpoint": settings.onesite_mcp_auth_token_endpoint,
        "client_id": settings.onesite_mcp_auth_client_id,
        "client_secret": settings.onesite_mcp_auth_client_secret,
        "scope": settings.onesite_mcp_auth_scopes,
    },
    "ldp": {
        "auth_enabled": settings.ldp_auth_enabled,
        "token_endpoint": settings.ldp_login_token_endpoint,
        "client_id": settings.ldp_login_client_id,
        "client_secret": settings.ldp_login_client_secret,
        "scope": "",
    },
    "books": {
        "auth_enabled": settings.books_auth_enabled,
        "token_endpoint": settings.books_auth_endpoint,
        "client_id": settings.books_auth_client_id,
        "client_secret": settings.books_auth_client_secret,
        "scope": settings.books_auth_scopes,
    },
}


async def _post_token_request(auth_settings: dict, auth_server: str) -> dict:
    """Post to the token endpoint, retrying once on transient failures.

    The httpx transport already retries TCP connect errors (retries=3).
    This covers higher-level failures like timeouts and HTTP errors.
    """
    for attempt in range(2):
        try:
            data = {
                "grant_type": "client_credentials",
                "client_id": auth_settings["client_id"],
                "client_secret": auth_settings["client_secret"],
                "scope": auth_settings["scope"],
            }
            result = await _http_client.post(auth_settings["token_endpoint"], data=data)
            response_data = result.json()
            if not response_data.get("access_token"):
                raise ValueError(
                    f"No access token in response; "
                    f"status code: {result.status_code}; "
                    f"response keys: {list(response_data.keys())}"
                )
            return response_data
        except Exception as e:
            if attempt == 0:
                logger.warning(f"Auth token fetch failed for {auth_server}, retrying: {type(e).__name__}: {e!r}")
                await asyncio.sleep(0.5)
            else:
                raise


async def get_auth_token(auth_server: str) -> str:
    """
    Connect to the auth server to get an auth token with caching.

    Caches tokens to avoid unnecessary requests. Concurrent callers for the same
    server may occasionally duplicate a fetch, but the dict assignment is atomic
    in CPython and all produced tokens are equally valid.
    """
    current_time = time.time()
    if auth_server in _auth_cache:
        token, expiry_time = _auth_cache[auth_server]
        if current_time < expiry_time:
            logger.debug("Auth token cache hit", auth_server=auth_server)
            return token

    # Need to fetch new token
    logger.debug("Fetching new auth token", auth_server=auth_server)
    fetch_start = time.monotonic()

    auth_settings = AUTH_SETTINGS.get(auth_server)
    if not auth_settings:
        raise ValueError(f"Unknown auth server: {auth_server}")

    if not auth_settings.get("auth_enabled"):
        raise ValueError(f"Auth is not enabled for {auth_server}")

    # Validate required auth settings
    missing_keys = [key for key in REQUIRED_KEYS if not auth_settings.get(key)]
    if missing_keys:
        raise ValueError(f"Missing required auth settings for {auth_server}: {', '.join(missing_keys)}")

    logger.debug(
        "Auth settings loaded",
        auth_server=auth_server,
        token_endpoint=auth_settings["token_endpoint"],
        client_id=auth_settings["client_id"],
        scope=auth_settings.get("scope", ""),
    )

    response_data = await _post_token_request(auth_settings, auth_server)
    access_token = response_data["access_token"]

    # Cache the token with expiry (default to 1 hour if not specified)
    expires_in = response_data.get("expires_in", 3600)  # Default 1 hour
    current_time = time.time()
    expiry_time = current_time + expires_in - TOKEN_EXPIRY_BUFFER
    _auth_cache[auth_server] = (access_token, expiry_time)

    if settings.startup_latency_logging_enabled:
        duration_ms = int((time.monotonic() - fetch_start) * 1000)
        logger.info(
            f"Auth token fetched for {auth_server}",
            event_type="auth_token_fetched",
            auth_server=auth_server,
            duration_ms=duration_ms,
        )
    else:
        logger.debug("Auth token cached", auth_server=auth_server)
    return access_token


async def get_facilities_mcp_auth_token(context=None) -> str:
    return await get_auth_token("facilities")


async def get_knock_mcp_auth_token(context=None) -> str:
    return await get_auth_token("knock_mcp")


async def get_loft_mcp_auth_token(context=None) -> str:
    if context and (cidp_token := getattr(context.ask_request.product_info.uc_consumer_identity_token, "id", None)):
        return cidp_token
    return await get_auth_token("loft_mcp")


async def get_onsite_mcp_auth_token(context=None) -> str:
    return await get_auth_token("one_site_mcp")


async def get_ldp_auth_token(context=None) -> str:
    return await get_auth_token("ldp")


async def get_books_auth_token(context=None) -> str:
    return await get_auth_token("books")


async def log_token() -> str | None:
    """Provided for testing purposes."""
    logger.info(await get_auth_token(""))


async def close():
    """Close the module-level HTTP client (call during shutdown)."""
    if _http_client.is_closed:
        return

    try:
        await _http_client.aclose()
    except RuntimeError as exc:
        # In some teardown paths, transports are finalized after loop shutdown.
        # Treat closed-loop cleanup errors as benign best-effort shutdown.
        if "Event loop is closed" in str(exc):
            logger.warning(
                "Ignoring auth HTTP client close after loop shutdown",
                error=str(exc),
                exc_info=True,
            )
            return
        raise


if __name__ == "__main__":
    """Provided for testing purposes."""
    import asyncio

    asyncio.run(log_token())
