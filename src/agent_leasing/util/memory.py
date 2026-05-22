"""
Asynchronous memory.

Uses cashews async cache: https://github.com/Krukov/cashews/tree/master
"""

from typing import Any

import structlog
from agents import TResponseInputItem
from cashews import cache

from agent_leasing.api.model import AskRequest
from agent_leasing.models.context import SessionScope
from agent_leasing.settings import settings

logger = structlog.getLogger()

_cache_initialized = False


def setup_cache() -> None:
    """
    Initialize the cache backend.

    Should be called once during application startup.
    Idempotent - safe to call multiple times.
    """
    global _cache_initialized

    if _cache_initialized:
        logger.debug("Cache already initialized, skipping")
        return

    if settings.redis_enabled and settings.redis_host and settings.redis_port:
        cache.setup(f"redis://{settings.redis_host}:{settings.redis_port}", retry_on_timeout=True)
        logger.info(f"Cache initialized with Redis: {settings.redis_host}:{settings.redis_port}")
    else:
        cache.setup("mem://")
        logger.info("Cache initialized with in-memory backend")

    _cache_initialized = True


# via url
# cache.setup("redis://0.0.0.0/?db=1&socket_connect_timeout=0.5&suppress=0&secret=my_secret&enable=1")
# or via kwargs
# cache.setup("redis://0.0.0.0/", db=1, wait_for_connection_timeout=0.5, suppress=False, secret=b"my_key", enable=True)


async def get(key: str, default=None):
    """Get from memory."""
    value = await cache.get(key, default=default)
    return value


async def put(key: str, value: Any, expire: str = "10m"):
    """Put into memory."""
    await cache.set(key, value, expire=expire)


async def get_input_items(key: str, preferred_max_history: int = 20) -> list[TResponseInputItem]:
    """
    Get list of input items from memory.

    If a tool call is present that is not in a pair it will be removed, because it would confuse the model.

    Args:
        key: Key to get from memory.
        preferred_max_history: Preferred maximum number of input items to return.
    Returns:
        list[dict]: List of input items.
    """
    input_items = await get(key, default=[])
    logger.debug(f"Memory get: {key}: {input_items}")

    new_input_items = input_items[-preferred_max_history:]

    # If we find an orphan of a tool call pair, remove it
    # Build a set of call_ids that appear more than once (i.e., paired)
    call_id_counts = {}
    for item in new_input_items:
        cid = item.get("call_id")
        if cid:
            call_id_counts[cid] = call_id_counts.get(cid, 0) + 1
    paired_call_ids = {cid for cid, count in call_id_counts.items() if count > 1}

    # Keep items that are not tool calls, or tool calls that are paired
    return [item for item in new_input_items if not item.get("call_id") or item["call_id"] in paired_call_ids]


async def put_input_items(key: str, value: list[TResponseInputItem]):
    """Put list of input items into memory."""
    logger.debug(f"Memory put: {key}: {value}")
    await put(key, value)


def context_cache_key(req: AskRequest) -> str:
    """Return the Redis key for this request's SessionScope cache.

    Channel is prefixed so SMS and EMAIL don't share an entry when upstream
    sends the same person-level `chat_session_id` for both.
    """
    return f"{req.conversation_type.value}:{req.chat_session_id}"


async def get_context(key: str) -> SessionScope | None:
    """Get context from memory."""
    data = await get(key + "_context")
    if data is None:
        return None
    return SessionScope.from_cache(data)


async def put_context(key: str, context: SessionScope, expire: str = "10m"):
    """Put context into memory."""
    await put(key + "_context", context.to_cache(), expire=expire)
