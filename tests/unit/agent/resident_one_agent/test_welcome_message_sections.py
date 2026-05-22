"""Tests for the welcome_message_sections setting.

Encodes the expected behavior after KNCK-39188:
- Welcome is minimal by default (greeting line + closing question only)
- Services line renders only when "services" is in welcome_message_sections
- Insight news renders only when "insights" is in welcome_message_sections
- Prefetch is skipped when "insights" is not in welcome_message_sections
- Settings `insight_news_channels` and `insight_news_items` are deleted
"""

from unittest.mock import AsyncMock, patch

import pytest

from agent_leasing.agent.resident_one_agent.agent_helper import (
    prefetch_property_overview_and_insights,
)

from .conftest import render_template

TEXT_CHANNELS = ["CHAT", "SMS", "EMAIL"]
ALL_CHANNELS = TEXT_CHANNELS + ["VOICE"]


# ── Template rendering: minimal welcome (default) ────────────────────


class TestMinimalWelcomeDefault:
    """When welcome_message_sections is empty, welcome has no services and no insights."""

    @pytest.mark.parametrize("channel", TEXT_CHANNELS)
    def test_instructions_template_no_services_when_sections_empty(
        self, instructions_template, mock_context, mock_settings, channel
    ):
        mock_settings.welcome_message_sections = []
        rendered = render_template(
            instructions_template,
            channel,
            mock_context,
            mock_settings,
            available_services=["billing", "maintenance", "community events"],
        )
        assert "**Services:**" not in rendered
        assert "I can help with billing, maintenance, community events" not in rendered

    @pytest.mark.parametrize("channel", TEXT_CHANNELS)
    def test_instructions_template_no_insights_when_sections_empty(
        self, instructions_template, mock_context, mock_settings, channel
    ):
        mock_settings.welcome_message_sections = []
        rendered = render_template(
            instructions_template,
            channel,
            mock_context,
            mock_settings,
        )
        assert "Insight news" not in rendered
        # The standalone section header renders as "\n# INSIGHT NEWS\n" when active;
        # the prose reference in verification rules is wrapped in backticks so won't match.
        assert "\n# INSIGHT NEWS\n" not in rendered

    @pytest.mark.parametrize("channel", TEXT_CHANNELS)
    def test_instructions_template_still_has_closing_question_when_sections_empty(
        self, instructions_template, mock_context, mock_settings, channel
    ):
        mock_settings.welcome_message_sections = []
        rendered = render_template(
            instructions_template,
            channel,
            mock_context,
            mock_settings,
        )
        assert "MANDATORY closing question" in rendered

    def test_voice_responder_template_no_services_when_sections_empty(
        self, voice_responder_template, mock_context, mock_settings
    ):
        mock_settings.welcome_message_sections = []
        rendered = render_template(
            voice_responder_template,
            "VOICE",
            mock_context,
            mock_settings,
            available_services=["billing", "maintenance", "community events"],
        )
        assert "**Services:**" not in rendered
        assert "I can help with billing, maintenance, community events" not in rendered

    def test_voice_responder_template_no_insights_when_sections_empty(
        self, voice_responder_template, mock_context, mock_settings
    ):
        mock_settings.welcome_message_sections = []
        rendered = render_template(
            voice_responder_template,
            "VOICE",
            mock_context,
            mock_settings,
        )
        assert "Insight news" not in rendered
        # The standalone section header renders as "\n# INSIGHT NEWS\n" when active;
        # the prose reference in verification rules is wrapped in backticks so won't match.
        assert "\n# INSIGHT NEWS\n" not in rendered

    def test_voice_responder_template_still_has_closing_question_when_sections_empty(
        self, voice_responder_template, mock_context, mock_settings
    ):
        mock_settings.welcome_message_sections = []
        rendered = render_template(
            voice_responder_template,
            "VOICE",
            mock_context,
            mock_settings,
        )
        # The closing-question rule survives in the Welcome Workflow even when
        # all optional sections are disabled.
        assert "Closing question" in rendered
        assert "How can I assist you today?" in rendered


# ── Template rendering: verbose welcome (services enabled) ───────────


