"""Unit tests for security_guardrail_agent."""

import inspect
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from agents import GuardrailFunctionOutput, RunContextWrapper

from agent_leasing.agent.guardrails.security_guardrail_agent.security_guardrail_agent import (
    _BLOCK_MESSAGE,
    SecurityGuardrailOutput,
    _check_content_safety,
    security_input_guardrail,
    security_output_guardrail,
)

# Module path prefix for patching
_MOD = "agent_leasing.agent.guardrails.security_guardrail_agent.security_guardrail_agent"


def _make_moderation_result(category_scores: dict[str, float], flagged: bool = False):
    """Build a mock OpenAI moderation response."""
    result = Mock()
    result.flagged = flagged
    result.category_scores = Mock()
    result.category_scores.model_dump.return_value = category_scores

    response = Mock()
    response.results = [result]
    return response


def _make_context(security_bypass: bool = False, language_code: str = "en"):
    """Build a mock SessionScope context."""
    ctx = Mock()
    ctx.security_bypass = security_bypass
    ctx.language_code = language_code
    return ctx


async def _invoke_guardrail(guardrail, ctx, agent, payload):
    """Call a guardrail's underlying function, awaiting if needed."""
    result = guardrail.guardrail_function(ctx, agent, payload)
    if inspect.isawaitable(result):
        return await result
    return result


class TestSecurityGuardrailOutput:
    """Tests for the SecurityGuardrailOutput Pydantic model."""

    def test_labels_returns_flagged_categories(self):
        output = SecurityGuardrailOutput(
            reasoning="test",
            flagged_categories=["violence", "sexual"],
            is_harmful=True,
            safe_response=_BLOCK_MESSAGE,
        )
        assert output.labels == ["violence", "sexual"]

    def test_labels_empty_when_no_categories(self):
        output = SecurityGuardrailOutput(
            reasoning=None,
            flagged_categories=[],
            is_harmful=False,
        )
        assert output.labels == []

    def test_default_safe_response_is_block_message(self):
        output = SecurityGuardrailOutput(
            flagged_categories=[],
            is_harmful=False,
        )
        assert output.safe_response == _BLOCK_MESSAGE


