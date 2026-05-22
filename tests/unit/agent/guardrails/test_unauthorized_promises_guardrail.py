"""Unit tests for unauthorized_promises_guardrail_agent — covers uncovered lines 48, 71, 96-130, 139, 197."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from agents import GuardrailFunctionOutput

from agent_leasing.agent.guardrails.unauthorized_promises_guardrail_agent.unauthorized_promises_guardrail_agent import (
    SAFE_RESPONSE,
    UnauthorizedPromisesGuardrailAgentOutput,
    UnauthorizedPromisesGuardrailOutput,
    _evaluate_unauthorized_promises,
    detect_unauthorized_event_promise,
    unauthorized_promises_output_guardrail,
)

_MODULE = "agent_leasing.agent.guardrails.unauthorized_promises_guardrail_agent.unauthorized_promises_guardrail_agent"


# ---------------------------------------------------------------------------
# Model property tests (lines 48, 71)
# ---------------------------------------------------------------------------


class TestUnauthorizedPromisesAgentOutputLabels:
    """Test labels property on UnauthorizedPromisesGuardrailAgentOutput (line 48)."""

    def test_labels_always_returns_empty_list(self):
        output = UnauthorizedPromisesGuardrailAgentOutput(
            reasoning="promise detected",
            is_promise=True,
        )
        assert output.labels == []

    def test_labels_empty_when_not_promise(self):
        output = UnauthorizedPromisesGuardrailAgentOutput(
            reasoning="no promise",
            is_promise=False,
        )
        assert output.labels == []


class TestUnauthorizedPromisesGuardrailOutputLabels:
    """Test labels property on UnauthorizedPromisesGuardrailOutput (line 71)."""

    def test_labels_always_returns_empty_list(self):
        output = UnauthorizedPromisesGuardrailOutput(
            reasoning="blocked",
            safe_response="Cannot do that.",
            is_promise=True,
        )
        assert output.labels == []


# ---------------------------------------------------------------------------
# detect_unauthorized_event_promise tests (lines 96-130)
# ---------------------------------------------------------------------------


class TestDetectUnauthorizedEventPromise:
    """Test the fast heuristic for unauthorized event/amenity promises."""

    @pytest.mark.parametrize(
        "text",
        [
            "I'll make sure we have a spot reserved for you at the pool party.",
            "I can get you into the resident mixer even though RSVPs are closed.",
            "We'll reserve a spot at the gym for you.",
            "I will book the clubhouse for your event.",
            "I'll hold a spot for you at the pool party.",
            "We can save you a spot at the amenity event.",
            "I have reserved the clubhouse for you.",
            "Reserved for you at the party this weekend.",
            "I'll save a spot for you at the pool.",
            "We'll hold you a spot at the gym event.",
        ],
    )
    def test_detects_unauthorized_event_promises(self, text):
        assert detect_unauthorized_event_promise(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            # Compliant phrasing that should NOT be flagged
            "You can reserve the pool area through the portal.",
            "To reserve the amenity, go online.",
            "Make a reservation online for the gym.",
            "You can make your reservation online.",
            # No event keyword present
            "I'll make sure you have the best apartment.",
            "I'll guarantee your application is approved.",
            # Empty / whitespace
            "",
            "   ",
            # Has event keyword but no promise pattern
            "The pool is open until 9 PM.",
            "The clubhouse hosts events on Fridays.",
            "What amenities are available?",
        ],
    )
    def test_does_not_flag_compliant_or_irrelevant_text(self, text):
        assert detect_unauthorized_event_promise(text) is False


# ---------------------------------------------------------------------------
# _evaluate_unauthorized_promises tests (line 139 — is_promise=True path)
# ---------------------------------------------------------------------------


class TestEvaluateUnauthorizedPromises:
    """Test _evaluate_unauthorized_promises covering both block and pass paths."""

    @pytest.mark.asyncio
    async def test_promise_detected_triggers_tripwire(self):
        """When guardrail agent says is_promise=True, tripwire should fire."""
        mock_decision = Mock()
        mock_decision.is_promise = True
        mock_decision.reasoning = "agent promised a waiver"
        mock_decision.safe_response = SAFE_RESPONSE

        mock_result = Mock()
        mock_result.final_output = mock_decision

        ctx = Mock()
        ctx.context = Mock()

        with (
            patch(f"{_MODULE}.Runner.run", new_callable=AsyncMock, return_value=mock_result),
            patch(f"{_MODULE}.extract_text_from_output", return_value="I'll waive the fee for you."),
            patch(f"{_MODULE}.localize_guardrail_response", new_callable=AsyncMock, return_value=SAFE_RESPONSE),
        ):
            result = await _evaluate_unauthorized_promises(ctx, "I'll waive the fee for you.", "output")

        assert result.tripwire_triggered is True
        assert isinstance(result.output_info, UnauthorizedPromisesGuardrailOutput)
        assert result.output_info.is_promise is True

    @pytest.mark.asyncio
    async def test_no_promise_does_not_trigger_tripwire(self):
        """When guardrail agent says is_promise=False, tripwire should not fire."""
        mock_decision = Mock()
        mock_decision.is_promise = False
        mock_decision.reasoning = "no issues"

        mock_result = Mock()
        mock_result.final_output = mock_decision

        ctx = Mock()
        ctx.context = Mock()

        original_content = "I can look up your balance."

        with (
            patch(f"{_MODULE}.Runner.run", new_callable=AsyncMock, return_value=mock_result),
            patch(f"{_MODULE}.extract_text_from_output", return_value=original_content),
        ):
            result = await _evaluate_unauthorized_promises(ctx, original_content, "output")

        assert result.tripwire_triggered is False
        assert result.output_info == original_content

    @pytest.mark.asyncio
    async def test_input_content_type_uses_extract_text_from_input(self):
        """The 'input' content_type path calls extract_text_from_input."""
        mock_decision = Mock()
        mock_decision.is_promise = False

        mock_result = Mock()
        mock_result.final_output = mock_decision

        ctx = Mock()
        ctx.context = Mock()

        with (
            patch(f"{_MODULE}.Runner.run", new_callable=AsyncMock, return_value=mock_result),
            patch(f"{_MODULE}.extract_text_from_input", return_value="input text") as mock_extract,
        ):
            await _evaluate_unauthorized_promises(ctx, "raw input", "input")

        mock_extract.assert_called_once_with("raw input")

    @pytest.mark.asyncio
    async def test_localize_guardrail_response_called_on_promise(self):
        """Verify localize_guardrail_response receives correct args when promise detected."""
        mock_decision = Mock()
        mock_decision.is_promise = True
        mock_decision.reasoning = "promise"
        mock_decision.safe_response = SAFE_RESPONSE

        mock_result = Mock()
        mock_result.final_output = mock_decision

        ctx = Mock()
        ctx.context = Mock()

        original = "I will waive the late fees."

        with (
            patch(f"{_MODULE}.Runner.run", new_callable=AsyncMock, return_value=mock_result),
            patch(f"{_MODULE}.extract_text_from_output", return_value="text"),
            patch(
                f"{_MODULE}.localize_guardrail_response", new_callable=AsyncMock, return_value="localized"
            ) as mock_localize,
        ):
            await _evaluate_unauthorized_promises(ctx, original, "output")

        mock_localize.assert_called_once_with(
            base_response=SAFE_RESPONSE,
            guardrail_name="unauthorized_promises_guardrail",
            original_content=original,
            content_type="output",
            language_code=ctx.context.language_code,
        )


# ---------------------------------------------------------------------------
# Decorator wrapper test (line 197)
# ---------------------------------------------------------------------------


class TestUnauthorizedPromisesOutputGuardrail:
    """Test that the output guardrail decorator delegates correctly."""

    @pytest.mark.asyncio
    async def test_output_guardrail_delegates(self):
        ctx = Mock()
        ctx.context = Mock()
        agent = Mock()
        output_content = "I'll waive the deposit for you."

        with patch(f"{_MODULE}._evaluate_unauthorized_promises", new_callable=AsyncMock) as mock_eval:
            expected = GuardrailFunctionOutput(output_info=output_content, tripwire_triggered=True)
            mock_eval.return_value = expected

            result = await unauthorized_promises_output_guardrail.guardrail_function(ctx, agent, output_content)

        mock_eval.assert_called_once_with(ctx, output_content, "output")
        assert result == expected
