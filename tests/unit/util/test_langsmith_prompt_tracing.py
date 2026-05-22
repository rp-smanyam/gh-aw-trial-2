"""Tests for LangSmith prompt tracing via log_prompt_to_langsmith() and log_prompt_to_langsmith_child()."""

from unittest.mock import MagicMock, patch

from agent_leasing.util.tracing_utils import (
    MAX_LANGSMITH_PROMPT_BYTES,
    log_prompt_to_langsmith,
    log_prompt_to_langsmith_child,
)


def _make_instructions_context_variables(**overrides) -> dict:
    """Build a default INSTRUCTIONS.md context_variables dict for tests."""
    base = {
        "channel": "CHAT",
        "current_time": "2025-06-25T11:00:00",
        "language_code": "en",
        "previous_response_id": None,
        "disabled_modules": [],
        "disabled_tools": [],
        "available_services": ["Billing", "Service Requests"],
        "pte_setting": True,
        "facilities_thinker_api_enabled": False,
        "onesite_new_rent_format": True,
        "property_name": "Altamonte Apartments",
        "uc_first_name": "Jane",
        "uc_last_name": "Doe",
        "identity_verified": False,
        "identity_verified_with_birth_year": False,
        "knock_resident_id": "kr-123",
        "ab_resident_id": None,
        "uc_company_id": None,
        "uc_property_id": None,
        "uc_community_id": None,
        "uc_resident_household_id": None,
        "uc_resident_member_id": None,
        "property_data": "**Altamonte Apartments**\nAddress: 123 Main St",
        "resident_data": None,
        "packages": None,
        "service_requests": None,
        "signed_up_community_events": None,
        "emergency_service_product": None,
        "callback_number": None,
    }
    base.update(overrides)
    return base


