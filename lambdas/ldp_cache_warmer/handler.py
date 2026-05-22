"""Lambda handlers for LDP cache warming.

Two-Lambda architecture with SQS queue:
- Producer (EventBridge): fetches property IDs from AI Config, chunks, sends to SQS
- Worker (SQS): warms LDP cache for each property, writes to Redis

Both handlers use the AWS Secrets Manager Lambda Extension to fetch secrets
from the local extension cache at localhost:2773.
"""

import asyncio
import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta

import httpx
from cashews import cache
from cashews.ttl import ttl_to_seconds

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Secret placeholders — populated by _apply_secrets() at handler start
AI_CONFIG_HOST = ""
AI_CONFIG_TOKEN = ""
LDP_RP_API_URL = ""
LDP_LOGIN_TOKEN_ENDPOINT = ""
LDP_LOGIN_CLIENT_ID = ""
LDP_LOGIN_CLIENT_SECRET = ""

# Non-secret env vars (set by Terraform, read at module level)
REDIS_HOST = os.environ.get("REDIS_HOST", "")
REDIS_PORT = os.environ.get("REDIS_PORT", "6379")

# Defaults used until _apply_secrets() runs (and for local dev without SM)
LDP_CACHE_TTL = "2h"
LDP_CACHE_EARLY_TTL = "1h30m"

# Tuning knobs — configurable via Secrets Manager without a Terraform apply
CHUNK_SIZE = 100
WORKER_CONCURRENCY = 20
LDP_WARM_MAX_RETRIES = 1
LDP_WARM_RETRY_BACKOFF_SECONDS = 1.0

LDP_REQUEST_TIMEOUT_SECONDS = 20.0
_RETRYABLE_LDP_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# cashews @cache.early prepends "early:v2:" to the user-provided key template
CACHE_KEY_PREFIX = "early:v2:ldp_property_data"

SECRET_KEYS = [
    "AI_CONFIG_HOST",
    "AI_CONFIG_TOKEN",
    "LDP_RP_API_URL",
    "LDP_LOGIN_TOKEN_ENDPOINT",
    "LDP_LOGIN_CLIENT_ID",
    "LDP_LOGIN_CLIENT_SECRET",
    "LDP_CACHE_TTL",
    "LDP_CACHE_EARLY_TTL",
    "CHUNK_SIZE",
    "WORKER_CONCURRENCY",
    "LDP_WARM_MAX_RETRIES",
    "LDP_WARM_RETRY_BACKOFF_SECONDS",
]


# =============================================================================
# Secrets
# =============================================================================


def _get_secrets() -> dict[str, str]:
    """Fetch secrets from SM Lambda Extension, or fall back to env vars for local dev."""
    session_token = os.environ.get("AWS_SESSION_TOKEN")
    if not session_token:
        return {k: os.environ.get(k, "") for k in SECRET_KEYS}

    secret_id = os.environ["SECRETS_MANAGER_SECRET_ID"]
    url = f"http://localhost:2773/secretsmanager/get?secretId={urllib.parse.quote(secret_id, safe='')}"
    req = urllib.request.Request(url, headers={"X-Aws-Parameters-Secrets-Token": session_token})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(json.loads(resp.read())["SecretString"])


def _apply_secrets(secrets: dict) -> None:
    """Set module-level globals from secrets dict."""
    global AI_CONFIG_HOST, AI_CONFIG_TOKEN, LDP_RP_API_URL  # noqa: PLW0603
    global LDP_LOGIN_TOKEN_ENDPOINT, LDP_LOGIN_CLIENT_ID, LDP_LOGIN_CLIENT_SECRET  # noqa: PLW0603
    global LDP_CACHE_TTL, LDP_CACHE_EARLY_TTL  # noqa: PLW0603
    global CHUNK_SIZE, WORKER_CONCURRENCY  # noqa: PLW0603
    global LDP_WARM_MAX_RETRIES, LDP_WARM_RETRY_BACKOFF_SECONDS  # noqa: PLW0603
    AI_CONFIG_HOST = secrets.get("AI_CONFIG_HOST", "")
    AI_CONFIG_TOKEN = secrets.get("AI_CONFIG_TOKEN", "")
    LDP_RP_API_URL = secrets.get("LDP_RP_API_URL", "")
    LDP_LOGIN_TOKEN_ENDPOINT = secrets.get("LDP_LOGIN_TOKEN_ENDPOINT", "")
    LDP_LOGIN_CLIENT_ID = secrets.get("LDP_LOGIN_CLIENT_ID", "")
    LDP_LOGIN_CLIENT_SECRET = secrets.get("LDP_LOGIN_CLIENT_SECRET", "")
    LDP_CACHE_TTL = secrets.get("LDP_CACHE_TTL", "2h")
    LDP_CACHE_EARLY_TTL = secrets.get("LDP_CACHE_EARLY_TTL", "1h30m")
    CHUNK_SIZE = int(secrets.get("CHUNK_SIZE", "100"))
    WORKER_CONCURRENCY = int(secrets.get("WORKER_CONCURRENCY", "20"))
    LDP_WARM_MAX_RETRIES = int(secrets.get("LDP_WARM_MAX_RETRIES", "1"))
    LDP_WARM_RETRY_BACKOFF_SECONDS = float(secrets.get("LDP_WARM_RETRY_BACKOFF_SECONDS", "1.0"))


