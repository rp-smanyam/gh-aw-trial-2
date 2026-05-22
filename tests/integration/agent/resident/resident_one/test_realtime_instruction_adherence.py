import os

import pytest
from agents import gen_trace_id, get_current_trace, trace
from agents.realtime import RealtimeRunner
from openevals.llm import create_async_llm_as_judge

from agent_leasing.agent.resident_one_agent.realtime import ResidentRealtimeResponderAgent
from tests.integration.agent.resident._test_cases import (
    INSTRUCTION_ADHERENCE_TEST_CASES,
)
from tests.integration.helpers import (
    build_realtime_test_model_config,
    get_realtime_response,
)
from tests.integration.langsmith_utils import (
    conditional_langsmith_test_decorator,
    log_test_data,
)

pytestmark = pytest.mark.llm_judge


@conditional_langsmith_test_decorator(pytest.mark.parametrize("test_case", INSTRUCTION_ADHERENCE_TEST_CASES))
@pytest.mark.pool(threshold=0.9)
async def test_instruction_adherence(
    resident_context_chat_ll,
    helpers,
    test_case,
):
    test_id = test_case["id"]
    prompt = test_case["prompt"]
    input_text = test_case["input_text"]
    expected_output = test_case["expected_output"]
    expected_score = test_case.get("expected_score", 0.5)

    custom_judge = create_async_llm_as_judge(
        prompt=prompt,
        feedback_key="no_links",  # This is used to post the feedback to LangSmith.
        use_reasoning=True,
        continuous=True,
        model=os.getenv("OPENEVALS_MODEL", "openai:gpt-4o"),
    )

    trace_id = gen_trace_id()
    with trace(f"Resident One Realtime Instruction Adherence Test {test_id}", trace_id=trace_id):
        current_trace = get_current_trace()
        print(  # noqa: T201
            f"Trace: https://platform.openai.com/traces/trace?trace_id={current_trace.trace_id}"
        )
        async with ResidentRealtimeResponderAgent(resident_context_chat_ll) as resident_agent:
            runner = RealtimeRunner(resident_agent.agent_instance)
            session_context = await runner.run(
                context=resident_context_chat_ll,
                model_config=build_realtime_test_model_config(),
            )

            async with session_context as session:
                response_text = await get_realtime_response(session, input_text)

                print(  # noqa: T201
                    f"comparing actual output '{response_text}' to expected output '{expected_output}'"
                )

            await helpers.assert_semantic_equivalence_diff(
                semantic_equivalence_judge=custom_judge,
                output=response_text,
                expected_output=expected_output,
                expected_score=expected_score,
            )

            log_test_data(
                inputs={"input_text": input_text},
                reference_outputs={"expected_output": expected_output},
                outputs={"actual_output": response_text},
            )
