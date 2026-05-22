"""Tests for transfer-to-staff Redis helpers."""

from datetime import datetime, timezone

import pytest

from agent_leasing.agent.tools.transfer_to_staff.handoff import (
    get_handoff_data,
    get_handoff_key,
    is_handoff_active,
    maybe_get_handoff_key,
)
from agent_leasing.util.memory import put, setup_cache

setup_cache()


@pytest.mark.asyncio
async def test_get_handoff_data_returns_none_for_missing_inputs():
    assert await get_handoff_data("", "prop", "res") is None
    assert await get_handoff_data("resident_sms", "", "res") is None
    assert await get_handoff_data("resident_sms", "prop", None, None) is None


@pytest.mark.asyncio
async def test_get_handoff_data_reads_existing_key():
    key = get_handoff_key("resident_sms", "property", "resident")
    payload = {"transferred": True, "handoff_time": datetime.now(timezone.utc).isoformat()}
    await put(key, payload, expire="3d")

    assert await get_handoff_data("resident_sms", "property", "resident") == payload


def test_handoff_keys_are_namespaced_by_id_source():
    assert get_handoff_key("resident_sms", "property", "123", None) != get_handoff_key(
        "resident_sms",
        "property",
        None,
        "123",
    )


@pytest.mark.asyncio
async def test_is_handoff_active_returns_false_without_data():
    assert not await is_handoff_active("resident_sms", "missing", "missing")


@pytest.mark.asyncio
async def test_is_handoff_active_respects_transferred_flag():
    product = "resident_sms"
    property_id = "test_property"
    resident_id = "test_resident"
    key = get_handoff_key(product, property_id, resident_id)

    await put(
        key,
        {"transferred": True, "handoff_time": datetime.now(timezone.utc).isoformat()},
        expire="3d",
    )

    assert await is_handoff_active(product, property_id, resident_id) is True

    await put(
        key,
        {"transferred": False, "handoff_time": datetime.now(timezone.utc).isoformat()},
        expire="3d",
    )

    assert await is_handoff_active(product, property_id, resident_id) is False


@pytest.mark.asyncio
async def test_handoff_roundtrip_uses_ab_resident_id_when_knock_resident_id_missing():
    """KNCK-39301: Missing Knock IDs should fall back to ab_resident_id.

    AIRR-sourced requests can have knock_resident_id=None, so both write and
    read paths need a stable resident-scoped fallback key.
    """
    product = "resident_one_sms"
    property_id = "2028667"
    knock_resident_id = None
    ab_resident_id = "21133143"

    key = get_handoff_key(product, property_id, knock_resident_id, ab_resident_id)
    payload = {"transferred": True, "handoff_time": "2026-03-30T00:00:00+00:00"}
    await put(key, payload, expire="3d")

    assert await get_handoff_data(product, property_id, knock_resident_id, ab_resident_id) == payload
    assert await is_handoff_active(product, property_id, knock_resident_id, ab_resident_id) is True


@pytest.mark.asyncio
async def test_missing_knock_resident_id_handoff_does_not_leak_across_ab_resident_ids():
    product = "resident_one_sms"
    property_id = "2028667"
    knock_resident_id = None

    await put(
        get_handoff_key(product, property_id, knock_resident_id, "21133143"),
        {"transferred": True, "handoff_time": "2026-03-30T00:00:00+00:00"},
        expire="3d",
    )

    assert await is_handoff_active(product, property_id, knock_resident_id, "21133143") is True
    assert await is_handoff_active(product, property_id, knock_resident_id, "99999999") is False


@pytest.mark.asyncio
async def test_handoff_reads_legacy_key_during_rollout():
    product = "resident_one_sms"
    property_id = "2028667"
    knock_resident_id = None
    ab_resident_id = "21133143"
    payload = {"transferred": True, "handoff_time": "2026-03-30T00:00:00+00:00"}

    await put(
        maybe_get_handoff_key(
            product,
            property_id,
            knock_resident_id,
            ab_resident_id,
            legacy=True,
        ),
        payload,
        expire="3d",
    )

    assert await get_handoff_data(product, property_id, knock_resident_id, ab_resident_id) == payload
