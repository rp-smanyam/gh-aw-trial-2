"""Tests for the set_conversation_language tool."""

from types import SimpleNamespace

import pytest

from agent_leasing.agent.tools.confirm_language_change import apply_language_code


def _make_context(initial_language: str = "en"):
    """Build a minimal context with language_code."""
    return SimpleNamespace(language_code=initial_language)


class TestSetLanguage:
    @pytest.mark.asyncio
    async def test_set_english(self):
        ctx = _make_context()
        result = await apply_language_code(ctx, "en")
        assert result == "ok"
        assert ctx.language_code == "en"

    @pytest.mark.asyncio
    async def test_set_non_english(self):
        ctx = _make_context()
        result = await apply_language_code(ctx, "es")
        assert result == "ok"
        assert ctx.language_code == "es"

    @pytest.mark.asyncio
    async def test_normalizes_input(self):
        ctx = _make_context()
        result = await apply_language_code(ctx, "  FR  ")
        assert result == "ok"
        assert ctx.language_code == "fr"

    @pytest.mark.asyncio
    async def test_switch_language(self):
        ctx = _make_context("es")
        result = await apply_language_code(ctx, "en")
        assert result == "ok"
        assert ctx.language_code == "en"


class TestToolRegistration:
    def test_exported_from_tools_package(self):
        from agent_leasing.agent.tools import set_conversation_language

        assert set_conversation_language is not None

    def test_tool_has_correct_name(self):
        from agent_leasing.agent.tools.confirm_language_change import set_conversation_language

        assert set_conversation_language.name == "set_conversation_language"