class TestLogPromptToLangsmith:
    """Tests for the log_prompt_to_langsmith utility (chat/SMS/email path)."""

    @patch("agent_leasing.util.tracing_utils.ls.trace")
    @patch("agent_leasing.util.tracing_utils.is_langsmith_enabled", return_value=True)
    def test_creates_prompt_span_with_parent(self, mock_enabled, mock_trace):
        """Creates a ChatPromptTemplate span nested under the given parent."""
        mock_run = MagicMock()
        mock_trace.return_value.__enter__ = MagicMock(return_value=mock_run)
        mock_trace.return_value.__exit__ = MagicMock(return_value=False)

        parent_headers = {"langsmith-trace": "some-trace-id"}
        ctx_vars = _make_instructions_context_variables()

        log_prompt_to_langsmith(
            prompt_name="INSTRUCTIONS.md",
            rendered_prompt="You are an assistant.",
            context_variables=ctx_vars,
            parent=parent_headers,
        )

        mock_trace.assert_called_once_with(
            name="ChatPromptTemplate",
            run_type="prompt",
            inputs={"template_name": "INSTRUCTIONS.md", "context_variables": ctx_vars},
            parent=parent_headers,
        )
        mock_run.end.assert_called_once_with(outputs={"rendered_prompt": "You are an assistant."})

    @patch("agent_leasing.util.tracing_utils.ls.trace")
    @patch("agent_leasing.util.tracing_utils.is_langsmith_enabled", return_value=True)
    def test_creates_span_without_parent(self, mock_enabled, mock_trace):
        """Creates a span with parent=None when no parent is provided."""
        mock_run = MagicMock()
        mock_trace.return_value.__enter__ = MagicMock(return_value=mock_run)
        mock_trace.return_value.__exit__ = MagicMock(return_value=False)

        log_prompt_to_langsmith(
            prompt_name="INSTRUCTIONS.md",
            rendered_prompt="You are an assistant.",
            context_variables={"channel": "CHAT"},
        )

        call_kwargs = mock_trace.call_args[1]
        assert call_kwargs["parent"] is None

    @patch("agent_leasing.util.tracing_utils.ls.trace")
    @patch("agent_leasing.util.tracing_utils.is_langsmith_enabled", return_value=False)
    def test_noop_when_disabled(self, mock_enabled, mock_trace):
        """Does nothing when LangSmith tracing is disabled."""
        log_prompt_to_langsmith(
            prompt_name="INSTRUCTIONS.md",
            rendered_prompt="You are an assistant.",
            context_variables={"channel": "CHAT"},
        )

        mock_trace.assert_not_called()

    @patch("agent_leasing.util.tracing_utils.ls.trace")
    @patch("agent_leasing.util.tracing_utils.is_langsmith_enabled", return_value=True)
    def test_truncates_oversized_prompt(self, mock_enabled, mock_trace):
        """Truncates prompts exceeding MAX_LANGSMITH_PROMPT_BYTES with a marker."""
        mock_run = MagicMock()
        mock_trace.return_value.__enter__ = MagicMock(return_value=mock_run)
        mock_trace.return_value.__exit__ = MagicMock(return_value=False)

        oversized_prompt = "x" * (MAX_LANGSMITH_PROMPT_BYTES + 1000)

        log_prompt_to_langsmith(
            prompt_name="INSTRUCTIONS.md",
            rendered_prompt=oversized_prompt,
            context_variables={},
        )

        call_kwargs = mock_run.end.call_args[1]
        logged_prompt = call_kwargs["outputs"]["rendered_prompt"]
        assert logged_prompt.endswith("\n\n[truncated]")
        # The total logged prompt (including marker) must stay within the byte limit
        assert len(logged_prompt.encode("utf-8")) <= MAX_LANGSMITH_PROMPT_BYTES

    @patch("agent_leasing.util.tracing_utils.ls.trace")
    @patch("agent_leasing.util.tracing_utils.is_langsmith_enabled", return_value=True)
    def test_does_not_truncate_within_limit(self, mock_enabled, mock_trace):
        """Does not truncate prompts within the size limit."""
        mock_run = MagicMock()
        mock_trace.return_value.__enter__ = MagicMock(return_value=mock_run)
        mock_trace.return_value.__exit__ = MagicMock(return_value=False)

        normal_prompt = "You are an assistant for Altamonte Apartments." * 100

        log_prompt_to_langsmith(
            prompt_name="INSTRUCTIONS.md",
            rendered_prompt=normal_prompt,
            context_variables={},
        )

        call_kwargs = mock_run.end.call_args[1]
        assert call_kwargs["outputs"]["rendered_prompt"] == normal_prompt

    @patch("agent_leasing.util.tracing_utils.ls.trace", side_effect=Exception("LangSmith unavailable"))
    @patch("agent_leasing.util.tracing_utils.is_langsmith_enabled", return_value=True)
    def test_handles_exceptions_gracefully(self, mock_enabled, mock_trace):
        """Never raises — exceptions are caught and logged."""
        # Should not raise
        log_prompt_to_langsmith(
            prompt_name="INSTRUCTIONS.md",
            rendered_prompt="test",
            context_variables={},
        )

    @patch("agent_leasing.util.tracing_utils.ls.trace")
    @patch("agent_leasing.util.tracing_utils.is_langsmith_enabled", return_value=True)
    def test_context_variables_include_comprehensive_fields(self, mock_enabled, mock_trace):
        """Input captures comprehensive context data for debugging."""
        mock_run = MagicMock()
        mock_trace.return_value.__enter__ = MagicMock(return_value=mock_run)
        mock_trace.return_value.__exit__ = MagicMock(return_value=False)

        context_variables = _make_instructions_context_variables(
            channel="SMS",
            language_code="es",
            previous_response_id="resp_abc123",
            disabled_modules=["PACKAGES"],
            available_services=["Billing"],
            pte_setting=False,
            facilities_thinker_api_enabled=True,
            onesite_new_rent_format=False,
            property_name="Sunset Apartments",
            uc_first_name="John",
            uc_last_name="Doe",
            identity_verified=True,
            knock_resident_id="kr-456",
            property_data="**Sunset Apartments**\nPet-friendly community",
            resident_data="John Doe, Unit 101",
            packages=[{"id": 1, "status": "pending"}],
            emergency_service_product="Advanced Emergency",
            callback_number="+15551234567",
        )

        log_prompt_to_langsmith(
            prompt_name="INSTRUCTIONS.md",
            rendered_prompt="You are an assistant for Sunset Apartments.",
            context_variables=context_variables,
            parent={"langsmith-trace": "chat-trace-id"},
        )

        call_kwargs = mock_trace.call_args[1]
        logged_vars = call_kwargs["inputs"]["context_variables"]
        assert logged_vars["property_name"] == "Sunset Apartments"
        assert logged_vars["uc_first_name"] == "John"
        assert logged_vars["uc_last_name"] == "Doe"
        assert logged_vars["property_data"] == "**Sunset Apartments**\nPet-friendly community"
        assert logged_vars["identity_verified"] is True
        assert logged_vars["language_code"] == "es"
        assert logged_vars["resident_data"] == "John Doe, Unit 101"
        assert logged_vars["packages"] == [{"id": 1, "status": "pending"}]
        assert logged_vars["emergency_service_product"] == "Advanced Emergency"
        assert logged_vars["callback_number"] == "+15551234567"


