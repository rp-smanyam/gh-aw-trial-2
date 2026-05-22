"""Unit tests for fair_housing_guardrail_agent — covers uncovered lines 69-71, 96-98, 109-140, 154, 165."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from agents import GuardrailFunctionOutput

from agent_leasing.agent.guardrails.fair_housing_guardrail_agent.fair_housing_guardrail_agent import (
    FairHousingAgentOutput,
    FairHousingGuardrailOutput,
    _evaluate_fair_housing,
    fair_housing_input_guardrail,
    fair_housing_output_guardrail,
    prompts,
)

# ---------------------------------------------------------------------------
# Model property tests (lines 69-71, 96-98)
# ---------------------------------------------------------------------------


class TestFairHousingAgentOutputLabels:
    """Test the labels property on FairHousingAgentOutput."""

    def test_labels_returns_category_when_present(self):
        output = FairHousingAgentOutput(
            reasoning="detected race bias",
            fair_housing_category="race",
            is_discriminative=True,
        )
        assert output.labels == ["race"]

    def test_labels_returns_empty_when_none_category(self):
        output = FairHousingAgentOutput(
            reasoning="no issue",
            fair_housing_category="none",
            is_discriminative=False,
        )
        assert output.labels == []

    @pytest.mark.parametrize(
        "category",
        ["religion", "disability", "sexual orientation", "other"],
    )
    def test_labels_returns_list_for_various_categories(self, category):
        output = FairHousingAgentOutput(
            reasoning="test",
            fair_housing_category=category,
            is_discriminative=True,
        )
        assert output.labels == [category]


class TestFairHousingGuardrailOutputLabels:
    """Test the labels property on FairHousingGuardrailOutput."""

    def test_labels_returns_category_when_present(self):
        output = FairHousingGuardrailOutput(
            reasoning="blocked",
            safe_response="I cannot help with that.",
            category="familial status",
            is_discriminative=True,
        )
        assert output.labels == ["familial status"]

    def test_labels_returns_empty_when_none_category(self):
        output = FairHousingGuardrailOutput(
            reasoning="safe",
            safe_response="ok",
            category="none",
            is_discriminative=False,
        )
        assert output.labels == []

    def test_labels_returns_empty_when_category_is_empty_string(self):
        output = FairHousingGuardrailOutput(
            reasoning="safe",
            safe_response="ok",
            category="",
            is_discriminative=False,
        )
        assert output.labels == []


# ---------------------------------------------------------------------------
# _evaluate_fair_housing tests (lines 109-140)
# ---------------------------------------------------------------------------

_MODULE = "agent_leasing.agent.guardrails.fair_housing_guardrail_agent.fair_housing_guardrail_agent"


class TestEvaluateFairHousing:
    """Test the _evaluate_fair_housing helper covering both input/output paths and block/pass."""

    @pytest.mark.asyncio
    async def test_discriminative_input_triggers_tripwire(self):
        """When the guardrail agent says is_discriminative=True for input, tripwire fires."""
        mock_decision = Mock()
        mock_decision.is_discriminative = True
        mock_decision.reasoning = "racial bias detected"
        mock_decision.safe_response = "I cannot help with that."
        mock_decision.fair_housing_category = "race"

        mock_result = Mock()
        mock_result.final_output = mock_decision

        ctx = Mock()
        ctx.context = Mock()

        with (
            patch(f"{_MODULE}.Runner.run", new_callable=AsyncMock, return_value=mock_result),
            patch(f"{_MODULE}.extract_text_from_input", return_value="some discriminative text"),
            patch(f"{_MODULE}.localize_guardrail_response", new_callable=AsyncMock, return_value="I cannot help."),
        ):
            result = await _evaluate_fair_housing(ctx, "discriminative text", "input")

        assert result.tripwire_triggered is True
        assert isinstance(result.output_info, FairHousingGuardrailOutput)
        assert result.output_info.category == "race"
        assert result.output_info.is_discriminative is True

    @pytest.mark.asyncio
    async def test_discriminative_output_triggers_tripwire(self):
        """When the guardrail agent says is_discriminative=True for output, tripwire fires."""
        mock_decision = Mock()
        mock_decision.is_discriminative = True
        mock_decision.reasoning = "disability bias"
        mock_decision.safe_response = "Cannot do that."
        mock_decision.fair_housing_category = "disability"

        mock_result = Mock()
        mock_result.final_output = mock_decision

        ctx = Mock()
        ctx.context = Mock()

        with (
            patch(f"{_MODULE}.Runner.run", new_callable=AsyncMock, return_value=mock_result),
            patch(f"{_MODULE}.extract_text_from_output", return_value="biased output"),
            patch(f"{_MODULE}.localize_guardrail_response", new_callable=AsyncMock, return_value="Safe."),
        ):
            result = await _evaluate_fair_housing(ctx, "biased output obj", "output")

        assert result.tripwire_triggered is True
        assert isinstance(result.output_info, FairHousingGuardrailOutput)
        assert result.output_info.category == "disability"

    @pytest.mark.asyncio
    async def test_safe_content_does_not_trigger_tripwire(self):
        """When the guardrail agent says is_discriminative=False, tripwire does not fire."""
        mock_decision = Mock()
        mock_decision.is_discriminative = False
        mock_decision.reasoning = "no issues"
        mock_decision.fair_housing_category = "none"

        mock_result = Mock()
        mock_result.final_output = mock_decision

        ctx = Mock()
        ctx.context = Mock()

        original_content = "What amenities do you have?"

        with (
            patch(f"{_MODULE}.Runner.run", new_callable=AsyncMock, return_value=mock_result),
            patch(f"{_MODULE}.extract_text_from_input", return_value=original_content),
        ):
            result = await _evaluate_fair_housing(ctx, original_content, "input")

        assert result.tripwire_triggered is False
        assert result.output_info == original_content

    @pytest.mark.asyncio
    async def test_localize_guardrail_response_called_with_correct_args(self):
        """Verify localize_guardrail_response receives the right parameters."""
        mock_decision = Mock()
        mock_decision.is_discriminative = True
        mock_decision.reasoning = "test"
        mock_decision.safe_response = "safe default"
        mock_decision.fair_housing_category = "sex"

        mock_result = Mock()
        mock_result.final_output = mock_decision

        ctx = Mock()
        ctx.context = Mock()

        original_content = "some output"

        with (
            patch(f"{_MODULE}.Runner.run", new_callable=AsyncMock, return_value=mock_result),
            patch(f"{_MODULE}.extract_text_from_output", return_value="text"),
            patch(
                f"{_MODULE}.localize_guardrail_response", new_callable=AsyncMock, return_value="localized"
            ) as mock_localize,
        ):
            await _evaluate_fair_housing(ctx, original_content, "output")

        mock_localize.assert_called_once_with(
            base_response="safe default",
            guardrail_name="fair_housing_guardrail",
            original_content=original_content,
            content_type="output",
            language_code=ctx.context.language_code,
        )


# ---------------------------------------------------------------------------
# Decorator wrapper tests (lines 154, 165)
# ---------------------------------------------------------------------------


class TestFairHousingInputGuardrail:
    """Test fair_housing_input_guardrail delegates to _evaluate_fair_housing with 'input'."""

    @pytest.mark.asyncio
    async def test_input_guardrail_delegates(self):
        ctx = Mock()
        ctx.context = Mock()
        agent = Mock()
        input_content = "Can only white people apply?"

        with patch(f"{_MODULE}._evaluate_fair_housing", new_callable=AsyncMock) as mock_eval:
            expected = GuardrailFunctionOutput(output_info=input_content, tripwire_triggered=False)
            mock_eval.return_value = expected

            # The @input_guardrail decorator wraps the function; call the underlying guardrail_function
            result = await fair_housing_input_guardrail.guardrail_function(ctx, agent, input_content)

        mock_eval.assert_called_once_with(ctx, input_content, "input")
        assert result == expected


class TestFairHousingOutputGuardrail:
    """Test fair_housing_output_guardrail delegates to _evaluate_fair_housing with 'output'."""

    @pytest.mark.asyncio
    async def test_output_guardrail_delegates(self):
        ctx = Mock()
        ctx.context = Mock()
        agent = Mock()
        output_content = "We prefer tenants of a certain background."

        with patch(f"{_MODULE}._evaluate_fair_housing", new_callable=AsyncMock) as mock_eval:
            expected = GuardrailFunctionOutput(output_info=output_content, tripwire_triggered=True)
            mock_eval.return_value = expected

            result = await fair_housing_output_guardrail.guardrail_function(ctx, agent, output_content)

        mock_eval.assert_called_once_with(ctx, output_content, "output")
        assert result == expected


class TestFairHousingPromptGuidance:
    """Regression tests to keep fair housing prompt focused on protected-class signals."""

    def test_prompt_contains_default_to_safe_rule(self):
        assert "DEFAULT-TO-SAFE RULE" in prompts.AGENT

    def test_prompt_contains_operational_message_carve_out(self):
        assert "Do NOT flag ordinary resident support messages about rent, billing" in prompts.AGENT

    def test_prompt_contains_acknowledgement_carve_out(self):
        assert "Do NOT flag short acknowledgements" in prompts.AGENT

    def test_prompt_contains_false_positive_regression_examples(self):
        assert "This rent is wrong.. trued to pay online but rent amount also wrong" in prompts.AGENT
        assert "Sounds good" in prompts.AGENT