class TestCheckContentSafety:
    """Tests for _check_content_safety — the core moderation logic."""

    @pytest.mark.asyncio
    async def test_safe_content_returns_not_triggered(self):
        """When no category exceeds threshold, tripwire is not triggered."""
        context = _make_context()
        scores = {"violence": 0.01, "sexual": 0.02}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {
                "violence": 0.25,
                "sexual": 0.4,
            }

            result = await _check_content_safety("Hello, how are you?", "input", context)

        assert isinstance(result, GuardrailFunctionOutput)
        assert result.tripwire_triggered is False
        assert result.output_info == "Hello, how are you?"

    @pytest.mark.asyncio
    async def test_harmful_content_triggers_tripwire(self):
        """When a category score exceeds its threshold, tripwire is triggered."""
        context = _make_context()
        scores = {"violence": 0.9, "sexual": 0.01}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.localize_guardrail_response", new_callable=AsyncMock) as mock_localize,
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock) as mock_threat,
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {
                "violence": 0.25,
                "sexual": 0.4,
            }
            mock_threat.return_value = False
            mock_localize.return_value = "Localized block message"

            result = await _check_content_safety("I will destroy everything", "input", context)

        assert result.tripwire_triggered is True
        assert isinstance(result.output_info, SecurityGuardrailOutput)
        assert result.output_info.is_harmful is True
        assert "violence" in result.output_info.flagged_categories
        assert result.output_info.safe_response == "Localized block message"
        assert result.output_info.reasoning is not None

    @pytest.mark.asyncio
    async def test_harmful_content_with_multiple_flagged_categories(self):
        """Multiple categories can be flagged simultaneously."""
        context = _make_context()
        scores = {"violence": 0.5, "sexual": 0.8}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.localize_guardrail_response", new_callable=AsyncMock) as mock_localize,
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock) as mock_threat,
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {
                "violence": 0.25,
                "sexual": 0.4,
            }
            mock_threat.return_value = False
            mock_localize.return_value = _BLOCK_MESSAGE

            result = await _check_content_safety("bad content", "input", context)

        assert result.tripwire_triggered is True
        assert set(result.output_info.flagged_categories) == {"violence", "sexual"}

    @pytest.mark.asyncio
    async def test_threat_to_user_bypass_disables_tripwire(self):
        """When the user is the victim of a threat, the guardrail should not trigger."""
        context = _make_context(security_bypass=False)
        scores = {"violence": 0.9}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock) as mock_threat,
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}
            mock_threat.return_value = True

            result = await _check_content_safety("My neighbor threatened to kill me", "input", context)

        assert result.tripwire_triggered is False
        # The bypass flag should have been set on the context
        assert context.security_bypass is True

    @pytest.mark.asyncio
    async def test_accidental_injury_bypasses_guardrail(self):
        """When the user reports an accidental injury, the guardrail should not trigger."""
        context = _make_context(security_bypass=False)
        scores = {"violence": 0.9, "self-harm": 0.5}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock) as mock_threat,
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"violence": 0.25, "self-harm": 0.25}
            mock_threat.return_value = True  # accidental injury → threat_to_user=True

            result = await _check_content_safety("I hit myself to the wall and I'm bleeding now", "input", context)

        assert result.tripwire_triggered is False
        assert context.security_bypass is True

    @pytest.mark.asyncio
    async def test_threat_to_user_bypass_does_not_apply_to_output(self):
        """is_threat_to_user bypass must NOT fire for output content — harmful agent output should always be blocked."""
        context = _make_context(security_bypass=False)
        scores = {"violence": 0.9}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.localize_guardrail_response", new_callable=AsyncMock) as mock_localize,
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock) as mock_threat,
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}
            mock_localize.return_value = _BLOCK_MESSAGE
            mock_threat.return_value = True  # even if threat evaluator says user is victim

            result = await _check_content_safety("I want to burn your skin off with acid", "output", context)

        # is_threat_to_user must never be called for output content
        mock_threat.assert_not_called()
        # Harmful output must always be blocked regardless
        assert result.tripwire_triggered is True
        assert context.security_bypass is False

    @pytest.mark.asyncio
    async def test_security_bypass_already_set_skips_threat_check(self):
        """When security_bypass is already True, is_threat_to_user should NOT be called again."""
        context = _make_context(security_bypass=True)
        scores = {"violence": 0.9}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.localize_guardrail_response", new_callable=AsyncMock) as mock_localize,
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock) as mock_threat,
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}
            mock_localize.return_value = _BLOCK_MESSAGE

            result = await _check_content_safety("More threatening text", "input", context)

        # is_threat_to_user should not be called because security_bypass is already True
        mock_threat.assert_not_called()
        # With bypass already set, the is_harmful check proceeds normally (no bypass)
        assert result.tripwire_triggered is True

    @pytest.mark.asyncio
    async def test_invalid_content_type_raises_value_error(self):
        """Passing an unknown content_type should raise ValueError."""
        context = _make_context()

        with pytest.raises(ValueError, match="Invalid content type: banana"):
            await _check_content_safety("some text", "banana", context)

    @pytest.mark.asyncio
    async def test_input_content_type_calls_extract_text_from_input(self):
        """content_type='input' should use extract_text_from_input."""
        context = _make_context()
        input_items = [{"role": "user", "content": "Hello"}]
        scores = {"violence": 0.0}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.extract_text_from_input") as mock_extract,
        ):
            mock_extract.return_value = "Hello"
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}

            await _check_content_safety(input_items, "input", context)

        mock_extract.assert_called_once_with(input_items)

    @pytest.mark.asyncio
    async def test_output_content_type_calls_extract_text_from_output(self):
        """content_type='output' should use extract_text_from_output."""
        context = _make_context()
        output_obj = Mock()
        output_obj.content = "Agent response"
        scores = {"violence": 0.0}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.extract_text_from_output") as mock_extract,
        ):
            mock_extract.return_value = "Agent response"
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}

            await _check_content_safety(output_obj, "output", context)

        mock_extract.assert_called_once_with(output_obj)

    @pytest.mark.asyncio
    async def test_category_not_in_scores_defaults_to_zero(self):
        """If a threshold category is missing from scores, it should default to 0.0."""
        context = _make_context()
        # The response has no scores at all — every category defaults to 0.0
        scores = {}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {
                "violence": 0.25,
                "sexual": 0.4,
            }

            result = await _check_content_safety("just a normal sentence", "input", context)

        assert result.tripwire_triggered is False

    @pytest.mark.asyncio
    async def test_localize_guardrail_response_called_with_correct_args(self):
        """Verify localize_guardrail_response is called with the right arguments when harmful."""
        context = _make_context()
        scores = {"violence": 0.9}
        original = "violent content"

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.localize_guardrail_response", new_callable=AsyncMock) as mock_localize,
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock, return_value=False),
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}
            mock_localize.return_value = "localized"

            await _check_content_safety(original, "input", context)

        mock_localize.assert_called_once_with(
            base_response=_BLOCK_MESSAGE,
            guardrail_name="security_guardrail",
            original_content=original,
            content_type="input",
            language_code="en",
        )


