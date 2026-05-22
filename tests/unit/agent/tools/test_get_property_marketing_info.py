from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestGetPropertyMarketingInfo:
    """Tests for the get_property_marketing_info function tool."""

    @pytest.mark.asyncio
    async def test_returns_resident_summary_on_success(self):
        """Returns LDP resident_summary when fetch succeeds."""
        mock_ctx = MagicMock()
        mock_ctx.context.property_id = "prop-123"

        with patch(
            "agent_leasing.agent.tools.get_property_marketing_info.fetch_ldp_property_data",
            new_callable=AsyncMock,
            return_value={"resident_summary": "Welcome to Sunset Apartments!"},
        ):
            from agent_leasing.agent.tools.get_property_marketing_info import (
                _get_property_marketing_info_impl,
            )

            result = await _get_property_marketing_info_impl(mock_ctx)

        assert result == "Welcome to Sunset Apartments!"

    @pytest.mark.asyncio
    async def test_returns_fallback_when_summary_is_none(self):
        """Returns fallback message when resident_summary is absent."""
        mock_ctx = MagicMock()
        mock_ctx.context.property_id = "prop-123"

        with patch(
            "agent_leasing.agent.tools.get_property_marketing_info.fetch_ldp_property_data",
            new_callable=AsyncMock,
            return_value={"resident_summary": None},
        ):
            from agent_leasing.agent.tools.get_property_marketing_info import (
                _get_property_marketing_info_impl,
            )

            result = await _get_property_marketing_info_impl(mock_ctx)

        assert result == "No marketing information available."

    @pytest.mark.asyncio
    async def test_returns_fallback_on_ldp_error(self):
        """Returns fallback message when LDP raises an error."""
        from agent_leasing.clients.ldp import LDPError

        mock_ctx = MagicMock()
        mock_ctx.context.property_id = "prop-123"

        with patch(
            "agent_leasing.agent.tools.get_property_marketing_info.fetch_ldp_property_data",
            new_callable=AsyncMock,
            side_effect=LDPError("Connection failed"),
        ):
            from agent_leasing.agent.tools.get_property_marketing_info import (
                _get_property_marketing_info_impl,
            )

            result = await _get_property_marketing_info_impl(mock_ctx)

        assert result == "No marketing information available."
