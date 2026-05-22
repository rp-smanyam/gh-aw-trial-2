from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from agent_leasing.agent.resident_one_agent.agent_helper import (
    prefetch_property_overview_and_insights,
)


@pytest.fixture
def mock_mcp_servers():
    """Mock MCP servers."""
    return {
        "knock_mcp_server": MagicMock(),
        "facilities_mcp_server": MagicMock(),
        "loft_mcp_server": MagicMock(),
    }


@pytest.fixture
def mock_context():
    """Mock session context."""
    context = MagicMock()
    context.property_id = 123
    context.disabled_modules = []
    context.previous_response_id = None
    context.has_openai_server_history = False

    # Mock product info
    context.ask_request.product_info.uc_company_id.id = 456
    context.ask_request.product_info.uc_resident_household_id.id = 789
    context.ask_request.product_info.uc_property_id.id = 123
    context.ask_request.product_info.uc_resident_member_id.id = 101
    context.ask_request.product_info.ab_resident_id.id = 202
    context.ask_request.product_info.uc_community_id.id = 303

    return context


@pytest.mark.asyncio
@patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool")
async def test_prefetch_no_insights(
    mock_call_and_save_tool,
    mock_settings,
    mock_mcp_servers,
    mock_context,
):
    """Test prefetch when 'insights' not in welcome_message_sections — no MCP calls made."""
    mock_settings.welcome_message_sections = []
    mock_call_and_save_tool.return_value = AsyncMock()

    await prefetch_property_overview_and_insights(
        mock_mcp_servers,
        mock_context,
    )

    # No insight tools should be called
    assert mock_call_and_save_tool.call_count == 0


@pytest.mark.asyncio
@patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.prefetch_active_service_requests")
async def test_prefetch_with_service_requests(
    mock_facilities_thinker,
    mock_call_and_save_tool,
    mock_settings,
    mock_mcp_servers,
    mock_context,
):
    """Test prefetch with service requests enabled; PACKAGES/EVENTS disabled to isolate."""
    mock_settings.welcome_message_sections = ["insights"]
    mock_settings.facilities_thinker_api_enabled = True
    mock_settings.sr_prefetch_via_mcp = False
    mock_call_and_save_tool.return_value = AsyncMock()
    mock_context.disabled_modules = ["PACKAGES", "EVENTS"]

    mock_context.ask_request.product_info.uc_consumer_identity_token.id = "cidp-token"
    mock_context.ask_request.product_info.resident_phone = "+15555550123"

    await prefetch_property_overview_and_insights(
        mock_mcp_servers,
        mock_context,
    )

    assert mock_call_and_save_tool.call_count == 0
    assert mock_facilities_thinker.call_count == 1


@pytest.mark.asyncio
@patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool")
async def test_prefetch_service_requests_via_mcp_skips_pre_processors(
    mock_call_and_save_tool,
    mock_settings,
    mock_mcp_servers,
    mock_context,
):
    """Test that the legacy MCP path for service requests passes skip_pre_processors=True.

    Prefetch runs before the resident verifies, so the verification pre-processor
    must be bypassed to avoid a VERIFICATION_REQUIRED failure on the first turn.
    """
    mock_settings.welcome_message_sections = ["insights"]
    mock_settings.facilities_thinker_api_enabled = False
    mock_call_and_save_tool.return_value = AsyncMock()
    mock_context.disabled_modules = ["PACKAGES", "EVENTS"]

    await prefetch_property_overview_and_insights(
        mock_mcp_servers,
        mock_context,
    )

    assert mock_call_and_save_tool.call_count == 1
    _, kwargs = mock_call_and_save_tool.call_args
    assert kwargs.get("skip_pre_processors") is True


@pytest.mark.asyncio
@patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool")
async def test_prefetch_with_packages(
    mock_call_and_save_tool,
    mock_settings,
    mock_mcp_servers,
    mock_context,
):
    """Test prefetch with packages enabled; MR/EVENTS disabled to isolate."""
    mock_settings.welcome_message_sections = ["insights"]
    mock_call_and_save_tool.return_value = AsyncMock()
    mock_context.disabled_modules = ["MR", "EVENTS"]

    await prefetch_property_overview_and_insights(
        mock_mcp_servers,
        mock_context,
    )

    assert mock_call_and_save_tool.call_count == 1

    tool_names = [call[0][1] for call in mock_call_and_save_tool.call_args_list]
    assert "get_residents_packages" in tool_names


