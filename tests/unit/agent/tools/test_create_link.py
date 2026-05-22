"""Tests for create_link function tool."""

import json
from unittest.mock import Mock, patch

import pytest
from agents import tool_context

# Import helper functions and internal implementation
from agent_leasing.agent.tools.create_link.create_link import (
    _create_all_open_service_request_link,
    _create_amenities_link,
    _create_community_events_link,
    _create_community_wall_link,
    _create_front_desk_instructions_link,
    _create_human_hand_off_link,
    _create_leasing_link,
    _create_package_link,
    _create_parking_link,
    _create_parking_passes_link,
    _create_payment_and_ledger_link,
    _create_resident_checklist_link,
    _create_service_request_link,
    _create_single_service_request_link,
    create_link,
)
from agent_leasing.api.model import AskRequest, Channel, ProductInfo, StaticPaths


@pytest.fixture
def mock_ask_request():
    """Create a mock AskRequest with portal configuration."""
    mock_request = Mock(spec=AskRequest)
    mock_request.product_info = Mock(spec=ProductInfo)
    mock_request.product_info.uc_portal_base_url = "http://hello.com"
    mock_request.product_info.static_paths = Mock(spec=StaticPaths)
    return mock_request


@pytest.fixture
def mock_tool_ctx(mock_ask_request):
    """Create a mock ToolContext compatible with FunctionTool.on_invoke_tool."""
    mock_ctx = Mock(spec=tool_context.ToolContext)
    mock_ctx.context = Mock()
    mock_ctx.context.ask_request = mock_ask_request
    mock_ctx.tool_name = "create_link"
    return mock_ctx


@pytest.fixture
def static_paths_with_values():
    """Create StaticPaths with configured values for all fields."""
    return StaticPaths(
        payment_and_ledger="/portal/payments",
        amenities="/portal/reservations",
        parking="/portal/parking-passes",
        package="/portal/packages",
        community_events="/portal/events",
        human_hand_off="/portal/messenger",
        service_request="/portal/mr",
        front_desk_instructions="/portal/fdi",
        resident_checklist="/portal/resident-checklist",
        parking_passes="/portal/parking-passes",
        community_wall="/portal/wall",
        single_service_request="/portal/mr/detail/mrId",
        all_open_service_request="/portal/mr/index/status/open",
        leasing="/portal/leasing",
    )


@pytest.fixture
def static_paths_with_none_values():
    """Create StaticPaths with None values for all fields."""
    return StaticPaths(
        payment_and_ledger=None,
        amenities=None,
        parking=None,
        package=None,
        community_events=None,
        human_hand_off=None,
        service_request=None,
        front_desk_instructions=None,
        resident_checklist=None,
        parking_passes=None,
        community_wall=None,
        single_service_request=None,
        all_open_service_request=None,
        leasing=None,
    )


class TestCreateLinkHelperFunctions:
    """Test cases for individual helper functions."""

    @pytest.mark.parametrize(
        "function_name,custom_expected",
        [
            (_create_payment_and_ledger_link, "http://hello.com/portal/payments"),
            (_create_amenities_link, "http://hello.com/portal/reservations"),
            (_create_parking_link, "http://hello.com/portal/parking-passes"),
            (_create_package_link, "http://hello.com/portal/packages"),
            (_create_community_events_link, "http://hello.com/portal/events"),
            (_create_human_hand_off_link, "http://hello.com/portal/messenger"),
            (_create_service_request_link, "http://hello.com/portal/mr"),
            (_create_front_desk_instructions_link, "http://hello.com/portal/fdi"),
            (
                _create_resident_checklist_link,
                "http://hello.com/portal/resident-checklist",
            ),
            (_create_parking_passes_link, "http://hello.com/portal/parking-passes"),
            (_create_community_wall_link, "http://hello.com/portal/wall"),
            (
                _create_single_service_request_link,
                "http://hello.com/portal/mr",  # no mr_id → falls back to generic SR page
            ),
            (
                _create_all_open_service_request_link,
                "http://hello.com/portal/mr/index/status/open",
            ),
            (_create_leasing_link, "http://hello.com/portal/leasing"),
        ],
    )
    def test_helper_functions_with_custom_paths(self, function_name, custom_expected, static_paths_with_values):
        """Test helper functions with custom static paths."""
        result = function_name("http://hello.com", static_paths_with_values)
        assert result == custom_expected

    def test_single_service_request_with_mr_id(self, static_paths_with_values):
        """Test that mr_id is appended after the mrId route segment."""
        result = _create_single_service_request_link("http://hello.com", static_paths_with_values, mr_id="3084436")
        assert result == "http://hello.com/portal/mr/detail/mrId/3084436"

    def test_single_service_request_without_mr_id_falls_back(self, static_paths_with_values):
        """Test that omitting mr_id falls back to generic service_request link."""
        result = _create_single_service_request_link("http://hello.com", static_paths_with_values)
        assert result == "http://hello.com/portal/mr"


