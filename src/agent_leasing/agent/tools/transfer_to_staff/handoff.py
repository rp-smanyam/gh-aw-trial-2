"""Redis helpers for the transfer-to-staff flow."""

import structlog

from agent_leasing.settings import settings
from agent_leasing.util.memory import get

logger = structlog.getLogger()

_KNOCK_PREFIX = "kn"
_AB_PREFIX = "ab"


async def is_handoff_active(
    product: str,
    property_id: str,
    knock_resident_id: str | int | None,
    ab_resident_id: str | int | None = None,
) -> bool:
    """Return True when Redis indicates an active handoff."""
    handoff_data = await get_handoff_data(product, property_id, knock_resident_id, ab_resident_id)

    if not handoff_data or not handoff_data.get("transferred"):
        return False

    logger.info(
        "Handoff is active",
        handoff_key=get_handoff_key(product, property_id, knock_resident_id, ab_resident_id),
    )
    return True


async def get_handoff_data(
    product: str,
    property_id: str,
    knock_resident_id: str | int | None,
    ab_resident_id: str | int | None = None,
) -> dict | None:
    """Fetch handoff metadata from Redis."""
    handoff_key = maybe_get_handoff_key(product, property_id, knock_resident_id, ab_resident_id)
    if handoff_key is None:
        return None

    handoff_data = await get(handoff_key)
    if handoff_data is not None:
        return handoff_data

    # Rollout compat: check pre-namespacing key for moratoriums that predate the deploy
    legacy_key = maybe_get_handoff_key(
        product,
        property_id,
        knock_resident_id,
        ab_resident_id,
        legacy=True,
    )
    if legacy_key is None or legacy_key == handoff_key:
        return None

    return await get(legacy_key)


def get_handoff_key(
    product: str,
    property_id: str,
    knock_resident_id: str | int | None,
    ab_resident_id: str | int | None = None,
) -> str:
    """Return the key under which handoff state is stored."""
    handoff_key = maybe_get_handoff_key(product, property_id, knock_resident_id, ab_resident_id)
    if handoff_key is None:
        raise ValueError("knock_resident_id or ab_resident_id is required to build a handoff key")
    return handoff_key


def maybe_get_handoff_key(
    product: str,
    property_id: str | None,
    knock_resident_id: str | int | None,
    ab_resident_id: str | int | None = None,
    *,
    legacy: bool = False,
) -> str | None:
    """Return the handoff key when enough identifying data is present."""
    if not product or not property_id:
        return None

    if knock_resident_id is not None:
        subject_id = str(knock_resident_id) if legacy else f"{_KNOCK_PREFIX}:{knock_resident_id}"
    elif ab_resident_id is not None:
        subject_id = str(ab_resident_id) if legacy else f"{_AB_PREFIX}:{ab_resident_id}"
    else:
        return None

    return f"{settings.app_name}:{product}_{property_id}_{subject_id}"
