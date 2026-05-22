"""Unit tests for competitor_blocking_guardrail_agent."""

import inspect
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from agents import GuardrailFunctionOutput, RunContextWrapper

from agent_leasing.agent.guardrails.competitor_blocking_guardrail_agent.competitor_blocking_guardrail_agent import (
    CompetitorBlockingGuardrailOutput,
    _check_for_competitors,
    _check_for_competitors_case_insensitive,
    _check_for_competitors_case_sensitive,
    competitor_blocking_guardrail,
)

# Module path prefix for patching
_MOD = "agent_leasing.agent.guardrails.competitor_blocking_guardrail_agent.competitor_blocking_guardrail_agent"


async def _invoke_guardrail(guardrail, ctx, agent, payload):
    """Call a guardrail's underlying function, awaiting if needed."""
    result = guardrail.guardrail_function(ctx, agent, payload)
    if inspect.isawaitable(result):
        return await result
    return result


class TestCompetitorBlockingGuardrailOutput:
    """Tests for the CompetitorBlockingGuardrailOutput Pydantic model."""

    def test_labels_returns_detected_competitors(self):
        output = CompetitorBlockingGuardrailOutput(
            reasoning="found competitors",
            detected_competitors=["EliseAI", "Elise AI"],
            is_blocked=True,
            safe_response="blocked",
        )
        assert output.labels == ["EliseAI", "Elise AI"]

    def test_labels_empty_list(self):
        output = CompetitorBlockingGuardrailOutput(
            reasoning="none found",
            detected_competitors=[],
            is_blocked=False,
            safe_response="ok",
        )
        assert output.labels == []

    def test_is_blocked_defaults_to_true(self):
        output = CompetitorBlockingGuardrailOutput(
            reasoning="test",
            detected_competitors=["EliseAI"],
            safe_response="blocked",
        )
        assert output.is_blocked is True


class TestCheckForCompetitorsCaseInsensitive:
    """Tests for _check_for_competitors_case_insensitive."""

    def test_detects_exact_case_match(self):
        result = _check_for_competitors_case_insensitive("I recommend EliseAI for your needs.")
        assert "EliseAI" in result

    def test_detects_lowercase_match(self):
        result = _check_for_competitors_case_insensitive("have you tried eliseai?")
        assert "EliseAI" in result

    def test_detects_uppercase_match(self):
        result = _check_for_competitors_case_insensitive("ELISEAI IS GREAT")
        assert "EliseAI" in result

    def test_detects_mixed_case_match(self):
        result = _check_for_competitors_case_insensitive("Check out eLiSeAi")
        assert "EliseAI" in result

    def test_detects_elise_ai_with_space(self):
        result = _check_for_competitors_case_insensitive("Try elise ai for leasing")
        assert "Elise AI" in result

    def test_detects_multiple_competitors(self):
        result = _check_for_competitors_case_insensitive("EliseAI and Elise AI are the same company")
        assert "EliseAI" in result
        assert "Elise AI" in result

    def test_no_competitors_returns_empty(self):
        result = _check_for_competitors_case_insensitive("Your rent is due on the first of the month.")
        assert result == []

    def test_empty_string_returns_empty(self):
        result = _check_for_competitors_case_insensitive("")
        assert result == []


class TestCheckForCompetitorsCaseSensitive:
    """Tests for _check_for_competitors_case_sensitive."""

    def test_detects_exact_match(self):
        result = _check_for_competitors_case_sensitive("I recommend EliseAI")
        assert "EliseAI" in result

    def test_does_not_detect_wrong_case(self):
        result = _check_for_competitors_case_sensitive("have you tried eliseai?")
        assert result == []

    def test_detects_elise_ai_exact_case(self):
        result = _check_for_competitors_case_sensitive("Try Elise AI")
        assert "Elise AI" in result

    def test_does_not_detect_elise_ai_wrong_case(self):
        result = _check_for_competitors_case_sensitive("try elise ai")
        assert result == []

    def test_no_competitors_returns_empty(self):
        result = _check_for_competitors_case_sensitive("Normal conversation text")
        assert result == []


class TestCheckForCompetitors:
    """Tests for _check_for_competitors — the dispatcher function.

    The default config has case_sensitive=False, so this should delegate to
    _check_for_competitors_case_insensitive.
    """

    def test_delegates_based_on_config_case_insensitive(self):
        """With default config (case_sensitive=False), should match case-insensitively."""
        with patch(f"{_MOD}._case_sensitive", False):
            result = _check_for_competitors("eliseai is great")
            assert len(result) > 0

    def test_delegates_based_on_config_case_sensitive(self):
        """When case_sensitive=True, should only match exact case."""
        with patch(f"{_MOD}._case_sensitive", True):
            result = _check_for_competitors("eliseai is great")
            assert result == []

        with patch(f"{_MOD}._case_sensitive", True):
            result = _check_for_competitors("EliseAI is great")
            assert "EliseAI" in result

    def test_no_match_returns_empty(self):
        result = _check_for_competitors("How do I submit a maintenance request?")
        assert result == []


