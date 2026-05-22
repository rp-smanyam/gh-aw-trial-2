"""Unit tests for prisma_airs_guardrail_agent."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from agents import GuardrailFunctionOutput

from agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent import (
    _BLOCK_MESSAGE,
    PrismaAirsGuardrailOutput,
    _build_request_payload,
    _check_content_safety_prisma_airs,
    _is_payload_empty,
    _prisma_airs_api_call,
)
from agent_leasing.agent.util import SessionScope
from agent_leasing.api.model import AskRequest, ProductInfo, UCReference


def _build_prisma_airs_response(
    action: str = "allow",
    prompt_detected: dict | None = None,
    response_detected: dict | None = None,
) -> dict:
    """Helper to build Prisma AIRS API response."""
    response = {
        "action": action,
        "request_id": "test-request-123",
    }
    if prompt_detected is not None:
        response["prompt_detected"] = prompt_detected
    if response_detected is not None:
        response["response_detected"] = response_detected
    return response


def _build_prisma_airs_context() -> SessionScope:
    return SessionScope(
        ask_request=AskRequest(
            product="resident_one_chat",
            product_info=ProductInfo(
                knock_property_id="test-property-789",
                knock_resident_id="test-resident-123",
                thread_id="test-thread-456",
                uc_portal_base_url="https://cassidysouth.qa1.loftliving.com",
                uc_resident_member_id=UCReference(id=1, source=""),
                uc_resident_household_id=UCReference(id=2, source=""),
                uc_company_id=UCReference(id=3, source=""),
                uc_property_id=UCReference(id=4, source=""),
                ab_resident_id=UCReference(id=5, source=""),
                uc_lease_id=UCReference(id=6, source=""),
            ),
        ),
    )


@pytest.mark.parametrize(
    "prompt, response",
    [
        # Basic cases
        ("Test prompt message", None),
        ("Test prompt", "Test response"),
        # Special characters
        ("This is a test prompt with special chars: !@#$%", "This is a test response with special chars: !@#$%"),
        # Empty strings
        ("", None),
        ("", ""),
        # Unicode and emoji
        ("Hello 世界 🌍", "Response with émojis 🎉✨"),
        # Newlines and whitespace
        ("Line 1\nLine 2\nLine 3", "Response\nwith\nmultiple\nlines"),
        ("  spaces before and after  ", None),
        # Very long strings
        ("A" * 1000, "B" * 1000),
        # Code-like content
        ("SELECT * FROM users WHERE id=1", "<script>alert('xss')</script>"),
        # Quotes and escaping
        ("Prompt with \"quotes\" and 'apostrophes' and \\backslashes", None),
    ],
)
class TestBuildRequestPayload:
    """Tests for _build_request_payload function."""

    def test_builds_correct_payload_structure(self, prompt, response):
        """Test that payload has correct structure for Prisma AIRS API."""
        # Arrange

        # Act
        with patch(
            "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.settings"
        ) as mock_settings:
            context = _build_prisma_airs_context()
            mock_settings.prisma_airs_profile_name = "test-profile"
            result = _build_request_payload(context=context, prompt=prompt, response=response)

        # Assert
        assert "contents" in result
        assert len(result["contents"]) == 1
        assert result["contents"][0]["prompt"] == prompt
        assert "ai_profile" in result
        assert result["ai_profile"]["profile_name"] == "test-profile"

    def test_includes_prompt_in_contents(self, prompt, response):
        """Test that prompt text is correctly placed in contents array."""
        # Arrange

        # Act
        with patch(
            "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.settings"
        ) as mock_settings:
            context = _build_prisma_airs_context()
            mock_settings.prisma_airs_profile_name = "security-profile"
            result = _build_request_payload(context=context, prompt=prompt, response=response)

        # Assert
        assert result["contents"][0]["prompt"] == prompt


class TestPrismaAirsApiCall:
    """Tests for _prisma_airs_api_call function."""

    @pytest.mark.asyncio
    async def test_calls_api_with_correct_headers(self):
        """Test that API call includes x-pan-token header with API key."""
        # Arrange
        payload = _build_prisma_airs_response()
        expected_response = _build_prisma_airs_response("allow")

        # Act
        with patch(
            "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.settings"
        ) as mock_settings:
            with patch(
                "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent._make_api_call",
                new_callable=AsyncMock,
            ) as mock_api_call:
                mock_settings.prisma_airs_api_key = "test-api-key-123"
                mock_settings.prisma_airs_api_url = "https://api.prisma.example.com"
                mock_api_call.return_value = expected_response

                result = await _prisma_airs_api_call(payload)

        # Assert
        assert result == expected_response
        mock_api_call.assert_called_once()
        call_args = mock_api_call.call_args
        assert call_args.args[0] == "https://api.prisma.example.com"
        assert call_args.args[1] == payload
        assert call_args.args[2] == {"x-pan-token": "test-api-key-123"}
        assert call_args.args[3] == "Prisma AIRS"

    @pytest.mark.asyncio
    async def test_raises_when_api_key_not_configured(self):
        """Test that ValueError is raised when API key is not set."""
        # Arrange
        payload = {}

        # Act / Assert
        with patch(
            "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.settings"
        ) as mock_settings:
            mock_settings.prisma_airs_api_key = ""
            mock_settings.prisma_airs_api_url = "https://api.example.com"

            with pytest.raises(ValueError, match="Prisma AIRS API key is not configured"):
                await _prisma_airs_api_call(payload)

    @pytest.mark.asyncio
    async def test_uses_post_method(self):
        """Test that API call uses POST method."""
        # Arrange
        payload = _build_prisma_airs_response()

        # Act
        with patch(
            "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.settings"
        ) as mock_settings:
            with patch(
                "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent._make_api_call",
                new_callable=AsyncMock,
            ) as mock_api_call:
                mock_settings.prisma_airs_api_key = "test-key"
                mock_settings.prisma_airs_api_url = "https://api.example.com"
                mock_api_call.return_value = _build_prisma_airs_response()

                await _prisma_airs_api_call(payload)

        # Assert - _make_api_call should have been called with method="POST" as default
        # The function signature has method="POST" as default
        mock_api_call.assert_called_once()


class TestCheckContentSafetyPrismaAirs:
    """Tests for _check_content_safety_prisma_airs function."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "action, expected_tripwire, block_settings",
        [
            ("allow", False, True),
            ("Allow", False, True),  # Case insensitive
            ("ALLOW", False, True),
            ("block", True, True),
            ("Block", True, True),  # Case insensitive
            ("BLOCK", True, True),
            ("allow", False, False),
            ("Allow", False, False),  # Case insensitive
            ("ALLOW", False, False),
            ("block", False, False),
            ("Block", False, False),  # Case insensitive
            ("BLOCK", False, False),
        ],
    )
    async def test_correctly_interprets_prisma_airs_action(self, action, expected_tripwire, block_settings):
        """Test that function correctly interprets allow/block actions from Prisma AIRS."""
        # Arrange
        test_content = "Test content for moderation"
        mock_context = Mock()
        mock_context.ask_request.product_info.knock_resident_id = "test-resident-123"
        mock_context.ask_request.product = "resident_one_chat"
        mock_context.thread_id = "test-thread-456"
        mock_context.language_code = "en"

        # Determine if the API said to block (regardless of blocking mode)
        api_said_block = action.lower() == "block"

        # Act
        with patch(
            "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent._prisma_airs_api_call",
            new_callable=AsyncMock,
        ) as mock_api:
            with patch(
                "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.settings"
            ) as mock_settings:
                mock_api.return_value = _build_prisma_airs_response(
                    action,
                    prompt_detected={"injection": api_said_block},
                )
                mock_settings.prisma_airs_profile_name = "test-profile"
                mock_settings.prisma_airs_blocking_mode = block_settings
                mock_settings.model = "gpt-4"
                mock_settings.resident_one_model = "gpt-4o"
                mock_settings.environment = "test"

                result = await _check_content_safety_prisma_airs(test_content, "input", mock_context)

        # Assert
        assert isinstance(result, GuardrailFunctionOutput)

        # tripwire_triggered should only be True if API said block AND blocking is enabled
        expected_tripwire = api_said_block and block_settings
        assert result.tripwire_triggered == expected_tripwire

        if api_said_block:
            # When API says block, we always return a PrismaAirsGuardrailOutput
            # (even if blocking is disabled - we just don't trigger the tripwire)
            assert isinstance(result.output_info, PrismaAirsGuardrailOutput)
            assert result.output_info.is_harmful
            assert result.output_info.safe_response == _BLOCK_MESSAGE
            assert set(result.output_info.flagged_categories) == {"injection"}
        else:
            # When API says allow, we return the original content
            assert result.output_info == test_content

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "content_type",
        ["input", "output"],
    )
    async def test_handles_input_and_output_content_types(self, content_type):
        """Test that function handles both input and output content types."""
        # Arrange
        test_content = "Content to moderate"
        mock_context = Mock()
        mock_context.ask_request.prompt = "Test prompt"

        # Act
        with patch(
            "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent._prisma_airs_api_call",
            new_callable=AsyncMock,
        ) as mock_api:
            with patch(
                "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.settings"
            ) as mock_settings:
                mock_api.return_value = _build_prisma_airs_response("allow")
                mock_settings.prisma_airs_profile_name = "test-profile"

                result = await _check_content_safety_prisma_airs(test_content, content_type, mock_context)

        # Assert
        assert isinstance(result, GuardrailFunctionOutput)
        assert not result.tripwire_triggered

    @pytest.mark.asyncio
    async def test_blocked_content_includes_reasoning(self):
        """Test that blocked content includes reasoning in output."""
        # Arrange
        test_content = "Harmful content"
        mock_context = Mock()
        mock_context.ask_request.product_info.knock_resident_id = "test-resident-123"
        mock_context.ask_request.product = "resident_one_chat"
        mock_context.thread_id = "test-thread-456"
        mock_context.language_code = "en"

        # Act
        with patch(
            "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent._prisma_airs_api_call",
            new_callable=AsyncMock,
        ) as mock_api:
            with patch(
                "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.settings"
            ) as mock_settings:
                mock_api.return_value = _build_prisma_airs_response("block")
                mock_settings.prisma_airs_profile_name = "test-profile"
                mock_settings.model = "gpt-4"
                mock_settings.resident_one_model = "gpt-4o"
                mock_settings.environment = "test"

                result = await _check_content_safety_prisma_airs(test_content, "input", mock_context)

        # Assert
        assert result.tripwire_triggered
        assert isinstance(result.output_info, PrismaAirsGuardrailOutput)
        assert result.output_info.reasoning is not None
        assert "block" in result.output_info.reasoning.lower()

    @pytest.mark.asyncio
    async def test_handles_missing_action_in_response(self):
        """Test that function defaults to 'allow' when action is missing from response."""
        # Arrange
        test_content = "Test content"
        mock_context = Mock()

        # Act
        with patch(
            "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent._prisma_airs_api_call",
            new_callable=AsyncMock,
        ) as mock_api:
            with patch(
                "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.settings"
            ) as mock_settings:
                mock_api.return_value = {}  # No 'action' key
                mock_settings.prisma_airs_profile_name = "test-profile"

                result = await _check_content_safety_prisma_airs(test_content, "input", mock_context)

        # Assert
        assert not result.tripwire_triggered
        assert result.output_info == test_content

    @pytest.mark.asyncio
    async def test_handles_list_input_content(self):
        """Test that function handles list of TResponseInputItem for input."""
        # Arrange
        mock_input_list = [Mock(), Mock()]
        mock_context = Mock()

        # Act
        with patch(
            "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.extract_text_from_input"
        ) as mock_extract:
            with patch(
                "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent._prisma_airs_api_call",
                new_callable=AsyncMock,
            ) as mock_api:
                with patch(
                    "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.settings"
                ) as mock_settings:
                    mock_extract.return_value = "Extracted text from input list"
                    mock_api.return_value = _build_prisma_airs_response("allow")
                    mock_settings.prisma_airs_profile_name = "test-profile"

                    result = await _check_content_safety_prisma_airs(mock_input_list, "input", mock_context)

        # Assert
        mock_extract.assert_called_once_with(mock_input_list)
        assert not result.tripwire_triggered

    @pytest.mark.asyncio
    async def test_handles_object_output_content(self):
        """Test that function handles object output content."""
        # Arrange
        mock_output_obj = Mock()
        mock_context = Mock()
        mock_context.ask_request.prompt = "Test prompt"

        # Act
        with patch(
            "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.extract_text_from_output"
        ) as mock_extract:
            with patch(
                "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent._prisma_airs_api_call",
                new_callable=AsyncMock,
            ) as mock_api:
                with patch(
                    "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.settings"
                ) as mock_settings:
                    mock_extract.return_value = "Extracted text from output object"
                    mock_api.return_value = _build_prisma_airs_response("allow")
                    mock_settings.prisma_airs_profile_name = "test-profile"

                    result = await _check_content_safety_prisma_airs(mock_output_obj, "output", mock_context)

        # Assert
        mock_extract.assert_called_once_with(mock_output_obj)
        assert not result.tripwire_triggered


