from unittest.mock import patch

import pytest
from agents import Agent, RunContextWrapper

from agent_leasing.agent.guardrails.competitor_blocking_guardrail_agent.competitor_blocking_guardrail_agent import (
    _check_for_competitors,
    competitor_blocking_guardrail,
)

# Fixed test competitors list to avoid test changes when competitors.json changes
TEST_COMPETITORS = ["Cooper Station", "EliseAI", "Elise AI"]


@pytest.fixture(autouse=True)
def mock_competitors():
    """Mock the competitors list and case-sensitivity flag with fixed values for testing."""
    with (
        patch(
            "agent_leasing.agent.guardrails.competitor_blocking_guardrail_agent.competitor_blocking_guardrail_agent._competitors",
            TEST_COMPETITORS,
        ),
        patch(
            "agent_leasing.agent.guardrails.competitor_blocking_guardrail_agent.competitor_blocking_guardrail_agent._case_sensitive",
            False,
        ),
    ):
        yield


@pytest.mark.pool(threshold=0.9, name="competitor_blocking")
@pytest.mark.parametrize(
    "output_text,expected_competitors",
    [
        # Test exact competitor names
        ("You should try Cooper Station for that.", ["Cooper Station"]),
        ("EliseAI has that feature.", ["EliseAI"]),
        # Test case insensitivity
        ("cooper station is great", ["Cooper Station"]),
        ("ELISEAI might help", ["EliseAI"]),
        ("elise ai could work", ["Elise AI"]),
        # Test multiple competitors in one response
        ("Both Cooper Station and EliseAI offer that.", ["Cooper Station", "EliseAI"]),
        # Test partial matches (should still trigger)
        ("I heard Cooper Station's platform is good.", ["Cooper Station"]),
    ],
)
def test_violate_competitor_blocking(
    output_text,
    expected_competitors,
):
    """Test that competitor mentions are detected by string matching.

    Calls _check_for_competitors() directly (pure string matching) instead of
    Runner.run() to eliminate LLM non-determinism. No @flaky needed.
    """
    detected = _check_for_competitors(output_text)
    assert detected == expected_competitors


@pytest.mark.pool(threshold=0.9, name="competitor_blocking")
@pytest.mark.parametrize(
    "output_text",
    [
        # Test safe responses (no competitors)
        "I can help you schedule a tour.",
        "Our community has great amenities.",
        "Let me check our availability.",
        "The rent is $1,500 per month.",
        # Test edge cases with similar words (should not trigger)
        "We cooperate with many partners.",
        "The station is nearby.",
        "Elise is a common name.",
        "AI technology is helpful.",
    ],
)
def test_not_violate_competitor_blocking(
    output_text,
):
    """Test that safe outputs do not contain competitor mentions.

    Calls _check_for_competitors() directly (pure string matching) instead of
    Runner.run() to eliminate LLM non-determinism. No @flaky needed.
    """
    detected = _check_for_competitors(output_text)
    assert detected == []


@pytest.mark.pool(threshold=0.9, name="competitor_blocking")
@pytest.mark.parametrize(
    "output_text,language_code,expected_competitors,expected_safe_response",
    [
        (
            "Creo que Elise AI podria ser una buena opcion.",
            "es",
            ["Elise AI"],
            (
                "Lo siento, no pude completar la solicitud anterior. "
                "Estoy aquí para ayudarle con sus necesidades relacionadas con la propiedad. "
                "¿En qué más puedo ayudarle hoy?"
            ),
        ),
        (
            "On m'a dit que la plateforme de Cooper Station est de bonne qualite.",
            "fr",
            ["Cooper Station"],
            (
                "Je suis désolé, je n'ai pas pu compléter la demande précédente. "
                "Je suis ici pour vous aider avec vos besoins liés à la propriété. "
                "Comment puis-je vous aider aujourd'hui?"
            ),
        ),
    ],
)
async def test_violate_competitor_blocking_users_language(
    voice_context,
    output_text,
    language_code,
    expected_competitors,
    expected_safe_response,
):
    """Test that competitor blocking returns the localized safe response."""
    voice_context.reset()
    voice_context.language_code = language_code
    guardrail_context = RunContextWrapper(context=voice_context)
    test_agent = Agent(name="Test Agent", instructions="You are a helpful assistant.")

    guardrail_result = await competitor_blocking_guardrail.run(
        guardrail_context,
        test_agent,
        output_text,
    )

    result = guardrail_result.output.output_info
    assert guardrail_result.output.tripwire_triggered is True
    assert result.is_blocked is True
    assert result.detected_competitors == expected_competitors
    assert result.safe_response == expected_safe_response