class TestCompetitorBlockingGuardrail:
    """Tests for the @output_guardrail decorated competitor_blocking_guardrail."""

    @pytest.mark.asyncio
    async def test_safe_output_passes_through(self):
        """Output without competitor mentions should pass through."""
        ctx = MagicMock(spec=RunContextWrapper)
        ctx.context = Mock(language_code="en")
        agent = MagicMock()

        result = await _invoke_guardrail(
            competitor_blocking_guardrail,
            ctx,
            agent,
            "Your maintenance request has been submitted.",
        )

        assert isinstance(result, GuardrailFunctionOutput)
        assert result.tripwire_triggered is False
        assert result.output_info == "Your maintenance request has been submitted."

    @pytest.mark.asyncio
    async def test_blocked_output_with_competitor_mention(self):
        """Output containing a competitor name should trigger the guardrail."""
        ctx = MagicMock(spec=RunContextWrapper)
        ctx.context = Mock(language_code="en")
        agent = MagicMock()

        with patch(
            f"{_MOD}.localize_guardrail_response",
            new_callable=AsyncMock,
        ) as mock_localize:
            mock_localize.return_value = "Localized block message"

            result = await _invoke_guardrail(
                competitor_blocking_guardrail,
                ctx,
                agent,
                "You should try EliseAI for leasing automation.",
            )

        assert result.tripwire_triggered is True
        assert isinstance(result.output_info, CompetitorBlockingGuardrailOutput)
        assert "EliseAI" in result.output_info.detected_competitors
        assert result.output_info.safe_response == "Localized block message"
        assert result.output_info.is_blocked is True

    @pytest.mark.asyncio
    async def test_blocked_output_case_insensitive(self):
        """Competitor detection should be case-insensitive by default."""
        ctx = MagicMock(spec=RunContextWrapper)
        ctx.context = Mock(language_code="en")
        agent = MagicMock()

        with patch(
            f"{_MOD}.localize_guardrail_response",
            new_callable=AsyncMock,
        ) as mock_localize:
            mock_localize.return_value = "blocked"

            result = await _invoke_guardrail(
                competitor_blocking_guardrail,
                ctx,
                agent,
                "have you heard of ELISEAI?",
            )

        assert result.tripwire_triggered is True
        assert isinstance(result.output_info, CompetitorBlockingGuardrailOutput)

    @pytest.mark.asyncio
    async def test_blocked_output_reasoning_includes_competitor_names(self):
        """Reasoning field should list the detected competitor names."""
        ctx = MagicMock(spec=RunContextWrapper)
        ctx.context = Mock(language_code="en")
        agent = MagicMock()

        with patch(
            f"{_MOD}.localize_guardrail_response",
            new_callable=AsyncMock,
            return_value="blocked",
        ):
            result = await _invoke_guardrail(
                competitor_blocking_guardrail,
                ctx,
                agent,
                "EliseAI and Elise AI compete with us.",
            )

        assert "EliseAI" in result.output_info.reasoning
        assert "Elise AI" in result.output_info.reasoning

    @pytest.mark.asyncio
    async def test_localize_guardrail_response_called_correctly(self):
        """Verify localize_guardrail_response is called with the right arguments."""
        ctx = MagicMock(spec=RunContextWrapper)
        ctx.context = Mock(language_code="en")
        agent = MagicMock()
        output_text = "Try EliseAI today"

        with patch(
            f"{_MOD}.localize_guardrail_response",
            new_callable=AsyncMock,
        ) as mock_localize:
            mock_localize.return_value = "localized"

            await _invoke_guardrail(competitor_blocking_guardrail, ctx, agent, output_text)

        mock_localize.assert_called_once_with(
            base_response=mock_localize.call_args.kwargs["base_response"],
            guardrail_name="competitor_blocking_guardrail",
            original_content=output_text,
            content_type="output",
            language_code="en",
        )

    @pytest.mark.asyncio
    async def test_structured_output_object(self):
        """Guardrail handles structured output objects (not just strings)."""
        ctx = MagicMock(spec=RunContextWrapper)
        ctx.context = Mock(language_code="en")
        agent = MagicMock()

        output_obj = Mock()
        output_obj.content = "Consider using EliseAI for automation"
        # Make str(output_obj) not contain competitor names so we can verify
        # it's extracting from the .content attribute
        output_obj.__str__ = lambda self: "mock object"

        with patch(
            f"{_MOD}.localize_guardrail_response",
            new_callable=AsyncMock,
            return_value="blocked",
        ):
            result = await _invoke_guardrail(competitor_blocking_guardrail, ctx, agent, output_obj)

        assert result.tripwire_triggered is True
        assert isinstance(result.output_info, CompetitorBlockingGuardrailOutput)