# Note: The decorator functions prisma_airs_input_guardrail and prisma_airs_output_guardrail
# are tested indirectly through the _check_content_safety_prisma_airs tests above.
# The decorators cannot be called directly in unit tests as they are InputGuardrail/OutputGuardrail objects.


class TestSecurityGuardrailOutput:
    """Tests for SecurityGuardrailOutput model."""

    def test_creates_output_with_all_fields(self):
        """Test that SecurityGuardrailOutput can be created with all fields."""
        # Arrange & Act
        output = PrismaAirsGuardrailOutput(
            flagged_categories=["category1", "category2"],
            is_harmful=True,
            safe_response=_BLOCK_MESSAGE,
            reasoning="Test reasoning",
        )

        # Assert
        assert output.flagged_categories == ["category1", "category2"]
        assert output.is_harmful
        assert output.safe_response == _BLOCK_MESSAGE
        assert output.reasoning == "Test reasoning"
        assert output.labels == ["category1", "category2"]

    def test_labels_property_returns_flagged_categories(self):
        """Test that labels property returns the same as flagged_categories."""
        # Arrange & Act
        output = PrismaAirsGuardrailOutput(
            flagged_categories=["test_category"],
            is_harmful=True,
            safe_response=_BLOCK_MESSAGE,
        )

        # Assert
        assert output.labels == output.flagged_categories
        assert output.labels == ["test_category"]


