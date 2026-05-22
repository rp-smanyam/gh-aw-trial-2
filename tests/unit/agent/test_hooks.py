"""Tests for RenterAIAgentHooks."""

from unittest.mock import Mock

import pytest

from agent_leasing.agent.hooks import RenterAIAgentHooks


@pytest.fixture
def hooks():
    return RenterAIAgentHooks()


@pytest.mark.asyncio
async def test_on_start_returns_none(hooks):
    result = await hooks.on_start(context=Mock(), agent=Mock())
    assert result is None


@pytest.mark.asyncio
async def test_on_end_returns_none(hooks):
    result = await hooks.on_end(context=Mock(), agent=Mock(), output=Mock())
    assert result is None


@pytest.mark.asyncio
async def test_on_tool_start_returns_none(hooks):
    result = await hooks.on_tool_start(context=Mock(), agent=Mock(), tool=Mock())
    assert result is None


@pytest.mark.asyncio
async def test_on_tool_end_returns_none(hooks):
    result = await hooks.on_tool_end(context=Mock(), agent=Mock(), tool=Mock(), result="some result")
    assert result is None
