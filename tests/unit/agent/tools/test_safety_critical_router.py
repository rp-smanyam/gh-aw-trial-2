"""Tests for safety_critical_router tool."""

from unittest.mock import Mock

import pytest

from agent_leasing.agent.tools.safety_critical_router.safety_critical_router import (
    _safety_critical_router_impl,
)


@pytest.mark.asyncio
async def test_safety_critical_and_maintenance():
    result = await _safety_critical_router_impl(ctx=Mock(), is_safety_critical=True, is_maintenance_related=True)
    assert result == ("This is an emergency maintenance related request.  Follow the emergency maintenance flow.")


@pytest.mark.asyncio
async def test_not_safety_critical_but_maintenance():
    result = await _safety_critical_router_impl(ctx=Mock(), is_safety_critical=False, is_maintenance_related=True)
    assert result == ("This is a maintenance related request.  Follow the maintenance Facilities Thinker flow.")


@pytest.mark.asyncio
async def test_safety_critical_but_not_maintenance():
    result = await _safety_critical_router_impl(ctx=Mock(), is_safety_critical=True, is_maintenance_related=False)
    assert result == ("This is a safety-critical request.  Follow the safety-critical Handoff flow.")


@pytest.mark.asyncio
async def test_not_safety_critical_and_not_maintenance():
    result = await _safety_critical_router_impl(ctx=Mock(), is_safety_critical=False, is_maintenance_related=False)
    assert result == (
        "This is not a safety-critical OR a maintenance-related request.  Please continue with the standard workflow"
    )