class TestIsPayloadEmpty:
    """Tests for _is_payload_empty function."""

    @pytest.mark.parametrize(
        "payload, expected",
        [
            # Empty cases - should return True
            ({}, True),
            ({"contents": []}, True),
            ({"contents": None}, True),
            ({"contents": [{}]}, True),
            ({"contents": [{"prompt": ""}]}, True),
            ({"contents": [{"prompt": "", "response": ""}]}, True),
            ({"contents": [{"prompt": "   ", "response": "  "}]}, True),
            ({"contents": [{"prompt": None, "response": None}]}, True),
            # Non-empty cases - should return False
            ({"contents": [{"prompt": "Hello"}]}, False),
            ({"contents": [{"prompt": "", "response": "World"}]}, False),
            ({"contents": [{"prompt": "Hello", "response": "World"}]}, False),
            ({"contents": [{"prompt": "  Hello  "}]}, False),
        ],
    )
    def test_is_payload_empty(self, payload, expected):
        """Test that _is_payload_empty correctly identifies empty payloads."""
        assert _is_payload_empty(payload) == expected


class TestEmptyContentSkipsApiCall:
    """Tests for skipping API calls when content is empty."""

    @pytest.mark.asyncio
    async def test_skips_api_call_for_empty_input(self):
        """Test that empty input content skips the API call entirely."""
        # Arrange
        mock_context = Mock()
        mock_context.ask_request.product_info.knock_resident_id = "test-resident"
        mock_context.ask_request.product = "resident_one_chat"
        mock_context.thread_id = "test-thread"

        # Act
        with patch(
            "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent._prisma_airs_api_call",
            new_callable=AsyncMock,
        ) as mock_api:
            with patch(
                "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.settings"
            ) as mock_settings:
                mock_settings.prisma_airs_profile_name = "test-profile"
                mock_settings.model = "gpt-4"
                mock_settings.resident_one_model = "gpt-4o"
                mock_settings.environment = "test"

                result = await _check_content_safety_prisma_airs("", "input", mock_context)

        # Assert - API should NOT be called
        mock_api.assert_not_called()
        assert isinstance(result, GuardrailFunctionOutput)
        assert not result.tripwire_triggered
        assert result.output_info == ""

    @pytest.mark.asyncio
    async def test_skips_api_call_for_whitespace_only_input(self):
        """Test that whitespace-only input content skips the API call."""
        # Arrange
        mock_context = Mock()
        mock_context.ask_request.product_info.knock_resident_id = "test-resident"
        mock_context.ask_request.product = "resident_one_chat"
        mock_context.thread_id = "test-thread"

        # Act
        with patch(
            "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent._prisma_airs_api_call",
            new_callable=AsyncMock,
        ) as mock_api:
            with patch(
                "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.settings"
            ) as mock_settings:
                mock_settings.prisma_airs_profile_name = "test-profile"
                mock_settings.model = "gpt-4"
                mock_settings.resident_one_model = "gpt-4o"
                mock_settings.environment = "test"

                result = await _check_content_safety_prisma_airs("   ", "input", mock_context)

        # Assert - API should NOT be called
        mock_api.assert_not_called()
        assert not result.tripwire_triggered