@pytest.mark.asyncio
@patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool")
async def test_prefetch_with_community_events(
    mock_call_and_save_tool,
    mock_settings,
    mock_mcp_servers,
    mock_context,
):
    """Test prefetch with community events enabled; MR/PACKAGES disabled to isolate."""
    mock_settings.welcome_message_sections = ["insights"]
    mock_call_and_save_tool.return_value = AsyncMock()
    mock_context.disabled_modules = ["MR", "PACKAGES"]

    await prefetch_property_overview_and_insights(
        mock_mcp_servers,
        mock_context,
    )

    assert mock_call_and_save_tool.call_count == 1

    tool_names = [call[0][1] for call in mock_call_and_save_tool.call_args_list]
    assert "fetch_user_signed_up_community_events" in tool_names


@pytest.mark.asyncio
@patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool")
async def test_prefetch_skips_community_events_when_previous_response(
    mock_call_and_save_tool,
    mock_settings,
    mock_mcp_servers,
    mock_context,
):
    """Test that community events are skipped when previous_response_id exists."""
    mock_settings.welcome_message_sections = ["insights"]
    mock_call_and_save_tool.return_value = AsyncMock()
    mock_context.previous_response_id = "some-id"
    mock_context.has_openai_server_history = True

    await prefetch_property_overview_and_insights(
        mock_mcp_servers,
        mock_context,
    )

    assert mock_call_and_save_tool.call_count == 0


@pytest.mark.asyncio
@patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool")
async def test_prefetch_skips_packages_when_module_disabled(
    mock_call_and_save_tool,
    mock_settings,
    mock_mcp_servers,
    mock_context,
):
    """Test that packages are skipped when PACKAGES module is disabled, but community events still prefetch."""
    mock_settings.welcome_message_sections = ["insights"]
    mock_call_and_save_tool.return_value = AsyncMock()
    mock_context.disabled_modules = ["PACKAGES", "MR"]

    await prefetch_property_overview_and_insights(
        mock_mcp_servers,
        mock_context,
    )

    # Packages skipped (PACKAGES disabled), but community events still fetched (EVENTS enabled)
    assert mock_call_and_save_tool.call_count == 1
    tool_names = [call[0][1] for call in mock_call_and_save_tool.call_args_list]
    assert "get_residents_packages" not in tool_names
    assert "fetch_user_signed_up_community_events" in tool_names


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "disabled_modules, expected_packages, expected_events",
    [
        pytest.param(["PACKAGES"], False, True, id="PACKAGES_disabled_EVENTS_enabled"),
        pytest.param(["EVENTS"], True, False, id="PACKAGES_enabled_EVENTS_disabled"),
        pytest.param(["PACKAGES", "EVENTS"], False, False, id="both_disabled"),
        pytest.param([], True, True, id="both_enabled"),
    ],
)
@patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool")
async def test_prefetch_packages_and_events_module_independence(
    mock_call_and_save_tool,
    mock_settings,
    mock_mcp_servers,
    mock_context,
    disabled_modules,
    expected_packages,
    expected_events,
):
    """Regression test for KNCK-39179: PACKAGES and EVENTS modules are checked independently."""
    mock_settings.welcome_message_sections = ["insights"]
    mock_call_and_save_tool.return_value = AsyncMock()
    # Disable MR in every case so the SR prefetch doesn't add noise to the tool_names list.
    mock_context.disabled_modules = disabled_modules + ["MR"]

    await prefetch_property_overview_and_insights(mock_mcp_servers, mock_context)

    tool_names = [call[0][1] for call in mock_call_and_save_tool.call_args_list]
    assert ("get_residents_packages" in tool_names) == expected_packages
    assert ("fetch_user_signed_up_community_events" in tool_names) == expected_events


@pytest.mark.asyncio
@patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.prefetch_active_service_requests")
async def test_prefetch_skips_all_insights_after_first_turn(
    mock_facilities_thinker,
    mock_call_and_save_tool,
    mock_settings,
    mock_mcp_servers,
    mock_context,
):
    """Test that all insights are skipped after the first turn."""
    mock_settings.welcome_message_sections = ["insights"]
    mock_call_and_save_tool.return_value = AsyncMock()
    mock_context.previous_response_id = "some-id"
    mock_context.has_openai_server_history = True

    await prefetch_property_overview_and_insights(
        mock_mcp_servers,
        mock_context,
    )

    assert mock_call_and_save_tool.call_count == 0
    assert mock_facilities_thinker.call_count == 0


@pytest.mark.asyncio
@patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.prefetch_active_service_requests")
async def test_prefetch_skips_service_requests_when_mr_disabled(
    mock_facilities_thinker,
    mock_call_and_save_tool,
    mock_settings,
    mock_mcp_servers,
    mock_context,
):
    """Test that service requests are skipped when MR module is disabled."""
    mock_settings.welcome_message_sections = ["insights"]
    mock_call_and_save_tool.return_value = AsyncMock()
    # Disable PACKAGES and EVENTS too so the assertion only counts SR behavior.
    mock_context.disabled_modules = ["MR", "PACKAGES", "EVENTS"]

    await prefetch_property_overview_and_insights(
        mock_mcp_servers,
        mock_context,
    )

    assert mock_call_and_save_tool.call_count == 0
    assert mock_facilities_thinker.call_count == 0


