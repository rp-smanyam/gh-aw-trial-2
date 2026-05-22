import pytest
from agents import (
    gen_trace_id,
    get_current_trace,
    trace,
)

from agent_leasing.agent.resident_one_agent.agent import ResidentAgent
from tests.integration.agent.resident._test_cases import LEASE_RENEWAL_TEST_CASES
from tests.integration.helpers import run_agent_with_guardrails
from tests.integration.langsmith_utils import (
    conditional_langsmith_test_decorator,
    log_test_data,
)

pytestmark = pytest.mark.llm_judge


@conditional_langsmith_test_decorator(
    pytest.mark.parametrize(
        "test_case",
        LEASE_RENEWAL_TEST_CASES,
    )
)
@pytest.mark.pool(threshold=0.9)
async def test_lease_renewal_chat(
    semantic_equivalence_judge,
    resident_context_unified_chat_ll,
    helpers,
    test_case,
):
    """Test that the agent correctly handles lease renewal requests in chat channel.

    This test verifies that:
    - Explicit renewal requests automatically receive the portal link
    - Implicit renewal intent triggers a confirmation prompt
    - Various phrasings of renewal requests are correctly detected
    """

    test_id = test_case["id"]
    input_text = test_case["input_text"]
    expected_output = test_case["expected_output"]
    expected_score = test_case.get("expected_score", 0.5)

    trace_id = gen_trace_id()
    with trace(f"Lease Renewal Test Chat {test_id}", trace_id=trace_id):
        current_trace = get_current_trace()
        print(  # noqa: T201
            f"Trace: https://platform.openai.com/traces/trace?trace_id={current_trace.trace_id}"
        )
        async with ResidentAgent(resident_context_unified_chat_ll) as resident_agent:
            output_text = await run_agent_with_guardrails(
                resident_agent.agent_instance,
                input_text,
                resident_context_unified_chat_ll,
            )

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