class TestSTTFalsePositiveBypass:
    """Tests for the voice STT artifact bypass in _check_content_safety."""

    @pytest.mark.asyncio
    async def test_voice_stt_artifact_bypasses_guardrail(self):
        """When voice channel + flagged + STT artifact detected, guardrail should not trigger."""
        context = _make_context()
        scores = {"violence": 0.9}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.get_channel_from_context", return_value="VOICE"),
            patch(f"{_MOD}.is_stt_false_positive", new_callable=AsyncMock, return_value=True),
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}

            result = await _check_content_safety("I need help with my killing", "input", context)

        assert result.tripwire_triggered is False

    @pytest.mark.asyncio
    async def test_voice_genuine_threat_not_bypassed(self):
        """When voice channel + flagged + NOT an STT artifact, guardrail should still trigger."""
        context = _make_context()
        scores = {"violence": 0.9}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.get_channel_from_context", return_value="VOICE"),
            patch(f"{_MOD}.is_stt_false_positive", new_callable=AsyncMock, return_value=False),
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock, return_value=False),
            patch(f"{_MOD}.localize_guardrail_response", new_callable=AsyncMock, return_value=_BLOCK_MESSAGE),
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}

            result = await _check_content_safety("I'm going to kill my neighbor", "input", context)

        assert result.tripwire_triggered is True

    @pytest.mark.asyncio
    async def test_non_voice_channel_skips_stt_check(self):
        """STT false positive check should only run for VOICE channel."""
        context = _make_context()
        scores = {"violence": 0.9}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.get_channel_from_context", return_value="CHAT"),
            patch(f"{_MOD}.is_stt_false_positive", new_callable=AsyncMock) as mock_stt,
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock, return_value=False),
            patch(f"{_MOD}.localize_guardrail_response", new_callable=AsyncMock, return_value=_BLOCK_MESSAGE),
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}

            result = await _check_content_safety("killing", "input", context)

        mock_stt.assert_not_called()
        assert result.tripwire_triggered is True

    @pytest.mark.asyncio
    async def test_output_content_skips_stt_check(self):
        """STT false positive check should only run for input, not output."""
        context = _make_context()
        scores = {"violence": 0.9}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.get_channel_from_context", return_value="VOICE"),
            patch(f"{_MOD}.is_stt_false_positive", new_callable=AsyncMock) as mock_stt,
            patch(f"{_MOD}.localize_guardrail_response", new_callable=AsyncMock, return_value=_BLOCK_MESSAGE),
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}

            result = await _check_content_safety("violent output", "output", context)

        mock_stt.assert_not_called()
        assert result.tripwire_triggered is True

    @pytest.mark.asyncio
    async def test_stt_bypass_runs_before_threat_check(self):
        """When STT artifact is detected, is_threat_to_user should not be called."""
        context = _make_context()
        scores = {"violence": 0.9}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.get_channel_from_context", return_value="VOICE"),
            patch(f"{_MOD}.is_stt_false_positive", new_callable=AsyncMock, return_value=True),
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock) as mock_threat,
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}

            result = await _check_content_safety("I need help with my killing", "input", context)

        assert result.tripwire_triggered is False
        mock_threat.assert_not_called()

    @pytest.mark.asyncio
    async def test_stt_evaluator_failure_fails_closed(self):
        """When STT evaluator raises an exception, guardrail should still trigger (fail closed)."""
        context = _make_context()
        scores = {"violence": 0.9}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.get_channel_from_context", return_value="VOICE"),
            patch(f"{_MOD}.is_stt_false_positive", new_callable=AsyncMock, side_effect=RuntimeError("timeout")),
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock, return_value=False),
            patch(f"{_MOD}.localize_guardrail_response", new_callable=AsyncMock, return_value=_BLOCK_MESSAGE),
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}

            result = await _check_content_safety("I need help with my killing", "input", context)

        assert result.tripwire_triggered is True