class TestLogPromptToLangsmithChild:
    """Tests for the log_prompt_to_langsmith_child utility (voice path)."""

    @patch("agent_leasing.util.tracing_utils.is_langsmith_enabled", return_value=True)
    def test_creates_child_run_under_parent(self, mock_enabled):
        """Creates a ChatPromptTemplate child run using create_child() + post()."""
        mock_parent = MagicMock()
        mock_child = MagicMock()
        mock_parent.create_child.return_value = mock_child

        log_prompt_to_langsmith_child(
            parent_run=mock_parent,
            prompt_name="VOICE_RESPONDER.md",
            rendered_prompt="You are a voice assistant.",
            context_variables={"channel": "VOICE", "disabled_modules": ["PACKAGES"]},
        )

        mock_parent.create_child.assert_called_once_with(
            name="ChatPromptTemplate",
            run_type="prompt",
            inputs={
                "template_name": "VOICE_RESPONDER.md",
                "context_variables": {"channel": "VOICE", "disabled_modules": ["PACKAGES"]},
            },
            outputs={"rendered_prompt": "You are a voice assistant."},
        )
        mock_child.post.assert_called_once()

    @patch("agent_leasing.util.tracing_utils.is_langsmith_enabled", return_value=True)
    def test_truncates_oversized_prompt_in_child(self, mock_enabled):
        """Truncates the prompt in child runs the same way as the chat path."""
        mock_parent = MagicMock()
        mock_child = MagicMock()
        mock_parent.create_child.return_value = mock_child

        oversized_prompt = "x" * (MAX_LANGSMITH_PROMPT_BYTES + 1000)

        log_prompt_to_langsmith_child(
            parent_run=mock_parent,
            prompt_name="VOICE_RESPONDER.md",
            rendered_prompt=oversized_prompt,
            context_variables={},
        )

        call_kwargs = mock_parent.create_child.call_args[1]
        logged_prompt = call_kwargs["outputs"]["rendered_prompt"]
        assert logged_prompt.endswith("\n\n[truncated]")
        # The total logged prompt (including marker) must stay within the byte limit
        assert len(logged_prompt.encode("utf-8")) <= MAX_LANGSMITH_PROMPT_BYTES
        mock_child.post.assert_called_once()

    @patch("agent_leasing.util.tracing_utils.is_langsmith_enabled", return_value=False)
    def test_noop_when_disabled(self, mock_enabled):
        """Does nothing when LangSmith tracing is disabled."""
        mock_parent = MagicMock()

        log_prompt_to_langsmith_child(
            parent_run=mock_parent,
            prompt_name="VOICE_RESPONDER.md",
            rendered_prompt="test",
            context_variables={},
        )

        mock_parent.create_child.assert_not_called()

    @patch("agent_leasing.util.tracing_utils.is_langsmith_enabled", return_value=True)
    def test_handles_exceptions_gracefully(self, mock_enabled):
        """Never raises — exceptions are caught and logged."""
        mock_parent = MagicMock()
        mock_parent.create_child.side_effect = Exception("LangSmith unavailable")

        # Should not raise
        log_prompt_to_langsmith_child(
            parent_run=mock_parent,
            prompt_name="VOICE_RESPONDER.md",
            rendered_prompt="test",
            context_variables={},
        )