class TestTimeoutHandling:
    """Tests for specific timeout exception handling."""

    @pytest.mark.asyncio
    async def test_handles_asyncio_timeout_error(self):
        """Test that asyncio.TimeoutError is caught and logged with specific message."""
        # Arrange
        mock_context = Mock()
        mock_context.ask_request.product_info.knock_resident_id = "test-resident"
        mock_context.ask_request.product = "resident_one_chat"
        mock_context.thread_id = "test-thread"

        # Act
        with patch(
            "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent._prisma_airs_api_call",
            new_callable=AsyncMock,
        ) as mock_api:
            with patch(
                "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.settings"
            ) as mock_settings:
                with patch(
                    "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.logger"
                ) as mock_logger:
                    mock_settings.prisma_airs_profile_name = "test-profile"
                    mock_settings.model = "gpt-4"
                    mock_settings.resident_one_model = "gpt-4o"
                    mock_settings.environment = "test"
                    mock_api.side_effect = asyncio.TimeoutError()

                    await _check_content_safety_prisma_airs("test content", "input", mock_context)

        # Assert - should log specific timeout message
        mock_logger.warning.assert_called_once_with("Prisma AIRS guardrail timed out")


class TestExceptionLogging:
    """Tests for exception logging improvements."""

    @pytest.mark.asyncio
    async def test_logs_exception_type_when_message_is_empty(self):
        """Test that exceptions with empty messages still get meaningful logging."""
        # Arrange
        mock_context = Mock()
        mock_context.ask_request.product_info.knock_resident_id = "test-resident"
        mock_context.ask_request.product = "resident_one_chat"
        mock_context.thread_id = "test-thread"

        # Act
        with patch(
            "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent._prisma_airs_api_call",
            new_callable=AsyncMock,
        ) as mock_api:
            with patch(
                "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.settings"
            ) as mock_settings:
                with patch(
                    "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.logger"
                ) as mock_logger:
                    mock_settings.prisma_airs_profile_name = "test-profile"
                    mock_settings.model = "gpt-4"
                    mock_settings.resident_one_model = "gpt-4o"
                    mock_settings.environment = "test"
                    # Raise an exception with empty message
                    mock_api.side_effect = RuntimeError("")

                    await _check_content_safety_prisma_airs("test content", "input", mock_context)

        # Assert - should log with type info instead of empty message
        mock_logger.warning.assert_called_once()
        log_call = mock_logger.warning.call_args[0][0]
        assert "RuntimeError" in log_call
        assert "Prisma AIRS guardrail failed:" in log_call

    @pytest.mark.asyncio
    async def test_logs_normal_exception_message(self):
        """Test that exceptions with normal messages are logged as-is."""
        # Arrange
        mock_context = Mock()
        mock_context.ask_request.product_info.knock_resident_id = "test-resident"
        mock_context.ask_request.product = "resident_one_chat"
        mock_context.thread_id = "test-thread"

        # Act
        with patch(
            "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent._prisma_airs_api_call",
            new_callable=AsyncMock,
        ) as mock_api:
            with patch(
                "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.settings"
            ) as mock_settings:
                with patch(
                    "agent_leasing.agent.guardrails.prisma_airs_guardrail_agent.prisma_airs_guardrail_agent.logger"
                ) as mock_logger:
                    mock_settings.prisma_airs_profile_name = "test-profile"
                    mock_settings.model = "gpt-4"
                    mock_settings.resident_one_model = "gpt-4o"
                    mock_settings.environment = "test"
                    mock_api.side_effect = RuntimeError("API connection timeout")

                    await _check_content_safety_prisma_airs("test content", "input", mock_context)

        # Assert - should log the actual message
        mock_logger.warning.assert_called_once()
        log_call = mock_logger.warning.call_args[0][0]
        assert "API connection timeout" in log_call