class TestCreateLink:
    """Test cases for create_link function - verifying correct string output to LLM."""

    @pytest.mark.parametrize(
        "link_type,expected_url",
        [
            ("payment_and_ledger", "http://hello.com/portal/payments"),
            ("amenities", "http://hello.com/portal/reservations"),
            ("parking", "http://hello.com/portal/parking-passes"),
            ("package", "http://hello.com/portal/packages"),
            ("community_events", "http://hello.com/portal/events"),
            ("human_hand_off", "http://hello.com/portal/messenger"),
            ("service_request", "http://hello.com/portal/mr"),
            ("front_desk_instructions", "http://hello.com/portal/fdi"),
            ("parking_passes", "http://hello.com/portal/parking-passes"),
            ("community_wall", "http://hello.com/portal/wall"),
            ("single_service_request", "http://hello.com/portal/mr"),  # no mr_id → generic
            (
                "all_open_service_request",
                "http://hello.com/portal/mr/index/status/open",
            ),
            ("leasing", "http://hello.com/portal/leasing"),
        ],
    )
    @pytest.mark.asyncio
    async def test_create_link_returns_correct_url(
        self, mock_tool_ctx, static_paths_with_values, link_type, expected_url
    ):
        """Test that create_link returns the correct URL string for each link type."""
        mock_tool_ctx.context.ask_request.product_info.static_paths = static_paths_with_values

        result = await create_link.on_invoke_tool(mock_tool_ctx, json.dumps({"link_type": link_type}))
        assert result == expected_url

    @pytest.mark.asyncio
    async def test_create_link_missing_base_url(self, mock_tool_ctx, static_paths_with_values):
        """Test error handling when base URL is missing."""
        mock_tool_ctx.context.ask_request.product_info.uc_portal_base_url = None
        mock_tool_ctx.context.ask_request.product_info.static_paths = static_paths_with_values

        result = await create_link.on_invoke_tool(mock_tool_ctx, json.dumps({"link_type": "payment_and_ledger"}))
        assert result == "Error building payment_and_ledger link: Portal base URL not configured"

    @pytest.mark.asyncio
    async def test_create_link_missing_static_paths(self, mock_tool_ctx):
        """Test error handling when static paths is None."""
        mock_tool_ctx.context.ask_request.product_info.static_paths = None

        result = await create_link.on_invoke_tool(mock_tool_ctx, json.dumps({"link_type": "parking"}))
        assert result == "Error building parking link: Static paths not configured"

    @pytest.mark.asyncio
    async def test_create_link_unknown_link_type(self, mock_tool_ctx, static_paths_with_values):
        """Test tool input validation for unknown link type."""
        mock_tool_ctx.context.ask_request.product_info.static_paths = static_paths_with_values

        result = await create_link.on_invoke_tool(mock_tool_ctx, json.dumps({"link_type": "unknown_type"}))
        assert "Invalid JSON input for tool create_link" in result
        assert "literal_error" in result

    @pytest.mark.asyncio
    async def test_create_link_exception_handling(self, mock_tool_ctx):
        """Test exception handling in create_link function."""
        # Mock an exception by making the context access fail
        mock_tool_ctx.context.ask_request.product_info = None

        result = await create_link.on_invoke_tool(mock_tool_ctx, json.dumps({"link_type": "package"}))
        assert "Error building package link:" in result
        assert "NoneType" in result or "AttributeError" in result

    @pytest.mark.asyncio
    @patch("agent_leasing.agent.tools.create_link.create_link.settings")
    async def test_create_link_single_sr_chat_thinker_enabled(
        self, mock_settings, mock_tool_ctx, static_paths_with_values
    ):
        """Test that mr_id is appended on chat channel with thinker API enabled."""
        mock_settings.facilities_thinker_api_enabled = True
        mock_tool_ctx.context.ask_request.product_info.static_paths = static_paths_with_values
        mock_tool_ctx.context.ask_request.conversation_type = Channel.CHAT

        result = await create_link.on_invoke_tool(
            mock_tool_ctx, json.dumps({"link_type": "single_service_request", "mr_id": "3084436"})
        )
        assert result == "http://hello.com/portal/mr/detail/mrId/3084436"

    @pytest.mark.asyncio
    @patch("agent_leasing.agent.tools.create_link.create_link.settings")
    async def test_create_link_single_sr_email_thinker_enabled(
        self, mock_settings, mock_tool_ctx, static_paths_with_values
    ):
        """Test that mr_id is ignored on email channel (returns display SR#, not URL ID)."""
        mock_settings.facilities_thinker_api_enabled = True
        mock_tool_ctx.context.ask_request.product_info.static_paths = static_paths_with_values
        mock_tool_ctx.context.ask_request.conversation_type = Channel.EMAIL

        result = await create_link.on_invoke_tool(
            mock_tool_ctx, json.dumps({"link_type": "single_service_request", "mr_id": "5415-1"})
        )
        assert result == "http://hello.com/portal/mr"

    @pytest.mark.asyncio
    @patch("agent_leasing.agent.tools.create_link.create_link.settings")
    async def test_create_link_single_sr_thinker_disabled(
        self, mock_settings, mock_tool_ctx, static_paths_with_values
    ):
        """Test that mr_id is ignored when thinker API is disabled (MCP path)."""
        mock_settings.facilities_thinker_api_enabled = False
        mock_tool_ctx.context.ask_request.product_info.static_paths = static_paths_with_values
        mock_tool_ctx.context.ask_request.conversation_type = Channel.CHAT

        result = await create_link.on_invoke_tool(
            mock_tool_ctx, json.dumps({"link_type": "single_service_request", "mr_id": "3603-1"})
        )
        assert result == "http://hello.com/portal/mr"

    @pytest.mark.asyncio
    @patch("agent_leasing.agent.tools.create_link.create_link.settings")
    async def test_create_link_single_sr_no_mr_id(self, mock_settings, mock_tool_ctx, static_paths_with_values):
        """Test that omitting mr_id falls back to generic service request page."""
        mock_settings.facilities_thinker_api_enabled = True
        mock_tool_ctx.context.ask_request.product_info.static_paths = static_paths_with_values
        mock_tool_ctx.context.ask_request.conversation_type = Channel.CHAT

        result = await create_link.on_invoke_tool(mock_tool_ctx, json.dumps({"link_type": "single_service_request"}))
        assert result == "http://hello.com/portal/mr"

    @pytest.mark.asyncio
    async def test_create_link_different_base_urls(self, mock_tool_ctx, static_paths_with_values):
        """Test create_link with different base URL formats."""
        mock_tool_ctx.context.ask_request.product_info.static_paths = static_paths_with_values

        test_urls = [
            "http://hello.com",
            "https://secure.example.com",
            "http://localhost:3000",
            "https://portal.realpage.com",
        ]

        for base_url in test_urls:
            mock_tool_ctx.context.ask_request.product_info.uc_portal_base_url = base_url
            result = await create_link.on_invoke_tool(mock_tool_ctx, json.dumps({"link_type": "package"}))
            expected = f"{base_url}/portal/packages"
            assert result == expected, f"Failed for base_url: {base_url}"

    @pytest.mark.asyncio
    async def test_create_link_fallback_to_base_url_when_path_is_none(self, mock_tool_ctx):
        """Test that a link type with None path falls back to the portal homepage."""
        mock_tool_ctx.context.ask_request.product_info.static_paths = StaticPaths(leasing=None)

        result = await create_link.on_invoke_tool(mock_tool_ctx, json.dumps({"link_type": "leasing"}))
        assert result == "http://hello.com"

    @pytest.mark.asyncio
    async def test_create_link_fallback_applies_to_any_link_type(self, mock_tool_ctx):
        """Test that the fallback applies generically — not just to leasing."""
        mock_tool_ctx.context.ask_request.product_info.static_paths = StaticPaths(parking=None)

        result = await create_link.on_invoke_tool(mock_tool_ctx, json.dumps({"link_type": "parking"}))
        assert result == "http://hello.com"
