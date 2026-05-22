from unittest import mock

from agent_leasing.api.auth.auth_helper import (
    get_facilities_mcp_auth_token,
    get_knock_mcp_auth_token,
    get_loft_mcp_auth_token,
    get_onsite_mcp_auth_token,
)


@mock.patch("agent_leasing.api.auth.auth_helper.get_auth_token", return_value="test-value")
class TestGetFacilitiesMcpAuthToken:
    async def test_main(self, mock_get_auth_token, resident_context_chat_ll):
        mock_get_auth_token.return_value == await get_facilities_mcp_auth_token(resident_context_chat_ll)


@mock.patch("agent_leasing.api.auth.auth_helper.get_auth_token", return_value="test-value")
class TestGetKnockMcpAuthToken:
    async def test_main(self, mock_get_auth_token, resident_context_chat_ll):
        mock_get_auth_token.return_value == await get_knock_mcp_auth_token(resident_context_chat_ll)


@mock.patch("agent_leasing.api.auth.auth_helper.get_auth_token", return_value="test-value")
class TestGetLoftMcpAuthToken:
    async def test_when_context_has_cidp_token(self, mock_get_auth_token, resident_context_chat_ll):
        resident_context_chat_ll.ask_request.product_info.uc_consumer_identity_token.id == await (
            get_loft_mcp_auth_token(resident_context_chat_ll)
        )

    async def test_when_context_does_not_have_cidp_token(self, mock_get_auth_token, resident_context_chat_ll):
        resident_context_chat_ll.ask_request.product_info.uc_consumer_identity_token = None
        mock_get_auth_token.return_value == await get_loft_mcp_auth_token(resident_context_chat_ll)

    async def test_when_does_not_have_context(self, mock_get_auth_token):
        mock_get_auth_token.return_value == await get_loft_mcp_auth_token(None)


@mock.patch("agent_leasing.api.auth.auth_helper.get_auth_token", return_value="test-value")
class TestGetOnesiteMcpAuthToken:
    async def test_main(self, mock_get_auth_token, resident_context_chat_ll):
        mock_get_auth_token.return_value == await get_onsite_mcp_auth_token(resident_context_chat_ll)
