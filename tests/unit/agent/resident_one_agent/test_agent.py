"""Tests for resident_one_agent agent module."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import jinja2
import pytest

from agent_leasing.agent.resident_one_agent.agent import ResidentAgent
from agent_leasing.agent.util import AgentArchitecture
from agent_leasing.clients.ldp import MODULE_TO_MCP_TOOLS

from .conftest import render_template


def _create_aenter_mocks():
    """Create common mocks for __aenter__ tests."""
    return {
        "get_disabled_modules_with_pte": patch(
            "agent_leasing.agent.resident_one_agent.agent.get_disabled_modules_with_pte",
            new_callable=AsyncMock,
            return_value=([], False),
        ),
        "get_disabled_tools": patch(
            "agent_leasing.agent.resident_one_agent.agent.get_disabled_tools_from_disabled_modules",
            return_value=[],
        ),
        "get_mcp_servers": patch(
            "agent_leasing.agent.resident_one_agent.agent.get_mcp_servers",
            return_value={},
        ),
        "custom_span": patch("agent_leasing.agent.resident_one_agent.agent.custom_span"),
        "set_span_data": patch("agent_leasing.agent.resident_one_agent.agent.set_span_data"),
        "fetch_ldp_property_data": patch(
            "agent_leasing.agent.resident_one_agent.agent.fetch_ldp_property_data",
            new_callable=AsyncMock,
            return_value={"resident_summary": "Property summary from LDP"},
        ),
        "prefetch": patch(
            "agent_leasing.agent.resident_one_agent.agent.prefetch_property_overview_and_insights",
            new_callable=AsyncMock,
            return_value=[],
        ),
        "get_channel": patch(
            "agent_leasing.agent.resident_one_agent.agent.get_channel_from_context",
            return_value="CHAT",
        ),
    }


class TestBaseResidentAgentAenter:
    """Tests for BaseResidentAgent.__aenter__ method."""

    @pytest.mark.asyncio
    async def test_aenter_fetches_disabled_modules_for_single_agent(self, resident_context_unified_chat_ll):
        """Test that __aenter__ fetches disabled modules for SINGLE_AGENT architecture."""
        context = resident_context_unified_chat_ll

        with (
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_disabled_modules_with_pte",
                new_callable=AsyncMock,
            ) as mock_get_disabled_modules,
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_disabled_tools_from_disabled_modules"
            ) as mock_get_disabled_tools,
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_mcp_servers",
                return_value={},
            ),
            patch("agent_leasing.agent.resident_one_agent.agent.custom_span") as mock_custom_span,
            patch("agent_leasing.agent.resident_one_agent.agent.set_span_data") as mock_set_span_data,
            patch(
                "agent_leasing.agent.resident_one_agent.agent.prefetch_property_overview_and_insights",
                new_callable=AsyncMock,
            ),
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_channel_from_context",
                return_value="CHAT",
            ),
            patch.object(ResidentAgent, "_create_agent", new_callable=AsyncMock) as mock_create_agent,
        ):
            # Setup mocks
            mock_get_disabled_modules.return_value = (["PARKING_PASS", "PACKAGES"], False)
            mock_get_disabled_tools.return_value = ["issue_guest_parking_pass", "get_residents_packages"]
            mock_custom_span.return_value.__enter__ = MagicMock()
            mock_custom_span.return_value.__exit__ = MagicMock()
            mock_create_agent.return_value = MagicMock()

            agent = ResidentAgent(context)

            # Verify architecture is SINGLE_AGENT
            assert agent.agent_architecture == AgentArchitecture.SINGLE_AGENT

            # Enter the async context
            async with agent:
                pass

            # Verify get_disabled_modules_with_pte was called
            mock_get_disabled_modules.assert_called_once_with(context.property_id)

            # Verify get_disabled_tools_from_disabled_modules was called
            mock_get_disabled_tools.assert_called_once_with(MODULE_TO_MCP_TOOLS, ["PARKING_PASS", "PACKAGES"])

            # Verify context was updated
            assert context.disabled_modules == ["PARKING_PASS", "PACKAGES"]
            assert context.disabled_tools == ["issue_guest_parking_pass", "get_residents_packages"]

            # Verify span data was set (called twice: once for disabled modules, once for prefetch)
            assert mock_set_span_data.call_count == 2

    @pytest.mark.asyncio
    async def test_aenter_initializes_mcp_servers_after_disabled_modules(self, resident_context_unified_chat_ll):
        """Test that MCP servers are initialized after disabled modules are set."""
        context = resident_context_unified_chat_ll

        call_order = []

        async def mock_get_disabled_modules(property_id):
            call_order.append("get_disabled_modules_with_pte")
            return ["MR"], False

        def mock_get_mcp_servers(ctx):
            call_order.append("get_mcp_servers")
            # Verify disabled_modules is already set when get_mcp_servers is called
            assert ctx.disabled_modules == ["MR"]
            return {}

        with (
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_disabled_modules_with_pte",
                side_effect=mock_get_disabled_modules,
            ),
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_mcp_servers",
                side_effect=mock_get_mcp_servers,
            ),
            patch("agent_leasing.agent.resident_one_agent.agent.custom_span") as mock_custom_span,
            patch("agent_leasing.agent.resident_one_agent.agent.set_span_data"),
            patch(
                "agent_leasing.agent.resident_one_agent.agent.prefetch_property_overview_and_insights",
                new_callable=AsyncMock,
            ),
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_channel_from_context",
                return_value="CHAT",
            ),
            patch.object(ResidentAgent, "_create_agent", new_callable=AsyncMock) as mock_create_agent,
        ):
            mock_custom_span.return_value.__enter__ = MagicMock()
            mock_custom_span.return_value.__exit__ = MagicMock()
            mock_create_agent.return_value = MagicMock()

            agent = ResidentAgent(context)

            async with agent:
                pass

            # Verify order: get_disabled_modules_with_pte before get_mcp_servers
            assert call_order == ["get_disabled_modules_with_pte", "get_mcp_servers"]

    @pytest.mark.asyncio
    async def test_aenter_skips_fetch_when_disabled_modules_already_set(self, resident_context_unified_chat_ll):
        """Test that __aenter__ skips fetching disabled modules when already set on context.

        This is important for the agent-as-a-tool pattern where ResidentAgent is used
        inside ResidentRealtimeResponderAgent - the realtime agent fetches disabled modules
        first, and the thinker should reuse them.
        """
        context = resident_context_unified_chat_ll
        # Pre-set disabled modules on context (simulating realtime agent already fetched them)
        context.disabled_modules = ["PARKING_PASS"]
        context.disabled_tools = ["issue_guest_parking_pass"]

        with (
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_disabled_modules_with_pte",
                new_callable=AsyncMock,
            ) as mock_get_disabled_modules,
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_mcp_servers",
                return_value={},
            ),
            patch("agent_leasing.agent.resident_one_agent.agent.custom_span") as mock_custom_span,
            patch("agent_leasing.agent.resident_one_agent.agent.set_span_data"),
            patch(
                "agent_leasing.agent.resident_one_agent.agent.prefetch_property_overview_and_insights",
                new_callable=AsyncMock,
            ),
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_channel_from_context",
                return_value="CHAT",
            ),
            patch.object(ResidentAgent, "_create_agent", new_callable=AsyncMock) as mock_create_agent,
        ):
            mock_custom_span.return_value.__enter__ = MagicMock()
            mock_custom_span.return_value.__exit__ = MagicMock()
            mock_create_agent.return_value = MagicMock()

            agent = ResidentAgent(context)

            async with agent:
                pass

            # Verify get_disabled_modules_with_pte was NOT called (already set)
            mock_get_disabled_modules.assert_not_called()

            # Verify context still has the pre-set values
            assert context.disabled_modules == ["PARKING_PASS"]
            assert context.disabled_tools == ["issue_guest_parking_pass"]

    @pytest.mark.asyncio
    async def test_aenter_always_runs_prefetch(self, resident_context_unified_chat_ll):
        """Test that __aenter__ always runs the prefetch during initialization."""
        context = resident_context_unified_chat_ll

        with (
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_disabled_modules_with_pte",
                new_callable=AsyncMock,
                return_value=([], False),
            ),
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_disabled_tools_from_disabled_modules",
                return_value=[],
            ),
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_mcp_servers",
                return_value={},
            ),
            patch("agent_leasing.agent.resident_one_agent.agent.custom_span") as mock_custom_span,
            patch("agent_leasing.agent.resident_one_agent.agent.set_span_data"),
            patch(
                "agent_leasing.agent.resident_one_agent.agent.prefetch_property_overview_and_insights",
                new_callable=AsyncMock,
            ) as mock_prefetch,
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_channel_from_context",
                return_value="CHAT",
            ),
            patch.object(ResidentAgent, "_create_agent", new_callable=AsyncMock) as mock_create_agent,
        ):
            mock_custom_span.return_value.__enter__ = MagicMock()
            mock_custom_span.return_value.__exit__ = MagicMock()
            mock_create_agent.return_value = MagicMock()

            agent = ResidentAgent(context)

            async with agent:
                pass

            # Verify prefetch was called (unconditionally)
            mock_prefetch.assert_called_once()
            call_args = mock_prefetch.call_args
            assert call_args[0][1] == context  # context is second positional arg

    @pytest.mark.asyncio
    async def test_aenter_prefetches_property_data(self, resident_context_unified_chat_ll):
        """Test that __aenter__ prefetches property data during initialization (flag=False path)."""
        context = resident_context_unified_chat_ll

        with (
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_disabled_modules_with_pte",
                new_callable=AsyncMock,
                return_value=([], False),
            ),
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_disabled_tools_from_disabled_modules",
                return_value=[],
            ),
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_mcp_servers",
                return_value={},
            ),
            patch(
                "agent_leasing.agent.resident_one_agent.agent.fetch_ldp_property_data",
                new_callable=AsyncMock,
                return_value={"resident_summary": "Property summary from LDP"},
            ),
            patch("agent_leasing.agent.resident_one_agent.agent.custom_span") as mock_custom_span,
            patch("agent_leasing.agent.resident_one_agent.agent.set_span_data"),
            patch(
                "agent_leasing.agent.resident_one_agent.agent.prefetch_property_overview_and_insights",
                new_callable=AsyncMock,
            ) as mock_prefetch,
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_channel_from_context",
                return_value="CHAT",
            ),
            patch("agent_leasing.agent.resident_one_agent.agent.settings") as mock_settings,
            patch.object(ResidentAgent, "_create_agent", new_callable=AsyncMock) as mock_create_agent,
        ):
            mock_custom_span.return_value.__enter__ = MagicMock()
            mock_custom_span.return_value.__exit__ = MagicMock()
            mock_create_agent.return_value = MagicMock()
            mock_settings.property_marketing_info_tool_enabled = False
            mock_settings.startup_latency_logging_enabled = False
            mock_settings.resident_one_prompt_version = 0

            agent = ResidentAgent(context)

            async with agent:
                pass

        # Verify prefetch was called with resident_summary (flag=False path)
        mock_prefetch.assert_called_once()
        call_args = mock_prefetch.call_args
        assert call_args[0][1] == context  # context is second positional arg
        assert call_args.kwargs["resident_summary"] == "Property summary from LDP"

    @pytest.mark.asyncio
    async def test_aenter_skips_prefetch_when_property_data_already_set(self, resident_context_unified_chat_ll):
        """Test that __aenter__ skips all prefetching when property_data is already set (flag=False path).

        This is important for the agent-as-a-tool pattern where ResidentAgent is used
        inside ResidentRealtimeResponderAgent — the realtime agent prefetches first,
        and the thinker should reuse the data.
        """
        context = resident_context_unified_chat_ll
        # Pre-set property_data (simulating realtime agent already prefetched)
        context.property_data = "Already fetched property overview"
        context.disabled_modules = []
        context.disabled_tools = []
        context.pte_setting = False

        with (
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_disabled_modules_with_pte",
                new_callable=AsyncMock,
            ),
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_mcp_servers",
                return_value={},
            ),
            patch("agent_leasing.agent.resident_one_agent.agent.custom_span") as mock_custom_span,
            patch("agent_leasing.agent.resident_one_agent.agent.set_span_data"),
            patch(
                "agent_leasing.agent.resident_one_agent.agent.prefetch_property_overview_and_insights",
                new_callable=AsyncMock,
            ) as mock_prefetch,
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_channel_from_context",
                return_value="CHAT",
            ),
            patch("agent_leasing.agent.resident_one_agent.agent.settings") as mock_settings,
            patch.object(ResidentAgent, "_create_agent", new_callable=AsyncMock) as mock_create_agent,
        ):
            mock_custom_span.return_value.__enter__ = MagicMock()
            mock_custom_span.return_value.__exit__ = MagicMock()
            mock_create_agent.return_value = MagicMock()
            mock_settings.property_marketing_info_tool_enabled = False
            mock_settings.startup_latency_logging_enabled = False
            mock_settings.resident_one_prompt_version = 0

            agent = ResidentAgent(context)

            async with agent:
                pass

        # Verify prefetch was NOT called (property_data already set → entire block skipped)
        mock_prefetch.assert_not_called()
        # Verify context still has the pre-set value
        assert context.property_data == "Already fetched property overview"


class TestResidentAgentTransferToolSelection:
    """Tests for channel-based transfer tool selection in ResidentAgent._create_agent."""

    @pytest.mark.asyncio
    async def test_voice_channel_excludes_call_management_tools(self, resident_context_voice_knck):
        """Test that VOICE channel excludes call management tools for thinker usage."""
        context = resident_context_voice_knck
        context.disabled_modules = []
        context.disabled_tools = []

        mocks = _create_aenter_mocks()
        # Override to return VOICE channel
        mocks["get_channel"] = patch(
            "agent_leasing.agent.resident_one_agent.agent.get_channel_from_context",
            return_value="VOICE",
        )

        with (
            mocks["get_disabled_modules_with_pte"],
            mocks["get_disabled_tools"],
            mocks["get_mcp_servers"],
            mocks["custom_span"] as mock_custom_span,
            mocks["set_span_data"],
            mocks["prefetch"],
            mocks["get_channel"],
        ):
            mock_custom_span.return_value.__enter__ = MagicMock()
            mock_custom_span.return_value.__exit__ = MagicMock()

            async with ResidentAgent(context) as agent_wrapper:
                agent = agent_wrapper.agent()
                tool_names = [t.name for t in agent.tools]

                # VOICE channel should NOT have call management tools
                assert "transfer_to_staff_voice" not in tool_names
                assert "transfer_to_staff_text" not in tool_names
                assert not any(name.startswith("emergency_service_transfer") for name in tool_names)

    @pytest.mark.asyncio
    async def test_chat_channel_uses_transfer_to_staff_text(self, resident_context_unified_chat_ll):
        """Test that CHAT channel gets transfer_to_staff_text tool, not transfer_to_staff_voice."""
        context = resident_context_unified_chat_ll
        context.disabled_modules = []
        context.disabled_tools = []

        mocks = _create_aenter_mocks()
        # Override to return CHAT channel
        mocks["get_channel"] = patch(
            "agent_leasing.agent.resident_one_agent.agent.get_channel_from_context",
            return_value="CHAT",
        )

        with (
            mocks["get_disabled_modules_with_pte"],
            mocks["get_disabled_tools"],
            mocks["get_mcp_servers"],
            mocks["custom_span"] as mock_custom_span,
            mocks["set_span_data"],
            mocks["prefetch"],
            mocks["get_channel"],
        ):
            mock_custom_span.return_value.__enter__ = MagicMock()
            mock_custom_span.return_value.__exit__ = MagicMock()

            async with ResidentAgent(context) as agent_wrapper:
                agent = agent_wrapper.agent()
                tool_names = [t.name for t in agent.tools]

                # CHAT channel should have transfer_to_staff_text
                assert "transfer_to_staff_text" in tool_names
                # CHAT channel should NOT have transfer_to_staff_voice
                assert "transfer_to_staff_voice" not in tool_names

    @pytest.mark.asyncio
    @pytest.mark.parametrize("channel", ["SMS", "EMAIL"])
    async def test_non_voice_channels_use_transfer_to_staff_text(self, resident_context_unified_chat_ll, channel):
        """Test that SMS and EMAIL channels get transfer_to_staff_text tool."""
        context = resident_context_unified_chat_ll
        context.disabled_modules = []
        context.disabled_tools = []

        mocks = _create_aenter_mocks()
        mocks["get_channel"] = patch(
            "agent_leasing.agent.resident_one_agent.agent.get_channel_from_context",
            return_value=channel,
        )

        with (
            mocks["get_disabled_modules_with_pte"],
            mocks["get_disabled_tools"],
            mocks["get_mcp_servers"],
            mocks["custom_span"] as mock_custom_span,
            mocks["set_span_data"],
            mocks["prefetch"],
            mocks["get_channel"],
        ):
            mock_custom_span.return_value.__enter__ = MagicMock()
            mock_custom_span.return_value.__exit__ = MagicMock()

            async with ResidentAgent(context) as agent_wrapper:
                agent = agent_wrapper.agent()
                tool_names = [t.name for t in agent.tools]

                # Non-voice channels should have transfer_to_staff_text
                assert "transfer_to_staff_text" in tool_names
                # Non-voice channels should NOT have transfer_to_staff_voice
                assert "transfer_to_staff_voice" not in tool_names


class TestResidentAgentPropertyOverviewTool:
    """Tests that get_property_marketing_info is registered in the agent tools."""

    @pytest.mark.asyncio
    async def test_get_property_marketing_info_in_local_tools(self, resident_context_unified_chat_ll):
        """Test that get_property_marketing_info is registered as a local tool in ResidentAgent."""
        context = resident_context_unified_chat_ll
        context.disabled_modules = []
        context.disabled_tools = []

        mocks = _create_aenter_mocks()

        with (
            mocks["get_disabled_modules_with_pte"],
            mocks["get_disabled_tools"],
            mocks["get_mcp_servers"],
            mocks["custom_span"] as mock_custom_span,
            mocks["set_span_data"],
            mocks["prefetch"],
            mocks["get_channel"],
            patch(
                "agent_leasing.agent.resident_one_agent.agent.settings.property_marketing_info_tool_enabled",
                new=True,
            ),
        ):
            mock_custom_span.return_value.__enter__ = MagicMock()
            mock_custom_span.return_value.__exit__ = MagicMock()

            async with ResidentAgent(context) as agent_wrapper:
                agent = agent_wrapper.agent()
                tool_names = [t.name for t in agent.tools]

                assert "get_property_marketing_info" in tool_names


class TestSmsConsentTemplateRendering:
    """Tests for SMS consent template rendering in INSTRUCTIONS.md.

    SMS consent logic was moved to the blocking gate (sms_consent.py) so the template
    no longer contains SMS REVOKED MODE or SMS CONSENT MODE sections. The agent only
    runs when status is "granted", so the template always renders normal workflows.
    """

    def test_sms_consent_sections_removed_from_template(self, instructions_template):
        """Verify SMS REVOKED MODE and SMS CONSENT MODE sections have been removed.

        These sections were removed because the blocking gate in sms_consent.py
        now handles all non-granted statuses before the agent runs.
        """
        assert "SMS REVOKED MODE" not in instructions_template
        assert "SMS CONSENT MODE" not in instructions_template
        assert "sms_restricted_mode" not in instructions_template

    def test_sms_channel_always_renders_normal_workflows(self, instructions_template, mock_context, mock_settings):
        """SMS channel always renders normal workflow instructions (gate handles consent)."""
        rendered = render_template(instructions_template, "SMS", mock_context, mock_settings)

        assert "# COMMUNICATION RULES & STYLE" in rendered
        assert "# WORKFLOWS" in rendered
        assert "SMS REVOKED MODE" not in rendered
        assert "SMS CONSENT MODE" not in rendered

    def test_chat_channel_renders_normal_workflows(self, instructions_template, mock_context, mock_settings):
        """CHAT channel renders normal workflow instructions."""
        rendered = render_template(instructions_template, "CHAT", mock_context, mock_settings)

        assert "# COMMUNICATION RULES & STYLE" in rendered
        assert "# WORKFLOWS" in rendered


class TestVerificationRetryTemplateRendering:
    """Tests for verification retry template rendering in INSTRUCTIONS.md.

    The prompt delegates retry/transfer logic to the tool's action field.
    """

    def test_prompt_delegates_to_action_field(self, instructions_template, mock_context, mock_settings):
        """Prompt tells the LLM to follow the action field on failure."""
        rendered = render_template(instructions_template, "SMS", mock_context, mock_settings)

        assert "follow the `action` field" in rendered

    def test_chat_has_no_verification_section(self, instructions_template, mock_context, mock_settings):
        """CHAT channel should not render VERIFICATION REQUIREMENTS at all."""
        rendered = render_template(instructions_template, "CHAT", mock_context, mock_settings)

        assert "VERIFICATION REQUIREMENTS" not in rendered


class TestLanguageInstructionsRendering:
    """Tests that INSTRUCTIONS.md renders channel-appropriate language rules."""

    @pytest.fixture
    def instructions_template(self):
        instructions_path = os.path.join(
            os.path.dirname(__file__),
            "../../../../src/agent_leasing/agent/resident_one_agent/INSTRUCTIONS.md",
        )
        with open(instructions_path) as f:
            return f.read()

    @pytest.fixture
    def mock_context(self):
        context = MagicMock()
        context.language_code = "es"
        context.sms_consent_status = "granted"
        context.sms_needs_consent_prompt = False
        context.ask_request.product_info.source = "KNCK"
        context.ask_request.product_info.knock_resident_id = "123"
        context.ask_request.product_info.ab_resident_id.id = "456"
        context.ask_request.product_info.uc_community_id.id = "789"
        context.is_identity_verified = MagicMock(return_value=False)
        context.is_identity_verified_with_birth_year = MagicMock(return_value=False)
        context.verification_attempts = {}
        return context

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.facilities_thinker_api_enabled = True
        settings.welcome_message_sections = []
        settings.max_identity_verification_attempts = 2
        return settings

    def _render(self, template_str, channel, context, settings):
        env = jinja2.Environment(undefined=jinja2.Undefined)
        template = env.from_string(template_str)
        return template.render(
            channel=channel,
            context=context,
            settings=settings,
            disabled_modules=[],
            disabled_tools=[],
        )

    def test_voice_thinker_follows_responder_language(self, instructions_template, mock_context, mock_settings):
        """VOICE thinker must follow context.language_code set by the responder."""
        rendered = self._render(instructions_template, "VOICE", mock_context, mock_settings)

        assert "es" in rendered
        assert "set by the responder" in rendered
        assert "do not detect or switch language independently" in rendered

    def test_voice_thinker_does_not_detect_independently(self, instructions_template, mock_context, mock_settings):
        """VOICE thinker must NOT have independent language detection instructions."""
        rendered = self._render(instructions_template, "VOICE", mock_context, mock_settings)

        assert "Detect the user's language" not in rendered

    def test_chat_detects_language_independently(self, instructions_template, mock_context, mock_settings):
        """CHAT thinker detects language on its own — no responder exists."""
        rendered = self._render(instructions_template, "CHAT", mock_context, mock_settings)

        assert "If this is the first message and the user writes in a different language" in rendered
        assert "set by the responder" not in rendered

    def test_sms_detects_language_independently(self, instructions_template, mock_context, mock_settings):
        """SMS thinker detects language on its own — no responder exists."""
        rendered = self._render(instructions_template, "SMS", mock_context, mock_settings)

        assert "If this is the first message and the user writes in a different language" in rendered
        assert "set by the responder" not in rendered

    def test_voice_does_not_mention_accents(self, instructions_template, mock_context, mock_settings):
        """VOICE thinker doesn't need accent rules — the responder handles that."""
        rendered = self._render(instructions_template, "VOICE", mock_context, mock_settings)

        assert "accent" not in rendered.lower()

    def test_chat_does_not_mention_accents(self, instructions_template, mock_context, mock_settings):
        """CHAT has no accent concern — accents are a speech phenomenon."""
        rendered = self._render(instructions_template, "CHAT", mock_context, mock_settings)

        assert "accent" not in rendered.lower()


