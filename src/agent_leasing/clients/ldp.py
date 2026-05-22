import json

import aiohttp
import structlog
from cashews import cache
from langsmith import traceable

from agent_leasing.api.auth.auth_helper import get_ldp_auth_token
from agent_leasing.settings import settings

logger = structlog.getLogger()

# Module-level aiohttp session reused across LDP fetches. Property data is
# cached via @cache.early, but cache misses and background refreshes still
# pay a TCP+TLS handshake each call. Lazy-init so creation happens inside
# the running event loop, not at import time. Per-call timeout is passed
# to session.post(...) so the 20s LDP contract is preserved.
_http_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    """Return the cached module-level session, creating it on first use.

    Replaces a stale session bound to a closed event loop — happens in test
    environments that create a fresh loop per test (pytest-asyncio default).
    Production runs in one persistent loop so this branch is dead code there.
    """
    global _http_session
    if _http_session is not None:
        session_loop = getattr(_http_session, "_loop", None)
        if session_loop is None or session_loop.is_closed():
            _http_session = None
    if _http_session is None:
        _http_session = aiohttp.ClientSession()
    return _http_session


async def close() -> None:
    """Close the module-level HTTP session (call during shutdown)."""
    global _http_session
    if _http_session is None:
        return
    try:
        await _http_session.close()
    finally:
        _http_session = None


ALL_MODULES = ["MR", "PAYMENT_CENTER", "PACKAGES", "PARKING_PASS", "EVENTS"]

# Maps modules to thinker tool names (used by resident_responder agent)
MODULE_TO_THINKER_TOOL = {
    "PAYMENT_CENTER": "policy_and_ledger_thinker_tool",
    "PARKING_PASS": "guest_parking_thinker_tool",
    "PACKAGES": "packages_thinker_tool",
    "EVENTS": "community_thinker_tool",
    "MR": "facilities_thinker_tool",
}

# Maps modules to MCP tool names (used by resident_one_agent)
MODULE_TO_MCP_TOOLS = {
    "PAYMENT_CENTER": [
        "get_lease_term_information",
        "get_rent_information",
        "get_fas_account_statement",
        "get_resident_autopay_and_transactions",
        "get_property_details",
        "get_custom_reminders",
        "manage_custom_reminders",
    ],
    "PARKING_PASS": ["issue_guest_parking_pass"],
    "PACKAGES": ["get_residents_packages"],
    "EVENTS": [
        "cancel_community_event",
        "fetch_community_events",
        "fetch_user_signed_up_community_events",
        "sign_up_community_events",
    ],
    "MR": [
        "create_service_request",
        "get_active_service_requests",
    ],  # TODO: This can be removed once Facilities Thinker is fully migrated to API calls
}

# Maps modules to human-readable service names for the greeting
MODULE_TO_SERVICE_NAME = {
    "MR": "maintenance",
    "PAYMENT_CENTER": "billing",
    "PACKAGES": "packages",
    "PARKING_PASS": "guest parking passes",
    "EVENTS": "community events",
}

# Backwards compatibility alias
MODULE_TO_TOOL = MODULE_TO_THINKER_TOOL


class LDPError(Exception):
    """Raised when LDP API calls fail."""

    pass


async def call_ldp_api(url: str, data: dict) -> dict:
    """Call LDP API. Raises LDPError on any failure."""
    timeout = aiohttp.ClientTimeout(total=20)  # 20 seconds

    headers = {}
    if settings.ldp_auth_enabled:
        try:
            token = await get_ldp_auth_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        except Exception as e:
            logger.warning(f"LDP auth failed: {e}")

    try:
        session = _get_session()
        async with session.post(url, headers=headers, json=data, timeout=timeout) as response:
            if response.status not in [200, 201]:
                logger.warning(f"LDP API failed: {url}, status={response.status}")
                raise LDPError(f"HTTP {response.status}")
            try:
                return await response.json()
            except json.decoder.JSONDecodeError as e:
                logger.warning(f"LDP API JSON parsing error: {url}, status={response.status}")
                raise LDPError("JSON parsing failed") from e
    except aiohttp.ClientError as e:
        logger.warning(f"LDP connection error: {e}")
        raise LDPError("Connection failed") from e
    except TimeoutError as e:
        logger.warning(f"LDP request timed out: {e}")
        raise LDPError("Request timed out") from e


