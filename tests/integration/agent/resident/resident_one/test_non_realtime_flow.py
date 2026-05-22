from contextlib import nullcontext

import pytest
from agents import (
    gen_trace_id,
    get_current_trace,
    trace,
)

from agent_leasing.agent.resident_one_agent.agent import ResidentAgent
from tests.integration.agent.resident._test_cases import (
    RESPONSE_CORRECTNESS_TEST_CASES,
    RESPONSE_CORRECTNESS_TEST_CASES_CHAT,
    RESPONSE_CORRECTNESS_TEST_CASES_EMAIL,
    RESPONSE_CORRECTNESS_TEST_CASES_SMS,
)
from tests.integration.helpers import (
    apply_tool_mocks,
    assert_expected_tool_calls,
    extract_ordered_items_from_run_result,
    filter_expected_tool_calls_for_channel,
    mock_enabled_modules_from_disabled_modules,
    patch_context,
    run_agent_with_guardrails,
)
from tests.integration.langsmith_utils import (
    conditional_langsmith_test_decorator,
    log_test_data,
)

pytestmark = pytest.mark.llm_judge


async def _run_response_correctness_test(
    agent_context,
    test_case,
    semantic_equivalence_judge,
    helpers,
    channel_name: str,
):
    """Shared helper for response correctness tests across channels."""
    test_id = test_case["id"]
    input_text = test_case["input_text"]
    expected_output = test_case.get(f"expected_output_{channel_name.lower()}", test_case["expected_output"])
    expected_score = test_case.get("expected_score", 0.5)
    test_config = test_case.get("test_config")
    expected_tool_calls = filter_expected_tool_calls_for_channel(
        test_case.get("expected_tool_calls"),
        channel_name,
    )

    trace_id = gen_trace_id()
    with trace(f"Resident Responder Correctness Test {channel_name} {test_id}", trace_id=trace_id):
        current_trace = get_current_trace()
        print(  # noqa: T201
            f"Trace: https://platform.openai.com/traces/trace?trace_id={current_trace.trace_id}"
        )
        disabled_modules = test_case.get("disabled_modules")
        tool_mocks = test_case.get("tool_mocks")
        modules_mock = (
            mock_enabled_modules_from_disabled_modules(disabled_modules)
            if disabled_modules is not None
            else nullcontext()
        )
        tool_mocks_context = apply_tool_mocks(tool_mocks) if tool_mocks is not None else nullcontext()
        with modules_mock, tool_mocks_context:
            patched_context = patch_context(agent_context, test_config)
            async with ResidentAgent(patched_context) as resident_agent:
                output_text, result = await run_agent_with_guardrails(
                    resident_agent.agent_instance,
                    input_text,
                    patched_context,
                    return_result=True,
                )

                if expected_tool_calls:
                    assert result is not None, (
                        "Expected tool calls require a successful RunResult, got guardrail output."
                    )
                    ordered_items = extract_ordered_items_from_run_result(result)
                    assert_expected_tool_calls(ordered_items, expected_tool_calls)

                await helpers.assert_semantic_equivalence_diff(
                    semantic_equivalence_judge=semantic_equivalence_judge,
                    output=output_text,
                    expected_output=expected_output,
                    expected_score=expected_score,
                )

                log_test_data(
                    inputs={"input_text": input_text},
                    reference_outputs={"expected_output": expected_output},
                    outputs={"actual_output": output_text},
                )


@conditional_langsmith_test_decorator(
    pytest.mark.parametrize(
        "test_case",
        RESPONSE_CORRECTNESS_TEST_CASES + RESPONSE_CORRECTNESS_TEST_CASES_CHAT,
    )
)
@pytest.mark.pool(threshold=0.9)
async def test_response_correctness_chat(
    semantic_equivalence_judge,
    resident_context_unified_chat_ll,
    helpers,
    test_case,
):
    """Test response correctness for CHAT channel."""
    await _run_response_correctness_test(
        resident_context_unified_chat_ll,
        test_case,
        semantic_equivalence_judge,
        helpers,
        "CHAT",
    )


@conditional_langsmith_test_decorator(
    pytest.mark.parametrize(
        "test_case",
        RESPONSE_CORRECTNESS_TEST_CASES + RESPONSE_CORRECTNESS_TEST_CASES_SMS,
    )
)
@pytest.mark.pool(threshold=0.9)
async def test_response_correctness_sms(
    semantic_equivalence_judge,
    resident_context_sms_ll,
    helpers,
    test_case,
):
    """Test response correctness for SMS channel."""
    await _run_response_correctness_test(
        resident_context_sms_ll,
        test_case,
        semantic_equivalence_judge,
        helpers,
        "SMS",
    )


@conditional_langsmith_test_decorator(
    pytest.mark.parametrize(
        "test_case",
        RESPONSE_CORRECTNESS_TEST_CASES + RESPONSE_CORRECTNESS_TEST_CASES_EMAIL,
    )
)
@pytest.mark.pool(threshold=0.9)
async def test_response_correctness_email(
    semantic_equivalence_judge,
    resident_context_email_ll,
    helpers,
    test_case,
):
    """Test response correctness for EMAIL channel."""
    await _run_response_correctness_test(
        resident_context_email_ll,
        test_case,
        semantic_equivalence_judge,
        helpers,
        "EMAIL",
    )