class TestNonEmergencyMaintenancePromptRendering:
    """Regression tests for silent downgrade from emergency phrasing to normal SR flow."""

    @pytest.mark.parametrize("channel", ["CHAT", "SMS", "EMAIL", "VOICE"])
    def test_prompt_requires_silent_non_emergency_downgrade(
        self, instructions_template, mock_context, mock_settings, channel
    ):
        rendered = render_template(instructions_template, channel, mock_context, mock_settings)

        assert "silently leave this workflow and continue with normal **Service Request Creation**" in rendered
        assert 'Do NOT say "this is not a maintenance emergency"' in rendered
        assert "Do not rely on a single stock line here" in rendered
        assert "Move straight to the service request offer, verification, or creation step that applies." in rendered
        assert (
            "vary the wording naturally instead of repeating the same sentence for every maintenance request."
            in rendered
        )
        assert "A range not working is not considered a maintenance emergency" in rendered


class TestPolicyAndLedgerVerificationGuardWiring:
    """Every PROTECTED_TOOLS entry enabled on the policy & ledger MCP server must have
    the verification pre-processor wired for non-CHAT channels.

    Regression for #1402/#1403: new ledger tools were added to ``PROTECTED_TOOLS`` and to
    the policy & ledger MCP server, but the wiring only attached
    ``verification_pre_processor`` to ``get_rent_information``. The remaining protected
    tools (``get_fas_account_statement``, ``get_resident_autopay_and_transactions``,
    ``get_property_details``, ``get_custom_reminders``, ``manage_custom_reminders``)
    were callable on SMS/VOICE/EMAIL without identity verification.
    """

    POLICY_AND_LEDGER_PROTECTED_TOOLS = {
        "get_rent_information",
        "get_fas_account_statement",
        "get_resident_autopay_and_transactions",
        "get_property_details",
        "get_custom_reminders",
        "manage_custom_reminders",
    }

    @pytest.mark.parametrize("channel", ["SMS", "VOICE", "EMAIL"])
    @patch("agent_leasing.agent.resident_one_agent.agent.create_voice_normalize_extras", return_value={})
    def test_verification_pre_processor_wired_for_every_protected_tool(self, _mock_voice_norm, channel):
        from agent_leasing.agent.resident_one_agent.agent import _create_policy_and_ledger_mcp_server
        from agent_leasing.agent.tools.mcp_pre_processors import VerificationError
        from agent_leasing.agent.tools.verification_check import PROTECTED_TOOLS
        from agent_leasing.settings import settings

        # Sanity: every tool we expect to be guarded must be declared in PROTECTED_TOOLS.
        for tool_name in self.POLICY_AND_LEDGER_PROTECTED_TOOLS:
            assert tool_name in PROTECTED_TOOLS, (
                f"{tool_name} missing from PROTECTED_TOOLS — verification_check will pass through"
            )

        with (
            patch(
                "agent_leasing.agent.resident_one_agent.agent.get_channel_from_context",
                return_value=channel,
            ),
            patch.object(settings, "identity_verification_enabled", True),
        ):
            context = MagicMock()
            context.disabled_modules = []
            context.disabled_tools = []
            # Unverified context: every protected pre-processor invocation should raise.
            context.is_identity_verified.return_value = False
            context.is_identity_verified_with_birth_year.return_value = False
            context.ask_request = MagicMock()
            context.ask_request.product = f"RESIDENT_ONE_{channel}"

            server = _create_policy_and_ledger_mcp_server(context)

            for tool_name in self.POLICY_AND_LEDGER_PROTECTED_TOOLS:
                processors = server.tool_pre_processors.get(tool_name, [])
                # The verification pre-processor is a closure named ``check`` from
                # ``verification_pre_processor``. Calling each processor with an
                # unverified context proves at least one of them enforces verification.
                raised = False
                for proc in processors:
                    try:
                        proc({}, context=context)
                    except VerificationError:
                        raised = True
                        break
                assert raised, (
                    f"{tool_name} on channel {channel} is not guarded by verification_pre_processor — "
                    "request would reach the MCP server without identity verification"
                )

    @patch("agent_leasing.agent.resident_one_agent.agent.create_voice_normalize_extras", return_value={})
    @patch("agent_leasing.agent.resident_one_agent.agent.get_channel_from_context", return_value="CHAT")
    def test_verification_pre_processor_not_wired_for_chat(self, _mock_channel, _mock_voice_norm):
        """CHAT channel is pre-authenticated and should not have the verification guard wired."""
        from agent_leasing.agent.resident_one_agent.agent import _create_policy_and_ledger_mcp_server
        from agent_leasing.agent.tools.mcp_pre_processors import VerificationError
        from agent_leasing.settings import settings

        with patch.object(settings, "identity_verification_enabled", True):
            context = MagicMock()
            context.disabled_modules = []
            context.disabled_tools = []
            context.is_identity_verified.return_value = False
            context.is_identity_verified_with_birth_year.return_value = False
            context.ask_request = MagicMock()
            context.ask_request.product = "RESIDENT_ONE_CHAT"

            server = _create_policy_and_ledger_mcp_server(context)

            for tool_name in self.POLICY_AND_LEDGER_PROTECTED_TOOLS:
                for proc in server.tool_pre_processors.get(tool_name, []):
                    # No pre-processor should raise VerificationError on CHAT.
                    try:
                        proc({}, context=context)
                    except VerificationError:
                        pytest.fail(f"{tool_name}: CHAT must bypass verification guard")