class TestVerboseWelcomeServicesOnly:
    """When welcome_message_sections contains 'services', services line renders."""

    @pytest.mark.parametrize("channel", TEXT_CHANNELS)
    def test_instructions_renders_services_line(self, instructions_template, mock_context, mock_settings, channel):
        mock_settings.welcome_message_sections = ["services"]
        rendered = render_template(
            instructions_template,
            channel,
            mock_context,
            mock_settings,
            available_services=["billing", "maintenance", "community events"],
        )
        assert "I can help with billing, maintenance, community events" in rendered

    @pytest.mark.parametrize("channel", TEXT_CHANNELS)
    def test_instructions_no_insights_when_only_services(
        self, instructions_template, mock_context, mock_settings, channel
    ):
        mock_settings.welcome_message_sections = ["services"]
        rendered = render_template(
            instructions_template,
            channel,
            mock_context,
            mock_settings,
        )
        assert "Insight news" not in rendered

    def test_voice_responder_renders_services_line(self, voice_responder_template, mock_context, mock_settings):
        mock_settings.welcome_message_sections = ["services"]
        rendered = render_template(
            voice_responder_template,
            "VOICE",
            mock_context,
            mock_settings,
            available_services=["billing", "maintenance", "community events"],
        )
        assert "I can help with billing, maintenance, community events" in rendered

    def test_voice_responder_no_insights_when_only_services(
        self, voice_responder_template, mock_context, mock_settings
    ):
        mock_settings.welcome_message_sections = ["services"]
        rendered = render_template(
            voice_responder_template,
            "VOICE",
            mock_context,
            mock_settings,
        )
        assert "Insight news" not in rendered


# ── Template rendering: verbose welcome (insights enabled) ───────────


class TestVerboseWelcomeInsightsOnly:
    """When welcome_message_sections contains 'insights', insight news block renders."""

    @pytest.mark.parametrize("channel", TEXT_CHANNELS)
    def test_instructions_renders_insights_block(self, instructions_template, mock_context, mock_settings, channel):
        mock_settings.welcome_message_sections = ["insights"]
        rendered = render_template(
            instructions_template,
            channel,
            mock_context,
            mock_settings,
        )
        assert "Insight news" in rendered

    @pytest.mark.parametrize("channel", TEXT_CHANNELS)
    def test_instructions_no_services_when_only_insights(
        self, instructions_template, mock_context, mock_settings, channel
    ):
        mock_settings.welcome_message_sections = ["insights"]
        rendered = render_template(
            instructions_template,
            channel,
            mock_context,
            mock_settings,
            available_services=["billing", "maintenance", "community events"],
        )
        assert "I can help with billing, maintenance, community events" not in rendered

    def test_voice_responder_renders_insights_block(self, voice_responder_template, mock_context, mock_settings):
        mock_settings.welcome_message_sections = ["insights"]
        rendered = render_template(
            voice_responder_template,
            "VOICE",
            mock_context,
            mock_settings,
        )
        assert "Insight news" in rendered

    def test_voice_responder_no_services_when_only_insights(
        self, voice_responder_template, mock_context, mock_settings
    ):
        mock_settings.welcome_message_sections = ["insights"]
        rendered = render_template(
            voice_responder_template,
            "VOICE",
            mock_context,
            mock_settings,
            available_services=["billing", "maintenance", "community events"],
        )
        assert "I can help with billing, maintenance, community events" not in rendered


# ── Template rendering: both sections enabled ────────────────────────


class TestVerboseWelcomeAllSections:
    """When both 'services' and 'insights' are in the list, both render."""

    @pytest.mark.parametrize("channel", TEXT_CHANNELS)
    def test_instructions_renders_both(self, instructions_template, mock_context, mock_settings, channel):
        mock_settings.welcome_message_sections = ["services", "insights"]
        rendered = render_template(
            instructions_template,
            channel,
            mock_context,
            mock_settings,
            available_services=["billing", "maintenance", "community events"],
        )
        assert "I can help with billing, maintenance, community events" in rendered
        assert "Insight news" in rendered

    def test_voice_responder_renders_both(self, voice_responder_template, mock_context, mock_settings):
        mock_settings.welcome_message_sections = ["services", "insights"]
        rendered = render_template(
            voice_responder_template,
            "VOICE",
            mock_context,
            mock_settings,
            available_services=["billing", "maintenance", "community events"],
        )
        assert "I can help with billing, maintenance, community events" in rendered
        assert "Insight news" in rendered


# ── Custom greeting interaction ──────────────────────────────────────