# =============================================================================
# Shared helpers
# =============================================================================


_cache_initialized = False


def _setup_cache() -> None:
    """Initialize cashews Redis backend. Skips re-init on Lambda warm starts."""
    global _cache_initialized  # noqa: PLW0603
    if _cache_initialized:
        return
    if REDIS_HOST:
        cache.setup(f"redis://{REDIS_HOST}:{REDIS_PORT}", retry_on_timeout=True)
        _cache_initialized = True
    else:
        raise RuntimeError("REDIS_HOST is required")


async def fetch_property_ids(client: httpx.AsyncClient) -> list[str]:
    """Fetch renter-AI-enabled property IDs from AI Config API."""
    url = f"{AI_CONFIG_HOST}/v3/properties/renter-ai-enabled"
    resp = await client.get(url, headers={"Authorization": f"Bearer {AI_CONFIG_TOKEN}"}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return [str(pid) for pid in data.get("property_data", [])]


async def fetch_ldp_auth_token(client: httpx.AsyncClient) -> str:
    """Get LDP OAuth token via client_credentials grant."""
    resp = await client.post(
        LDP_LOGIN_TOKEN_ENDPOINT,
        data={
            "grant_type": "client_credentials",
            "client_id": LDP_LOGIN_CLIENT_ID,
            "client_secret": LDP_LOGIN_CLIENT_SECRET,
            "scope": "",
        },
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in LDP auth response (status={resp.status_code})")
    return token


def _parse_ldp_response(response: dict) -> dict:
    """Parse LDP renter-read response into the format the service expects.

    Returns dict with keys: enabled_modules, pte_setting, resident_summary.
    Same logic as ldp.py _parse_enabled_modules_with_pte + _parse_resident_summary.
    """
    records = response.get("records", [])
    if not records:
        return {"enabled_modules": None, "pte_setting": False, "resident_summary": None}

    record = records[0]
    loft_living_data = record.get("extras", {}).get("loftLiving", {})
    enabled_modules = loft_living_data.get("modules")
    pte_setting = loft_living_data.get("permissionToEnter", False)
    resident_summary = record.get("resident_summary")

    return {
        "enabled_modules": enabled_modules,
        "pte_setting": pte_setting,
        "resident_summary": resident_summary,
    }


def _is_retryable_ldp_exception(exc: Exception) -> bool:
    """Return True for transient LDP failures that are safe to retry."""
    if isinstance(exc, httpx.TimeoutException | httpx.NetworkError):
        return True

    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in _RETRYABLE_LDP_STATUS_CODES


def _ldp_retry_reason(exc: Exception) -> str:
    """Summarize the transient failure for retry logging."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


async def _fetch_ldp_property_response(
    client: httpx.AsyncClient,
    ldp_token: str,
    property_id: str,
) -> httpx.Response:
    """Fetch a single property from LDP with retry/backoff for transient failures."""
    url = f"{LDP_RP_API_URL}/renter-read"
    payload = {
        "dataset_id": "lz_renter_data_hub",
        "table_name": "property_info",
        "filters": {"and": [{"field": "property_id", "operator": "=", "value": str(property_id)}]},
        "offset": 0,
    }
    total_attempts = LDP_WARM_MAX_RETRIES + 1

    for attempt in range(1, total_attempts + 1):
        try:
            resp = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {ldp_token}", "Content-Type": "application/json"},
                timeout=LDP_REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            return resp
        except Exception as exc:
            if not _is_retryable_ldp_exception(exc) or attempt == total_attempts:
                raise

            backoff_seconds = LDP_WARM_RETRY_BACKOFF_SECONDS * attempt
            logger.warning(
                "Retrying LDP warm for property %s after %s (attempt %d/%d, backoff %.1fs)",
                property_id,
                _ldp_retry_reason(exc),
                attempt + 1,
                total_attempts,
                backoff_seconds,
            )
            await asyncio.sleep(backoff_seconds)


async def warm_property(
    client: httpx.AsyncClient,
    ldp_token: str,
    property_id: str,
    semaphore: asyncio.Semaphore,
) -> bool:
    """Fetch LDP data for a single property and write to cache. Returns True on success."""
    async with semaphore:
        try:
            resp = await _fetch_ldp_property_response(client, ldp_token, property_id)
            parsed = _parse_ldp_response(resp.json())

            if parsed["enabled_modules"] is None:
                logger.warning("No modules in LDP response for property %s", property_id)
                return False

            # cashews @cache.early stores values as [early_expire_at, result]
            # where early_expire_at is a UTC datetime. We must match this format
            # so the service can unpack the value correctly (cashews early.py:84).
            early_expire_at = datetime.now(UTC) + timedelta(seconds=int(ttl_to_seconds(LDP_CACHE_EARLY_TTL)))
            cache_key = f"{CACHE_KEY_PREFIX}:{property_id}"
            await cache.set(cache_key, [early_expire_at, parsed], expire=LDP_CACHE_TTL)
            return True

        except Exception:
            logger.exception("Failed to warm property %s", property_id)
            return False


# =============================================================================
# Producer handler (EventBridge → SQS)
# =============================================================================


async def _fetch_property_ids() -> list[str]:
    """Thin async wrapper for producer — creates client and fetches IDs."""
    async with httpx.AsyncClient() as client:
        return await fetch_property_ids(client)


def producer_handler(event: dict, context) -> dict:
    """EventBridge trigger: fetch property IDs from AI Config, chunk, send to SQS."""
    secrets = _get_secrets()
    _apply_secrets(secrets)

    queue_url = os.environ.get("SQS_QUEUE_URL")
    if not queue_url:
        raise RuntimeError("SQS_QUEUE_URL is required")

    property_ids = asyncio.run(_fetch_property_ids())
    if not property_ids:
        logger.info("No properties to warm")
        return {"statusCode": 200, "body": json.dumps({"sent": 0, "chunks": 0})}

    import boto3  # Lambda runtime provides boto3; deferred to avoid test dependency

    chunk_size = CHUNK_SIZE
    chunks = [property_ids[i : i + chunk_size] for i in range(0, len(property_ids), chunk_size)]

    sqs = boto3.client("sqs")
    entries: list[dict] = []
    batch_count = 0
    all_failed: list[str] = []
    for i, chunk in enumerate(chunks):
        entries.append(
            {
                "Id": str(i),
                "MessageBody": json.dumps({"property_ids": chunk, "chunk_index": i}),
            }
        )
        if len(entries) == 10 or i == len(chunks) - 1:
            resp = sqs.send_message_batch(QueueUrl=queue_url, Entries=entries)
            if resp.get("Failed"):
                failed_ids = [f["Id"] for f in resp["Failed"]]
                logger.error("SQS send_message_batch partial failure, chunks dropped: %s", failed_ids)
                all_failed.extend(failed_ids)
            batch_count += 1
            entries = []

    logger.info("Sent %d properties in %d chunks (%d SQS batches)", len(property_ids), len(chunks), batch_count)

    if all_failed:
        # Raise so Lambda marks invocation as failed → CloudWatch alarm + EventBridge retry.
        # All batches were attempted; only failed chunks are lost. EventBridge retry re-sends
        # everything, but workers are idempotent (cache key overwrites).
        raise RuntimeError(f"{len(all_failed)} SQS chunks failed to send: {all_failed}")

    return {"statusCode": 200, "body": json.dumps({"sent": len(property_ids), "chunks": len(chunks)})}


# =============================================================================
# Worker handler (SQS → Redis)
# =============================================================================


async def _warm_batch(property_ids: list[str]) -> dict:
    """Warm a batch of properties with bounded concurrency."""
    # Tune chunk size, concurrency, request timeout, and retry knobs together so
    # typical runtimes stay under the 120s Lambda timeout.
    semaphore = asyncio.Semaphore(WORKER_CONCURRENCY)

    async with httpx.AsyncClient() as client:
        ldp_token = await fetch_ldp_auth_token(client)
        results = await asyncio.gather(*[warm_property(client, ldp_token, pid, semaphore) for pid in property_ids])

    succeeded = sum(1 for r in results if r)
    return {"succeeded": succeeded, "failed": len(results) - succeeded}


async def _process_sqs_records(records: list[dict]) -> dict:
    """Process SQS records, returning partial batch failures for retry.

    Note: batch_size=1 in the SQS event source mapping, so this loop processes
    exactly one record per invocation. If batch_size increases, consider
    asyncio.gather for concurrent processing.
    """
    failures = []
    for record in records:
        try:
            body = json.loads(record["body"])
            property_ids = body["property_ids"]
            chunk_index = body.get("chunk_index", "?")

            result = await _warm_batch(property_ids)
            logger.info(
                "Chunk %s: %d succeeded, %d failed out of %d",
                chunk_index,
                result["succeeded"],
                result["failed"],
                len(property_ids),
            )
            # Partial property failures are logged, not retried via SQS.
            # Next 30-min cycle will re-warm them. SQS retry would re-process
            # already-cached properties in the same chunk.
        except Exception:
            logger.exception("Failed to process chunk from SQS record %s", record.get("messageId"))
            failures.append({"itemIdentifier": record["messageId"]})

    return {"batchItemFailures": failures}


def worker_handler(event: dict, context) -> dict:
    """SQS trigger: warm LDP cache for a batch of property IDs."""
    secrets = _get_secrets()
    _apply_secrets(secrets)
    _setup_cache()

    return asyncio.run(_process_sqs_records(event.get("Records", [])))