@pytest.mark.asyncio
@patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.logger")
async def test_prefetch_handles_exceptions(
    mock_logger,
    mock_call_and_save_tool,
    mock_settings,
    mock_mcp_servers,
    mock_context,
):
    """Test that exceptions during prefetch are logged."""
    mock_settings.welcome_message_sections = ["insights"]
    mock_context.disabled_modules = ["PACKAGES", "EVENTS"]

    mock_context.ask_request.product_info.uc_consumer_identity_token.id = "cidp-token"
    mock_context.ask_request.product_info.resident_phone = "+15555550123"

    mock_call_and_save_tool.return_value = AsyncMock()

    async def fake_gather(*args, **kwargs):
        return [ValueError("Test error")]

    with patch(
        "agent_leasing.agent.resident_one_agent.agent_helper.asyncio.gather",
        new=fake_gather,
    ):
        await prefetch_property_overview_and_insights(
            mock_mcp_servers,
            mock_context,
        )

    # Verify error was logged with the expected message and exception object
    mock_logger.error.assert_called_once_with("Unable to prefetch MCP data", error=ANY)


@pytest.mark.asyncio
@patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool", return_value=AsyncMock())
@patch(
    "agent_leasing.agent.resident_one_agent.agent_helper.prefetch_active_service_requests",
    return_value=AsyncMock(),
)
async def test_prefetch_with_all_insights(
    mock_function_tool,
    mock_call_and_save_tool,
    mock_settings,
    mock_mcp_servers,
    mock_context,
):
    """Test prefetch with all insights enabled."""
    mock_settings.welcome_message_sections = ["insights"]
    mock_settings.facilities_thinker_api_enabled = True
    mock_settings.sr_prefetch_via_mcp = False

    mock_context.ask_request.product_info.uc_consumer_identity_token.id = "cidp-token"
    mock_context.ask_request.product_info.resident_phone = "+15555550123"

    await prefetch_property_overview_and_insights(
        mock_mcp_servers,
        mock_context,
    )

    # Should call for all 3 insights (two MCP calls and one function tool)
    assert mock_call_and_save_tool.call_count == 2
    assert mock_function_tool.call_count == 1

    mcp_tool_names = [call[0][1] for call in mock_call_and_save_tool.call_args_list]
    assert "get_residents_packages" in mcp_tool_names
    assert "fetch_user_signed_up_community_events" in mcp_tool_names


@pytest.mark.asyncio
@patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool")
async def test_prefetch_sets_property_data_from_resident_summary(
    mock_call_and_save_tool,
    mock_settings,
    mock_mcp_servers,
    mock_context,
):
    """Test that resident_summary sets context.property_data when tool is disabled (flag=False)."""
    mock_settings.welcome_message_sections = []
    mock_settings.property_marketing_info_tool_enabled = False
    mock_call_and_save_tool.return_value = AsyncMock()
    mock_context.property_data = None

    await prefetch_property_overview_and_insights(
        mock_mcp_servers,
        mock_context,
        resident_summary="Property summary from LDP",
    )

    assert mock_context.property_data == "Property summary from LDP"
    assert mock_call_and_save_tool.call_count == 0


@pytest.mark.asyncio
@patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool")
async def test_prefetch_no_property_data_when_resident_summary_is_none(
    mock_call_and_save_tool,
    mock_settings,
    mock_mcp_servers,
    mock_context,
):
    """Test that property_data is not set when resident_summary is None (flag=False)."""
    mock_settings.welcome_message_sections = []
    mock_settings.property_marketing_info_tool_enabled = False
    mock_call_and_save_tool.return_value = AsyncMock()
    mock_context.property_data = None

    await prefetch_property_overview_and_insights(
        mock_mcp_servers,
        mock_context,
        resident_summary=None,
    )

    assert mock_context.property_data is None


@pytest.mark.asyncio
@patch("agent_leasing.agent.resident_one_agent.agent_helper.settings")
@patch("agent_leasing.agent.resident_one_agent.agent_helper.call_and_save_tool")
async def test_prefetch_does_not_overwrite_existing_property_data(
    mock_call_and_save_tool,
    mock_settings,
    mock_mcp_servers,
    mock_context,
):
    """Test that resident_summary does not overwrite existing property_data (flag=False)."""
    mock_settings.welcome_message_sections = []
    mock_settings.property_marketing_info_tool_enabled = False
    mock_call_and_save_tool.return_value = AsyncMock()
    mock_context.property_data = "Existing data"

    await prefetch_property_overview_and_insights(
        mock_mcp_servers,
        mock_context,
        resident_summary="New summary from LDP",
    )

    # property_data should NOT be overwritten
    assert mock_context.property_data == "Existing data"
