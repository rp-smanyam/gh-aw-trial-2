"""Tests for agent_service module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_leasing.agent.util import UnsupportedAgentException
from agent_leasing.api.model import Flow
from agent_leasing.clients.ldp import get_disabled_tools_from_disabled_modules
from agent_leasing.models.context import SessionScope
from agent_leasing.services.agent_service import (
    CONVERSATION_ID_HEADER,
    AgentRequest,
    build_agent_request,
    ensure_conversation_id,
    get_flows,
    save_conversation_id,
    save_previous_response_id,
)
from agent_leasing.settings import settings


@pytest.fixture
def mock_context(ask_request_resident_chat_ll):
    return SessionScope(
        ask_request=ask_request_resident_chat_ll,
        thread_id="test-thread-123",
        previous_response_id="prev-response-456",
    )


class TestBuildAgentRequest:
    """Tests for build_agent_request function."""

    @pytest.mark.asyncio
    async def test_build_agent_request_new_context(self, ask_request_resident_chat_ll):
        """Test build_agent_request with a new context (not in memory)."""
        with (
            patch(
                "agent_leasing.services.agent_service.memory.get_context", new_callable=AsyncMock
            ) as mock_get_context,
            patch("agent_leasing.services.agent_service.agent_selector") as mock_agent_selector,
        ):
            # Setup mocks
            mock_get_context.return_value = None  # No existing context
            mock_agent = MagicMock()
            mock_agent.name = "TestAgent"
            mock_agent_selector.return_value = mock_agent

            # Execute
            settings.expire_chat = "12m"
            result = await build_agent_request(ask_request_resident_chat_ll)

            # Assertions
            assert isinstance(result, AgentRequest)
            assert result.trace_id is not None
            assert result.language_code == "en"
            assert result.workflow_name == "RESIDENT_ONE_CHAT"
            assert len(result.flows) == 1
            assert result.flows[0].name == "RESIDENT_ONE_CHAT"
            assert result.context is not None
            assert result.agent == mock_agent
            assert result.previous_response_id is None  # No previous response for new context
            assert result.expire == "12m"

            # Verify context was created
            assert result.context.thread_id is not None

    @pytest.mark.asyncio
    async def test_build_agent_request_existing_context(self, ask_request_resident_chat_ll, mock_context):
        """Test build_agent_request with an existing context in memory."""
        with (
            patch(
                "agent_leasing.services.agent_service.memory.get_context", new_callable=AsyncMock
            ) as mock_get_context,
            patch("agent_leasing.services.agent_service.agent_selector") as mock_agent_selector,
        ):
            # Setup mocks
            mock_get_context.return_value = mock_context
            mock_agent = MagicMock()
            mock_agent.name = "TestAgent"
            mock_agent_selector.return_value = mock_agent

            # Execute
            result = await build_agent_request(ask_request_resident_chat_ll)

            # Assertions
            assert isinstance(result, AgentRequest)
            assert result.context == mock_context
            assert result.previous_response_id == "prev-response-456"  # From existing context
            assert result.thread_id == "test-thread-123"

    @pytest.mark.asyncio
    async def test_build_agent_request_unsupported_agent(self, ask_request_resident_chat_ll):
        """Test build_agent_request raises exception for unsupported agent."""
        with (
            patch(
                "agent_leasing.services.agent_service.memory.get_context", new_callable=AsyncMock
            ) as mock_get_context,
            patch("agent_leasing.services.agent_service.agent_selector") as mock_agent_selector,
        ):
            # Setup mocks
            mock_get_context.return_value = None
            mock_agent_selector.side_effect = UnsupportedAgentException("Unsupported agent")

            # Execute and assert
            with pytest.raises(UnsupportedAgentException):
                await build_agent_request(ask_request_resident_chat_ll)

    @pytest.mark.asyncio
    async def test_build_agent_request_headers_contains_trace_id(self, ask_request_resident_chat_ll):
        """Test that build_agent_request includes trace ID in headers."""
        with (
            patch(
                "agent_leasing.services.agent_service.memory.get_context", new_callable=AsyncMock
            ) as mock_get_context,
            patch("agent_leasing.services.agent_service.agent_selector") as mock_agent_selector,
        ):
            # Setup mocks
            mock_get_context.return_value = None
            mock_agent = MagicMock()
            mock_agent.name = "TestAgent"
            mock_agent_selector.return_value = mock_agent

            # Execute
            result = await build_agent_request(ask_request_resident_chat_ll)

            # Assertions
            assert "X-OpenAPI-Trace-Id" in result.headers
            assert result.headers["X-OpenAPI-Trace-Id"] == result.trace_id

    @pytest.mark.asyncio
    async def test_build_agent_request_metadata_includes_all_fields(self, ask_request_resident_chat_ll):
        """Test that build_agent_request creates metadata with all expected fields."""
        with (
            patch(
                "agent_leasing.services.agent_service.memory.get_context", new_callable=AsyncMock
            ) as mock_get_context,
            patch("agent_leasing.services.agent_service.agent_selector") as mock_agent_selector,
        ):
            # Setup mocks
            mock_get_context.return_value = None
            mock_agent = MagicMock()
            mock_agent.name = "TestAgent"
            mock_agent_selector.return_value = mock_agent

            # Execute
            result = await build_agent_request(ask_request_resident_chat_ll)

            # Assertions
            assert "chat-session-id" in result.metadata
            assert result.metadata["chat-session-id"] == ask_request_resident_chat_ll.chat_session_id
            assert "property-id" in result.metadata
            assert result.metadata["property-id"] == ask_request_resident_chat_ll.property_id
            assert "product" in result.metadata
            assert result.metadata["product"] == ask_request_resident_chat_ll.product
            assert "agent" in result.metadata
            assert result.metadata["agent"] == "TestAgent"


class TestGetFlows:
    """Tests for get_flows function."""

    def test_get_flows_with_thinker_tool(self):
        """Test _get_flows extracts thinker_tool function calls."""
        # Create mock result with thinker_tool
        result = MagicMock()
        mock_response = MagicMock()

        tool_call = MagicMock()
        tool_call.type = "function_call"
        tool_call.name = "community_thinker_tool"

        mock_response.output = [tool_call]
        result.raw_responses = [mock_response]

        flows = get_flows(result)

        assert len(flows) == 1
        assert isinstance(flows[0], Flow)
        # Flow class transforms "community_thinker_tool" to "COMMUNITY_FLOW"
        assert flows[0].name == "COMMUNITY_FLOW"

    def test_get_flows_with_transfer_to_staff(self):
        """Test _get_flows extracts transfer_to_staff_text function calls."""
        result = MagicMock()
        mock_response = MagicMock()

        tool_call = MagicMock()
        tool_call.type = "function_call"
        tool_call.name = "transfer_to_staff_text"

        mock_response.output = [tool_call]
        result.raw_responses = [mock_response]

        flows = get_flows(result)

        assert len(flows) == 1
        # Flow class uppercases the name
        assert flows[0].name == "TRANSFER_TO_STAFF_TEXT"

    def test_get_flows_with_multiple_calls(self):
        """Test _get_flows with multiple matching function calls."""
        result = MagicMock()
        mock_response = MagicMock()

        tool_call1 = MagicMock()
        tool_call1.type = "function_call"
        tool_call1.name = "packages_thinker_tool"

        tool_call2 = MagicMock()
        tool_call2.type = "function_call"
        tool_call2.name = "transfer_to_staff_text"

        mock_response.output = [tool_call1, tool_call2]
        result.raw_responses = [mock_response]

        flows = get_flows(result)

        assert len(flows) == 2
        # Flow class transforms names
        assert flows[0].name == "PACKAGES_FLOW"
        assert flows[1].name == "TRANSFER_TO_STAFF_TEXT"

    def test_get_flows_ignores_other_function_calls(self):
        """Test _get_flows ignores function calls not matching the pattern."""
        result = MagicMock()
        mock_response = MagicMock()

        tool_call1 = MagicMock()
        tool_call1.type = "function_call"
        tool_call1.name = "create_service_request"

        tool_call2 = MagicMock()
        tool_call2.type = "function_call"
        tool_call2.name = "packages_thinker_tool"

        mock_response.output = [tool_call1, tool_call2]
        result.raw_responses = [mock_response]

        flows = get_flows(result)

        assert len(flows) == 1
        assert flows[0].name == "PACKAGES_FLOW"

    def test_get_flows_with_no_raw_responses(self):
        """Test _get_flows handles missing raw_responses gracefully."""
        result = MagicMock()
        result.raw_responses = []

        flows = get_flows(result)

        assert flows == []

    def test_get_flows_with_exception(self):
        """Test _get_flows handles exceptions gracefully."""
        result = MagicMock()
        result.raw_responses = [None]

        flows = get_flows(result)

        assert flows == []

    def test_get_flows_with_empty_output(self):
        """Test _get_flows with empty output list."""
        result = MagicMock()
        mock_response = MagicMock()
        mock_response.output = []
        result.raw_responses = [mock_response]

        flows = get_flows(result)

        assert flows == []


class TestSavePreviousResponseId:
    """Tests for save_previous_response_id function."""

    @pytest.mark.asyncio
    async def test_save_previous_response_id_with_valid_id(self, ask_request_resident_chat_ll):
        """Test save_previous_response_id with a valid previous_response_id."""
        headers = {}
        context = SessionScope(ask_request=ask_request_resident_chat_ll)
        previous_response_id = "test_response_id_123"

        await save_previous_response_id(headers, context, previous_response_id)

        # Check that headers were updated with the previous response ID
        assert "X-OpenAI-Previous-Response-Id" in headers
        assert headers["X-OpenAI-Previous-Response-Id"] == previous_response_id

        # Check that context was updated with the previous response ID
        assert context.previous_response_id == previous_response_id

    @pytest.mark.asyncio
    async def test_save_previous_response_id_with_none(self, ask_request_resident_chat_ll):
        """Test save_previous_response_id when previous_response_id is None."""
        headers = {"existing_header": "existing_value"}
        context = SessionScope(ask_request=ask_request_resident_chat_ll)
        context.previous_response_id = "old_response_id"

        await save_previous_response_id(headers, context, None)

        # Check that headers were not modified
        assert "X-OpenAI-Previous-Response-Id" not in headers
        assert headers == {"existing_header": "existing_value"}

        # Check that context previous_response_id was not changed
        assert context.previous_response_id == "old_response_id"

    @pytest.mark.asyncio
    async def test_save_previous_response_id_with_empty_string(self, ask_request_resident_chat_ll):
        """Test save_previous_response_id when previous_response_id is empty string."""
        headers = {}
        context = SessionScope(ask_request=ask_request_resident_chat_ll)

        await save_previous_response_id(headers, context, "")

        # Check that headers were not modified (empty string is falsy)
        assert "X-OpenAI-Previous-Response-Id" not in headers

        # Check that context previous_response_id was not changed
        assert context.previous_response_id is None

    @pytest.mark.asyncio
    async def test_save_previous_response_id_updates_existing_headers(self, ask_request_resident_chat_ll):
        """Test save_previous_response_id updates existing headers correctly."""
        headers = {
            "Content-Type": "application/json",
            "X-Custom-Header": "custom_value",
        }
        context = SessionScope(ask_request=ask_request_resident_chat_ll)
        previous_response_id = "new_response_id_456"

        await save_previous_response_id(headers, context, previous_response_id)

        # Check that existing headers are preserved
        assert headers["Content-Type"] == "application/json"
        assert headers["X-Custom-Header"] == "custom_value"

        # Check that new header was added
        assert headers["X-OpenAI-Previous-Response-Id"] == previous_response_id

        # Check that context was updated
        assert context.previous_response_id == previous_response_id

    @pytest.mark.asyncio
    async def test_save_previous_response_id_overwrites_existing_header(self, ask_request_resident_chat_ll):
        """Test save_previous_response_id overwrites existing previous response ID header."""
        headers = {"X-OpenAI-Previous-Response-Id": "old_response_id"}
        context = SessionScope(ask_request=ask_request_resident_chat_ll)
        context.previous_response_id = "old_context_id"
        new_response_id = "new_response_id_789"

        await save_previous_response_id(headers, context, new_response_id)

        # Check that header was overwritten
        assert headers["X-OpenAI-Previous-Response-Id"] == new_response_id

        # Check that context was overwritten
        assert context.previous_response_id == new_response_id


class TestGetDisabledToolsFromDisabledModules:
    """Tests for get_disabled_tools_from_disabled_modules helper."""

    def test_none_disabled_modules(self):
        module_mapping = {
            "PAYMENT_CENTER": ["get_lease_term_information", "get_rent_information"],
            "PARKING_PASS": ["issue_guest_parking_pass"],
        }

        result = get_disabled_tools_from_disabled_modules(module_mapping, None)

        assert result == []

    def test_empty_disabled_modules(self):
        module_mapping = {
            "PAYMENT_CENTER": ["get_lease_term_information", "get_rent_information"],
            "PARKING_PASS": ["issue_guest_parking_pass"],
        }
        disabled_modules: list[str] = []

        result = get_disabled_tools_from_disabled_modules(module_mapping, disabled_modules)

        assert result == []

    def test_multiple_disabled_modules(self):
        module_mapping = {
            "PAYMENT_CENTER": ["get_lease_term_information", "get_rent_information"],
            "PARKING_PASS": ["issue_guest_parking_pass"],
            "PACKAGES": ["get_residents_packages"],
        }
        disabled_modules = ["PARKING_PASS", "PACKAGES"]

        result = get_disabled_tools_from_disabled_modules(module_mapping, disabled_modules)

        assert result == ["issue_guest_parking_pass", "get_residents_packages"]

    @pytest.mark.parametrize(
        "disabled_modules,expected_count",
        [
            ([], 0),
            (["PAYMENT_CENTER"], 2),
            (["PARKING_PASS"], 1),
            (["PACKAGES"], 1),
            (["PAYMENT_CENTER", "PACKAGES"], 3),
            (["PAYMENT_CENTER", "PARKING_PASS", "PACKAGES"], 4),
        ],
    )
    def test_various_disabled_modules_combinations(self, disabled_modules, expected_count):
        module_mapping = {
            "PAYMENT_CENTER": ["get_lease_term_information", "get_rent_information"],
            "PARKING_PASS": ["issue_guest_parking_pass"],
            "PACKAGES": ["get_residents_packages"],
        }

        result = get_disabled_tools_from_disabled_modules(module_mapping, disabled_modules)

        assert len(result) == expected_count


class TestBuildAgentRequestInputSanitization:
    """Tests for input sanitization in build_agent_request."""

    @pytest.mark.asyncio
    async def test_build_agent_request_sanitizes_urls_in_prompt(self, ask_request_resident_chat_ll):
        """Test that URLs in the prompt are sanitized before processing."""
        # Set prompt with URL
        ask_request_resident_chat_ll.prompt = "Check out https://sketchy-site.com for details"

        with (
            patch(
                "agent_leasing.services.agent_service.memory.get_context", new_callable=AsyncMock
            ) as mock_get_context,
            patch("agent_leasing.services.agent_service.agent_selector") as mock_agent_selector,
        ):
            mock_get_context.return_value = None
            mock_agent = MagicMock()
            mock_agent.name = "TestAgent"
            mock_agent_selector.return_value = mock_agent

            result = await build_agent_request(ask_request_resident_chat_ll)

            # Verify URL was sanitized in the request
            assert "https://sketchy-site.com" not in ask_request_resident_chat_ll.prompt
            assert "[external link removed]" in ask_request_resident_chat_ll.prompt
            assert result.context.ask_request.prompt == ask_request_resident_chat_ll.prompt

    @pytest.mark.asyncio
    async def test_build_agent_request_sanitizes_multiple_urls(self, ask_request_resident_chat_ll):
        """Test that multiple URLs are all sanitized."""
        ask_request_resident_chat_ll.prompt = "See https://site1.com and http://site2.com and www.site3.com"

        with (
            patch(
                "agent_leasing.services.agent_service.memory.get_context", new_callable=AsyncMock
            ) as mock_get_context,
            patch("agent_leasing.services.agent_service.agent_selector") as mock_agent_selector,
        ):
            mock_get_context.return_value = None
            mock_agent = MagicMock()
            mock_agent.name = "TestAgent"
            mock_agent_selector.return_value = mock_agent

            await build_agent_request(ask_request_resident_chat_ll)

            # Verify all URLs were sanitized
            assert "site1.com" not in ask_request_resident_chat_ll.prompt
            assert "site2.com" not in ask_request_resident_chat_ll.prompt
            assert "site3.com" not in ask_request_resident_chat_ll.prompt
            assert ask_request_resident_chat_ll.prompt.count("[external link removed]") == 3

    @pytest.mark.asyncio
    async def test_build_agent_request_preserves_clean_prompt(self, ask_request_resident_chat_ll):
        """Test that prompts without URLs are unchanged."""
        original_prompt = "When is my rent due? I need to make a payment."
        ask_request_resident_chat_ll.prompt = original_prompt

        with (
            patch(
                "agent_leasing.services.agent_service.memory.get_context", new_callable=AsyncMock
            ) as mock_get_context,
            patch("agent_leasing.services.agent_service.agent_selector") as mock_agent_selector,
        ):
            mock_get_context.return_value = None
            mock_agent = MagicMock()
            mock_agent.name = "TestAgent"
            mock_agent_selector.return_value = mock_agent

            await build_agent_request(ask_request_resident_chat_ll)

            # Prompt should be unchanged
            assert ask_request_resident_chat_ll.prompt == original_prompt

    @pytest.mark.asyncio
    async def test_build_agent_request_handles_empty_prompt(self, ask_request_resident_chat_ll):
        """Test that empty prompts are handled gracefully."""
        ask_request_resident_chat_ll.prompt = ""

        with (
            patch(
                "agent_leasing.services.agent_service.memory.get_context", new_callable=AsyncMock
            ) as mock_get_context,
            patch("agent_leasing.services.agent_service.agent_selector") as mock_agent_selector,
        ):
            mock_get_context.return_value = None
            mock_agent = MagicMock()
            mock_agent.name = "TestAgent"
            mock_agent_selector.return_value = mock_agent

            result = await build_agent_request(ask_request_resident_chat_ll)

            # Should complete without error
            assert result is not None
            assert ask_request_resident_chat_ll.prompt == ""

    @pytest.mark.asyncio
    async def test_build_agent_request_sanitizes_before_logging(self, ask_request_resident_chat_ll):
        """Test that sanitization happens before the prompt is logged."""
        ask_request_resident_chat_ll.prompt = "Visit https://secret-url.com/private"

        with (
            patch(
                "agent_leasing.services.agent_service.memory.get_context", new_callable=AsyncMock
            ) as mock_get_context,
            patch("agent_leasing.services.agent_service.agent_selector") as mock_agent_selector,
            patch("agent_leasing.services.agent_service.logger") as mock_logger,
        ):
            mock_get_context.return_value = None
            mock_agent = MagicMock()
            mock_agent.name = "TestAgent"
            mock_agent_selector.return_value = mock_agent

            await build_agent_request(ask_request_resident_chat_ll)

            # Verify that the logged prompt doesn't contain the URL
            # The logger.info call with the prompt should have the sanitized version
            log_calls = [str(call) for call in mock_logger.info.call_args_list]
            log_content = " ".join(log_calls)
            assert "https://secret-url.com" not in log_content


class TestEnsureConversationId:
    """Tests for ensure_conversation_id."""

    @pytest.mark.asyncio
    async def test_returns_none_when_disabled(self, ask_request_resident_chat_ll):
        """When use_conversations_api is False, returns None without calling OpenAI."""
        context = SessionScope(ask_request=ask_request_resident_chat_ll)
        with patch.object(settings, "use_conversations_api", False):
            result = await ensure_conversation_id(context)
        assert result is None
        assert context.openai_conversation_id is None

    @pytest.mark.asyncio
    async def test_creates_conversation_on_first_call(self, ask_request_resident_chat_ll):
        """Creates a conversation and stores the ID in context."""
        context = SessionScope(ask_request=ask_request_resident_chat_ll)
        mock_conversation = MagicMock()
        mock_conversation.id = "conv_abc123"

        with (
            patch.object(settings, "use_conversations_api", True),
            patch("agent_leasing.clients.openai.get_openai_client") as mock_get_client,
        ):
            mock_client = MagicMock()
            mock_client.conversations.create = AsyncMock(return_value=mock_conversation)
            mock_get_client.return_value = mock_client

            result = await ensure_conversation_id(context)

        assert result == "conv_abc123"
        assert context.openai_conversation_id == "conv_abc123"
        mock_client.conversations.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reuses_existing_conversation_id(self, ask_request_resident_chat_ll):
        """Returns existing conversation_id without creating a new one."""
        context = SessionScope(
            ask_request=ask_request_resident_chat_ll,
            openai_conversation_id="conv_existing",
        )

        with (
            patch.object(settings, "use_conversations_api", True),
            patch("agent_leasing.clients.openai.get_openai_client") as mock_get_client,
        ):
            result = await ensure_conversation_id(context)

        assert result == "conv_existing"
        mock_get_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_retries_on_transient_failure(self, ask_request_resident_chat_ll):
        """Retries on failure and succeeds on second attempt."""
        context = SessionScope(ask_request=ask_request_resident_chat_ll)
        mock_conversation = MagicMock()
        mock_conversation.id = "conv_retry_success"

        with (
            patch.object(settings, "use_conversations_api", True),
            patch("agent_leasing.clients.openai.get_openai_client") as mock_get_client,
            patch("agent_leasing.services.agent_service.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_client = MagicMock()
            mock_client.conversations.create = AsyncMock(side_effect=[Exception("transient"), mock_conversation])
            mock_get_client.return_value = mock_client

            result = await ensure_conversation_id(context)

        assert result == "conv_retry_success"
        assert context.openai_conversation_id == "conv_retry_success"
        assert mock_client.conversations.create.await_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_all_retries_exhausted(self, ask_request_resident_chat_ll):
        """Raises the last exception after all retries fail."""
        context = SessionScope(ask_request=ask_request_resident_chat_ll)

        with (
            patch.object(settings, "use_conversations_api", True),
            patch("agent_leasing.clients.openai.get_openai_client") as mock_get_client,
            patch("agent_leasing.services.agent_service.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_client = MagicMock()
            mock_client.conversations.create = AsyncMock(side_effect=Exception("persistent failure"))
            mock_get_client.return_value = mock_client

            with pytest.raises(Exception, match="persistent failure"):
                await ensure_conversation_id(context)

        assert context.openai_conversation_id is None


class TestSaveConversationId:
    """Tests for save_conversation_id."""

    def test_sets_header_when_conversation_id_present(self, ask_request_resident_chat_ll):
        headers = {}
        context = SessionScope(
            ask_request=ask_request_resident_chat_ll,
            openai_conversation_id="conv_123",
        )
        save_conversation_id(headers, context)
        assert headers[CONVERSATION_ID_HEADER] == "conv_123"

    def test_no_header_when_conversation_id_absent(self, ask_request_resident_chat_ll):
        headers = {}
        context = SessionScope(ask_request=ask_request_resident_chat_ll)
        save_conversation_id(headers, context)
        assert CONVERSATION_ID_HEADER not in headers


class TestBuildAgentRequestConversationId:
    """Tests for conversation creation in build_agent_request."""

    @pytest.mark.asyncio
    async def test_no_background_task_when_disabled(self, ask_request_resident_chat_ll):
        """No conversation creation task is started when use_conversations_api is False."""
        with (
            patch.object(settings, "use_conversations_api", False),
            patch(
                "agent_leasing.services.agent_service.memory.get_context",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("agent_leasing.services.agent_service.agent_selector") as mock_selector,
        ):
            mock_agent = MagicMock()
            mock_agent.name = "TestAgent"
            mock_selector.return_value = mock_agent

            result = await build_agent_request(ask_request_resident_chat_ll)

        assert not hasattr(result.context, "_conversation_creation_task")
        assert result.context.openai_conversation_id is None

    @pytest.mark.asyncio
    async def test_background_task_started_when_enabled(self, ask_request_resident_chat_ll):
        """A background conversation creation task is started when use_conversations_api is True."""
        mock_conversation = MagicMock()
        mock_conversation.id = "conv_new"

        with (
            patch.object(settings, "use_conversations_api", True),
            patch(
                "agent_leasing.services.agent_service.memory.get_context",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("agent_leasing.services.agent_service.agent_selector") as mock_selector,
            patch("agent_leasing.clients.openai.get_openai_client") as mock_get_client,
        ):
            mock_client = MagicMock()
            mock_client.conversations.create = AsyncMock(return_value=mock_conversation)
            mock_get_client.return_value = mock_client
            mock_agent = MagicMock()
            mock_agent.name = "TestAgent"
            mock_selector.return_value = mock_agent

            result = await build_agent_request(ask_request_resident_chat_ll)

            # Background task was started
            assert hasattr(result.context, "_conversation_creation_task")

            # Await it to verify it resolves correctly
            conv_id = await ensure_conversation_id(result.context)
            assert conv_id == "conv_new"
            assert result.context.openai_conversation_id == "conv_new"


class TestIsOrphanFunctionCallError:
    """Issue #1569: detect the OpenAI 400 produced by an orphan function_call
    in a server-side conversation."""

    def _make_bad_request(self, *, status: int = 400, err_type: str = "invalid_request_error", message: str):
        from openai import BadRequestError

        response = MagicMock(status_code=status, headers={})
        # APIError.__init__ reads type/param/code from body's top-level keys (not nested
        # under "error"). Match that shape so the exception's .type attribute is set.
        return BadRequestError(
            message=message,
            response=response,
            body={"message": message, "type": err_type, "param": "input", "code": None},
        )

    def test_matches_orphan_400(self):
        from agent_leasing.services.agent_service import is_orphan_function_call_error

        exc = self._make_bad_request(message="No tool output found for function call call_abc123.")
        assert is_orphan_function_call_error(exc) is True

    def test_rejects_other_400(self):
        from agent_leasing.services.agent_service import is_orphan_function_call_error

        exc = self._make_bad_request(message="Invalid 'input': missing required field.")
        assert is_orphan_function_call_error(exc) is False

    def test_rejects_non_bad_request(self):
        from agent_leasing.services.agent_service import is_orphan_function_call_error

        assert is_orphan_function_call_error(ValueError("nope")) is False
        assert is_orphan_function_call_error(Exception("No tool output found for function call call_x")) is False


class TestRunAgentWithOrphanRecovery:
    """Issue #1569 Layer 2: when OpenAI 400s on a corrupted conversation,
    reset and retry once with a fresh conversation."""

    @pytest.mark.asyncio
    async def test_passes_through_when_no_error(self, mock_context):
        """Normal path: no error → no retry, single Runner.run call."""
        from agent_leasing.services.agent_service import run_agent_with_orphan_recovery

        run_result = MagicMock(name="run_result")
        with patch("agents.Runner.run", new_callable=AsyncMock, return_value=run_result) as mock_run:
            result = await run_agent_with_orphan_recovery(
                MagicMock(),
                input="hi",
                context=mock_context,
                previous_response_id=None,
                conversation_id="conv_existing",
            )
        assert result is run_result
        assert mock_run.await_count == 1
        call_kwargs = mock_run.await_args.kwargs
        assert call_kwargs["conversation_id"] == "conv_existing"
        assert call_kwargs["previous_response_id"] is None  # because conversation_id is set

    @pytest.mark.asyncio
    async def test_other_bad_request_propagates(self, mock_context):
        """A 400 that's NOT the orphan error must NOT trigger recovery."""
        from openai import BadRequestError

        from agent_leasing.services.agent_service import run_agent_with_orphan_recovery

        response = MagicMock(status_code=400, headers={})
        unrelated = BadRequestError(
            message="Some other validation error.",
            response=response,
            body={"message": "Some other validation error.", "type": "invalid_request_error"},
        )
        with (
            patch("agents.Runner.run", new_callable=AsyncMock, side_effect=unrelated) as mock_run,
            pytest.raises(BadRequestError),
        ):
            await run_agent_with_orphan_recovery(
                MagicMock(),
                input="hi",
                context=mock_context,
                previous_response_id=None,
                conversation_id="conv_existing",
            )
        # No retry attempted
        assert mock_run.await_count == 1

    @pytest.mark.asyncio
    async def test_orphan_error_recovers_with_fresh_conversation(self, mock_context):
        """Orphan 400 → reset conversation_id, create new, retry, return retry's result."""
        from openai import BadRequestError

        from agent_leasing.services.agent_service import run_agent_with_orphan_recovery

        response = MagicMock(status_code=400, headers={})
        orphan_exc = BadRequestError(
            message="No tool output found for function call call_abc.",
            response=response,
            body={
                "message": "No tool output found for function call call_abc.",
                "type": "invalid_request_error",
                "param": "input",
            },
        )

        retry_result = MagicMock(name="retry_result")
        mock_context.openai_conversation_id = "conv_corrupt"
        mock_context.previous_response_id = "resp_in_corrupt_conv"

        # First call raises, second call succeeds
        with (
            patch("agents.Runner.run", new_callable=AsyncMock, side_effect=[orphan_exc, retry_result]) as mock_run,
            patch(
                "agent_leasing.services.agent_service._create_conversation",
                new_callable=AsyncMock,
                return_value="conv_fresh",
            ) as mock_create,
        ):
            metadata = {"openai-conversation-id": "conv_corrupt"}
            result = await run_agent_with_orphan_recovery(
                MagicMock(),
                input="hi",
                context=mock_context,
                previous_response_id=None,
                conversation_id="conv_corrupt",
                openai_metadata=metadata,
            )

        assert result is retry_result
        assert mock_run.await_count == 2
        assert mock_create.await_count == 1
        # Retry used the fresh conversation_id, no previous_response_id chaining
        retry_kwargs = mock_run.await_args_list[1].kwargs
        assert retry_kwargs["conversation_id"] == "conv_fresh"
        assert retry_kwargs["previous_response_id"] is None
        # Metadata was updated
        assert metadata["openai-conversation-id"] == "conv_fresh"
        # Both context fields were cleared so other code paths (e.g. facilities API
        # tool headers) don't forward stale ids alongside the fresh conversation.
        assert mock_context.previous_response_id is None


class TestResetAndCreateFreshConversation:
    """Issue #1569 Codex P2: streaming retry must clear BOTH conversation_id and
    previous_response_id, otherwise other code paths read the stale response id
    from context and forward it alongside the new conversation."""

    @pytest.mark.asyncio
    async def test_clears_both_context_fields(self, mock_context):
        from agent_leasing.services.agent_service import reset_and_create_fresh_conversation

        mock_context.openai_conversation_id = "conv_corrupt"
        mock_context.previous_response_id = "resp_in_corrupt_conv"

        with patch(
            "agent_leasing.services.agent_service._create_conversation",
            new_callable=AsyncMock,
            return_value="conv_fresh",
        ):
            new_id = await reset_and_create_fresh_conversation(mock_context)

        assert new_id == "conv_fresh"
        assert mock_context.previous_response_id is None
        # _create_conversation sets openai_conversation_id on the context to the
        # fresh id; we only require it isn't the corrupted one.
        assert mock_context.openai_conversation_id != "conv_corrupt"