class TestCustomGreetingWithMinimalWelcome:
    """Custom greeting continues to work when welcome is minimal."""

    def test_custom_greeting_still_renders_with_empty_sections(
        self, instructions_template, mock_context, mock_settings
    ):
        mock_settings.welcome_message_sections = []
        rendered = render_template(
            instructions_template,
            "CHAT",
            mock_context,
            mock_settings,
            custom_greeting="Welcome to Oakwood!",
        )
        assert "Welcome to Oakwood!" in rendered

    def test_custom_greeting_in_voice_with_empty_sections(self, voice_responder_template, mock_context, mock_settings):
        mock_settings.welcome_message_sections = []
        rendered = render_template(
            voice_responder_template,
            "VOICE",
            mock_context,
            mock_settings,
            custom_greeting="Welcome to Oakwood!",
        )
        assert "Welcome to Oakwood!" in rendered


# ── Prefetch gating ──────────────────────────────────────────────────
# mock_mcp_servers and prefetch_mock_context fixtures live in conftest.py


class TestPrefetchGating:
    """Prefetch skipped when insights not enabled, runs when enabled."""

    @pytest.mark.asyncio
    @patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
    @patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool")
    @patch("agent_leasing.agent.resident_one_agent.agent_helper.prefetch_active_service_requests")
    async def test_prefetch_skipped_when_insights_not_in_sections(
        self,
        mock_facilities_thinker,
        mock_call_and_save_tool,
        mock_settings,
        mock_mcp_servers,
        prefetch_mock_context,
    ):
        """When 'insights' not in welcome_message_sections, no prefetch calls."""
        mock_settings.welcome_message_sections = []
        mock_settings.facilities_thinker_api_enabled = True
        mock_settings.sr_prefetch_via_mcp = False
        mock_call_and_save_tool.return_value = AsyncMock()

        await prefetch_property_overview_and_insights(
            mock_mcp_servers,
            prefetch_mock_context,
        )

        assert mock_call_and_save_tool.call_count == 0
        assert mock_facilities_thinker.call_count == 0

    @pytest.mark.asyncio
    @patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
    @patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool")
    async def test_prefetch_skipped_when_only_services_in_sections(
        self,
        mock_call_and_save_tool,
        mock_settings,
        mock_mcp_servers,
        prefetch_mock_context,
    ):
        """Services alone does not trigger prefetch — only 'insights' does."""
        mock_settings.welcome_message_sections = ["services"]
        mock_settings.facilities_thinker_api_enabled = True
        mock_settings.sr_prefetch_via_mcp = False
        mock_call_and_save_tool.return_value = AsyncMock()

        await prefetch_property_overview_and_insights(
            mock_mcp_servers,
            prefetch_mock_context,
        )

        assert mock_call_and_save_tool.call_count == 0

    @pytest.mark.asyncio
    @patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
    @patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool")
    async def test_prefetch_runs_when_insights_enabled(
        self,
        mock_call_and_save_tool,
        mock_settings,
        mock_mcp_servers,
        prefetch_mock_context,
    ):
        """When 'insights' is in welcome_message_sections, prefetch runs for all three insight types."""
        mock_settings.welcome_message_sections = ["insights"]
        mock_settings.facilities_thinker_api_enabled = False  # force MCP path for SRs
        mock_call_and_save_tool.return_value = AsyncMock()

        await prefetch_property_overview_and_insights(
            mock_mcp_servers,
            prefetch_mock_context,
        )

        # SR + packages + events = 3 calls
        assert mock_call_and_save_tool.call_count == 3

    @pytest.mark.asyncio
    @patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
    @patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool")
    async def test_prefetch_skipped_when_openai_history_exists(
        self,
        mock_call_and_save_tool,
        mock_settings,
        mock_mcp_servers,
        prefetch_mock_context,
    ):
        """Even with insights enabled, prefetch is skipped on subsequent turns."""
        mock_settings.welcome_message_sections = ["insights"]
        mock_settings.facilities_thinker_api_enabled = False
        mock_call_and_save_tool.return_value = AsyncMock()
        prefetch_mock_context.has_openai_server_history = True

        await prefetch_property_overview_and_insights(
            mock_mcp_servers,
            prefetch_mock_context,
        )

        assert mock_call_and_save_tool.call_count == 0


# ── Setting removal ──────────────────────────────────────────────────


class TestDeletedSettings:
    """insight_news_channels and insight_news_items are deleted from the Settings class."""

    def test_insight_news_channels_removed(self):
        from agent_leasing.settings import Config

        assert "insight_news_channels" not in Config.model_fields

    def test_insight_news_items_removed(self):
        from agent_leasing.settings import Config

        assert "insight_news_items" not in Config.model_fields

    def test_welcome_message_sections_exists_and_defaults_empty(self):
        from agent_leasing.settings import Config

        assert "welcome_message_sections" in Config.model_fields
        assert Config.model_fields["welcome_message_sections"].default == []