async def get_ldp_data(property_id: str) -> dict:
    """Fetch property data from LDP. Raises LDPError on failure."""
    url = settings.ldp_rp_api_url + "/renter-read"
    data = {
        "dataset_id": "lz_renter_data_hub",
        "table_name": "property_info",
        "filters": {"and": [{"field": "property_id", "operator": "=", "value": f"{property_id}"}]},
        "offset": 0,
    }

    logger.info(f"LDP POST to url: {url} with data: {data}")

    return await call_ldp_api(url, data=data)


def _parse_enabled_modules_with_pte(response: dict) -> tuple[list[str] | None, bool]:
    """Extract enabled modules and permission-to-enter flag from LDP response. Returns None, False if invalid."""
    if not response:
        return None, False
    records = response.get("records", [])
    if not records:
        return None, False
    loft_living_data = records[0].get("extras", {}).get("loftLiving", {})
    enabled = loft_living_data.get("modules")
    pte_setting = loft_living_data.get("permissionToEnter", False)
    return enabled, pte_setting


def _parse_resident_summary(response: dict) -> str | None:
    """Extract resident_summary from LDP response. Returns None if unavailable."""
    if not response:
        return None
    records = response.get("records", [])
    if not records:
        return None
    return records[0].get("resident_summary")


@cache.early(ttl=settings.ldp_cache_ttl, early_ttl=settings.ldp_cache_early_ttl, key="ldp_property_data:{property_id}")
async def fetch_ldp_property_data(property_id: str) -> dict:
    """Fetch and parse all LDP property data with background refresh.

    Returns dict with keys: enabled_modules, pte_setting, resident_summary.

    Uses @cache.early so the cache is refreshed in the background after early_ttl,
    while still serving the cached value immediately. Raises LDPError on failure.
    """
    response = await get_ldp_data(property_id)
    enabled, pte_setting = _parse_enabled_modules_with_pte(response)
    if enabled is None:
        raise LDPError("No modules in LDP response")
    resident_summary = _parse_resident_summary(response)
    return {
        "enabled_modules": enabled,
        "pte_setting": pte_setting,
        "resident_summary": resident_summary,
    }


@traceable
async def get_disabled_modules_with_pte(property_id: str) -> tuple[list[str], bool]:
    """
    Get disabled modules and permission-to-enter setting.

    Returns all modules disabled on any failure. If settings.ldp_modules_all_enabled is True, then
    all modules are enabled.
    """
    try:
        data = await fetch_ldp_property_data(property_id)
        enabled, pte_setting = data["enabled_modules"], data["pte_setting"]
        disabled = [] if settings.ldp_modules_all_enabled else [m for m in ALL_MODULES if m not in enabled]

        if settings.ldp_modules_all_enabled:
            logger.info(f"All modules enabled: {ALL_MODULES}")
        elif disabled:
            logger.info(f"Disabled modules: {disabled}")

        return disabled, pte_setting
    except LDPError as e:
        if settings.ldp_modules_all_enabled:
            # Local-dev escape hatch: when this flag is set, the operator
            # has explicitly opted into "all modules on" — don't let an
            # LDP outage flip the agent into a fully-disabled state.
            logger.warning(
                f"LDP fetch failed for property {property_id}: {e}. ldp_modules_all_enabled=True; enabling all modules."
            )
            return [], False
        logger.warning(f"LDP fetch failed for property {property_id}: {e}. Disabling all modules.")
        return ALL_MODULES, False


@traceable
def get_disabled_tools_from_disabled_modules(
    module_mapping: dict[str, list[str]], disabled_modules: list[str] | None
) -> list[str]:
    if not disabled_modules:
        return []
    return [tool for module in disabled_modules for tool in module_mapping.get(module, [])]


def get_available_services(disabled_modules: list[str]) -> list[str]:
    """Return a list of available service names based on disabled modules.

    Iterates ALL_MODULES in order, excludes disabled ones, and maps to display names.
    """
    return [MODULE_TO_SERVICE_NAME[m] for m in ALL_MODULES if m not in disabled_modules]