class TestFrustrationBypass:
    """Tests for the frustration evaluator bypass in _check_content_safety."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "text",
        [
            "Omg AI sucks!!!",
            "What the fuck are you talking about",
            "Why the fuck would you say this? I paid it yesterday on time...",
            "Come on people quit sending out stupid texts...",
            "You have been very kind...Too bad your company sucks.",
        ],
    )
    async def test_production_like_frustration_inputs_bypass_on_sms(self, text):
        """Production-like frustration complaints should bypass when harassment-only and non-voice."""
        context = _make_context()
        scores = {"harassment": 0.5}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.get_channel_from_context", return_value="SMS"),
            patch(f"{_MOD}.is_frustration_not_harassment", new_callable=AsyncMock, return_value=True),
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"harassment": 0.35, "harassment/threatening": 0.25}

            result = await _check_content_safety(text, "input", context)

        assert result.tripwire_triggered is False

    @pytest.mark.asyncio
    async def test_frustration_bypasses_guardrail_on_chat(self):
        """When chat channel + harassment-only flag + frustration detected, guardrail should not trigger."""
        context = _make_context()
        scores = {"harassment": 0.5}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.get_channel_from_context", return_value="CHAT"),
            patch(f"{_MOD}.is_frustration_not_harassment", new_callable=AsyncMock, return_value=True),
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"harassment": 0.35}

            result = await _check_content_safety("you're not doing s*** for me", "input", context)

        assert result.tripwire_triggered is False

    @pytest.mark.asyncio
    async def test_genuine_harassment_not_bypassed(self):
        """When chat channel + harassment flag + NOT frustration, guardrail should trigger."""
        context = _make_context()
        scores = {"harassment": 0.8}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.get_channel_from_context", return_value="CHAT"),
            patch(f"{_MOD}.is_frustration_not_harassment", new_callable=AsyncMock, return_value=False),
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock, return_value=False),
            patch(f"{_MOD}.localize_guardrail_response", new_callable=AsyncMock, return_value=_BLOCK_MESSAGE),
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"harassment": 0.35}

            result = await _check_content_safety("you're a worthless piece of garbage", "input", context)

        assert result.tripwire_triggered is True

    @pytest.mark.asyncio
    async def test_frustration_bypass_skips_voice_channel(self):
        """Frustration evaluator should NOT run for voice (voice uses STT bypass instead)."""
        context = _make_context()
        scores = {"harassment": 0.5}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.get_channel_from_context", return_value="VOICE"),
            patch(f"{_MOD}.is_stt_false_positive", new_callable=AsyncMock, return_value=False),
            patch(f"{_MOD}.is_frustration_not_harassment", new_callable=AsyncMock) as mock_frustration,
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock, return_value=False),
            patch(f"{_MOD}.localize_guardrail_response", new_callable=AsyncMock, return_value=_BLOCK_MESSAGE),
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"harassment": 0.35}

            await _check_content_safety("you're not doing s*** for me", "input", context)

        mock_frustration.assert_not_called()

    @pytest.mark.asyncio
    async def test_frustration_bypass_skips_harassment_threatening(self):
        """Frustration evaluator should NOT run when harassment/threatening is flagged."""
        context = _make_context()
        scores = {"harassment/threatening": 0.5}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.get_channel_from_context", return_value="CHAT"),
            patch(f"{_MOD}.is_frustration_not_harassment", new_callable=AsyncMock) as mock_frustration,
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock, return_value=False),
            patch(f"{_MOD}.localize_guardrail_response", new_callable=AsyncMock, return_value=_BLOCK_MESSAGE),
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"harassment/threatening": 0.35}

            result = await _check_content_safety("I'll find out where you work", "input", context)

        mock_frustration.assert_not_called()
        assert result.tripwire_triggered is True

    @pytest.mark.asyncio
    async def test_stop_texting_with_threatening_harassment_still_triggers(self):
        """Even frustration-like phrasing should not bypass when harassment/threatening is flagged."""
        context = _make_context()
        scores = {"harassment/threatening": 0.5}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.get_channel_from_context", return_value="SMS"),
            patch(f"{_MOD}.is_frustration_not_harassment", new_callable=AsyncMock) as mock_frustration,
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock, return_value=False),
            patch(f"{_MOD}.localize_guardrail_response", new_callable=AsyncMock, return_value=_BLOCK_MESSAGE),
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"harassment": 0.35, "harassment/threatening": 0.25}

            result = await _check_content_safety("Stop fucking texting me", "input", context)

        mock_frustration.assert_not_called()
        assert result.tripwire_triggered is True

    @pytest.mark.asyncio
    async def test_frustration_bypass_skips_non_harassment_categories(self):
        """Frustration evaluator should NOT run when non-harassment categories are also flagged."""
        context = _make_context()
        scores = {"harassment": 0.5, "violence": 0.5}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.get_channel_from_context", return_value="CHAT"),
            patch(f"{_MOD}.is_frustration_not_harassment", new_callable=AsyncMock) as mock_frustration,
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock, return_value=False),
            patch(f"{_MOD}.localize_guardrail_response", new_callable=AsyncMock, return_value=_BLOCK_MESSAGE),
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"harassment": 0.35, "violence": 0.35}

            result = await _check_content_safety("violent harassing text", "input", context)

        mock_frustration.assert_not_called()
        assert result.tripwire_triggered is True

    @pytest.mark.asyncio
    async def test_frustration_bypass_skips_output(self):
        """Frustration evaluator should NOT run on output content."""
        context = _make_context()
        scores = {"harassment": 0.5}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.get_channel_from_context", return_value="CHAT"),
            patch(f"{_MOD}.is_frustration_not_harassment", new_callable=AsyncMock) as mock_frustration,
            patch(f"{_MOD}.localize_guardrail_response", new_callable=AsyncMock, return_value=_BLOCK_MESSAGE),
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"harassment": 0.35}

            result = await _check_content_safety("harassing output", "output", context)

        mock_frustration.assert_not_called()
        assert result.tripwire_triggered is True

    @pytest.mark.asyncio
    async def test_frustration_evaluator_failure_fails_closed(self):
        """When frustration evaluator errors internally it returns False, so the guardrail still triggers."""
        context = _make_context()
        scores = {"harassment": 0.5}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.get_channel_from_context", return_value="CHAT"),
            patch(f"{_MOD}.is_frustration_not_harassment", new_callable=AsyncMock, return_value=False),
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock, return_value=False),
            patch(f"{_MOD}.localize_guardrail_response", new_callable=AsyncMock, return_value=_BLOCK_MESSAGE),
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"harassment": 0.35}

            result = await _check_content_safety("you're not doing s*** for me", "input", context)

        assert result.tripwire_triggered is True


class TestSecurityInputGuardrail:
    """Tests for the @input_guardrail decorated security_input_guardrail."""

    @pytest.mark.asyncio
    async def test_input_guardrail_safe_content(self):
        """Input guardrail passes safe content through."""
        ctx = MagicMock(spec=RunContextWrapper)
        ctx.context = _make_context()
        agent = MagicMock()
        scores = {"violence": 0.0}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}

            result = await _invoke_guardrail(security_input_guardrail, ctx, agent, "How do I pay rent?")

        assert result.tripwire_triggered is False
        assert result.output_info == "How do I pay rent?"

    @pytest.mark.asyncio
    async def test_input_guardrail_harmful_content(self):
        """Input guardrail blocks harmful content."""
        ctx = MagicMock(spec=RunContextWrapper)
        ctx.context = _make_context()
        agent = MagicMock()
        scores = {"violence": 0.95}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.localize_guardrail_response", new_callable=AsyncMock) as mock_localize,
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock, return_value=False),
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}
            mock_localize.return_value = _BLOCK_MESSAGE

            result = await _invoke_guardrail(security_input_guardrail, ctx, agent, "extremely violent text")

        assert result.tripwire_triggered is True
        assert isinstance(result.output_info, SecurityGuardrailOutput)


class TestSecurityOutputGuardrail:
    """Tests for the @output_guardrail decorated security_output_guardrail."""

    @pytest.mark.asyncio
    async def test_output_guardrail_safe_content(self):
        """Output guardrail passes safe content through."""
        ctx = MagicMock(spec=RunContextWrapper)
        ctx.context = _make_context()
        agent = MagicMock()
        scores = {"violence": 0.0}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}

            result = await _invoke_guardrail(security_output_guardrail, ctx, agent, "Your rent is $1500 per month.")

        assert result.tripwire_triggered is False
        assert result.output_info == "Your rent is $1500 per month."

    @pytest.mark.asyncio
    async def test_output_guardrail_harmful_content(self):
        """Output guardrail blocks harmful agent output."""
        ctx = MagicMock(spec=RunContextWrapper)
        ctx.context = _make_context()
        agent = MagicMock()
        scores = {"sexual": 0.85}

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.localize_guardrail_response", new_callable=AsyncMock) as mock_localize,
            patch(f"{_MOD}.is_threat_to_user", new_callable=AsyncMock, return_value=False),
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(return_value=_make_moderation_result(scores))
            mock_settings.security_guardrail_thresholds = {"sexual": 0.4}
            mock_localize.return_value = _BLOCK_MESSAGE

            result = await _invoke_guardrail(security_output_guardrail, ctx, agent, "inappropriate agent output")

        assert result.tripwire_triggered is True
        assert isinstance(result.output_info, SecurityGuardrailOutput)
        assert "sexual" in result.output_info.flagged_categories


class TestModerationApiFailure:
    """Tests for moderation API failure handling — see issue #1599.

    When OpenAI's moderation endpoint returns 5xx or times out, the guardrail
    must fail open (let the content through with a warning log) rather than
    propagating the exception up to the streaming generator, which causes the
    resident to receive the canned fallback response instead of the agent's
    real reply.
    """

    @pytest.mark.asyncio
    async def test_moderation_api_exception_fails_open_input(self):
        """When moderations.create raises (e.g. 504), input guardrail returns tripwire_triggered=False."""
        import openai

        context = _make_context()

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(
                side_effect=openai.InternalServerError(
                    message="504 Gateway Timeout",
                    response=Mock(status_code=504),
                    body=None,
                )
            )
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}
            mock_settings.security_guardrail_moderation_timeout_seconds = 5.0
            mock_settings.security_guardrail_fail_open_on_moderation_error = True

            result = await _check_content_safety("Hello", "input", context)

        assert result.tripwire_triggered is False
        assert result.output_info == "Hello"

    @pytest.mark.asyncio
    async def test_moderation_api_exception_fails_open_output(self):
        """When moderations.create raises on output, fail open so the agent's reply still gets delivered."""
        import openai

        context = _make_context()
        original_output = "Your rent is $1500."

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(
                side_effect=openai.InternalServerError(
                    message="504 Gateway Timeout",
                    response=Mock(status_code=504),
                    body=None,
                )
            )
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}
            mock_settings.security_guardrail_moderation_timeout_seconds = 5.0
            mock_settings.security_guardrail_fail_open_on_moderation_error = True

            result = await _check_content_safety(original_output, "output", context)

        assert result.tripwire_triggered is False
        assert result.output_info == original_output

    @pytest.mark.asyncio
    async def test_moderation_api_timeout_fails_open(self):
        """When the SDK raises APITimeoutError (per-request timeout exceeded), fail open."""
        import openai

        context = _make_context()

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(
                side_effect=openai.APITimeoutError(request=Mock())
            )
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}
            mock_settings.security_guardrail_moderation_timeout_seconds = 5.0
            mock_settings.security_guardrail_fail_open_on_moderation_error = True

            result = await _check_content_safety("Hello", "input", context)

        assert result.tripwire_triggered is False
        assert result.output_info == "Hello"

    @pytest.mark.asyncio
    async def test_fail_open_disabled_propagates_exception(self):
        """Kill switch: when fail-open is disabled, exception propagates (current/legacy behavior)."""
        import openai

        context = _make_context()

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(
                side_effect=openai.InternalServerError(
                    message="504 Gateway Timeout",
                    response=Mock(status_code=504),
                    body=None,
                )
            )
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}
            mock_settings.security_guardrail_moderation_timeout_seconds = 5.0
            mock_settings.security_guardrail_fail_open_on_moderation_error = False

            with pytest.raises(openai.InternalServerError):
                await _check_content_safety("Hello", "input", context)

    @pytest.mark.asyncio
    async def test_moderation_api_failure_logs_warning(self):
        """When the moderation API fails and we fail open, a warning is logged with the exception."""
        import openai

        context = _make_context()

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
            patch(f"{_MOD}.logger") as mock_logger,
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(
                side_effect=openai.InternalServerError(
                    message="504 Gateway Timeout",
                    response=Mock(status_code=504),
                    body=None,
                )
            )
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}
            mock_settings.security_guardrail_moderation_timeout_seconds = 5.0
            mock_settings.security_guardrail_fail_open_on_moderation_error = True

            await _check_content_safety("Hello", "input", context)

        assert mock_logger.warning.called, "Expected a warning log when moderation API fails"

    @pytest.mark.asyncio
    async def test_bad_request_error_propagates(self):
        """4xx BadRequestError is a persistent client bug (oversized input, bad model name);
        it must propagate, not silently disable moderation forever."""
        import openai

        context = _make_context()

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(
                side_effect=openai.BadRequestError(
                    message="Invalid input",
                    response=Mock(status_code=400),
                    body=None,
                )
            )
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}
            mock_settings.security_guardrail_moderation_timeout_seconds = 5.0
            mock_settings.security_guardrail_fail_open_on_moderation_error = True

            with pytest.raises(openai.BadRequestError):
                await _check_content_safety("Hello", "input", context)

    @pytest.mark.asyncio
    async def test_auth_error_propagates(self):
        """401 AuthenticationError is a persistent config bug; must propagate so we don't
        silently run with moderation disabled until someone notices."""
        import openai

        context = _make_context()

        with (
            patch(f"{_MOD}.get_openai_client") as mock_get_client,
            patch(f"{_MOD}.settings") as mock_settings,
        ):
            mock_get_client.return_value.moderations.create = AsyncMock(
                side_effect=openai.AuthenticationError(
                    message="Invalid API key",
                    response=Mock(status_code=401),
                    body=None,
                )
            )
            mock_settings.security_guardrail_thresholds = {"violence": 0.25}
            mock_settings.security_guardrail_moderation_timeout_seconds = 5.0
            mock_settings.security_guardrail_fail_open_on_moderation_error = True

            with pytest.raises(openai.AuthenticationError):
                await _check_content_safety("Hello", "input", context)
