from contextlib import nullcontext

import pytest
from agents import gen_trace_id, get_current_trace, trace
from agents.realtime import RealtimeRunner

from agent_leasing.agent.resident_one_agent.realtime import THINKER_TOOL_NAME, ResidentRealtimeResponderAgent
from tests.integration.agent.resident._test_cases import (
    RESPONSE_CORRECTNESS_TEST_CASES,
    RESPONSE_CORRECTNESS_TEST_CASES_VOICE,
    THINKER_DISPATCH_TEST_CASES,
    VOICE_TRANSFER_HANDOFF_TEST_CASES,
)
from tests.integration.helpers import (
    apply_tool_mocks,
    assert_expected_tool_calls,
    build_realtime_test_model_config,
    extract_ordered_items_from_history,
    filter_expected_tool_calls_for_channel,
    get_multi_turn_response_with_history,
    get_realtime_response,
    get_realtime_response_with_history,
    insert_voice_thinker_run_items_after_thinker,
    mock_enabled_modules_from_disabled_modules,
    patch_context,
)
from tests.integration.langsmith_utils import (
    conditional_langsmith_test_decorator,
    log_test_data,
)

pytestmark = pytest.mark.llm_judge


@conditional_langsmith_test_decorator(
    pytest.mark.parametrize(
        "test_case",
        RESPONSE_CORRECTNESS_TEST_CASES + RESPONSE_CORRECTNESS_TEST_CASES_VOICE,
    )
)
@pytest.mark.pool(threshold=0.9)
async def test_response_correctness_voice(
    semantic_equivalence_judge,
    resident_context_voice_knck,
    helpers,
    test_case,
):
    """Test that ResidentRealtimeResponderAgent returns semantically correct responses for voice channel."""

    test_id = test_case["id"]
    input_text = test_case["input_text"]
    # Voice can opt into a channel-specific expected_output, mirroring non_realtime_flow.
    expected_output = test_case.get("expected_output_voice", test_case["expected_output"])
    expected_score = test_case.get("expected_score", 0.5)
    test_config = test_case.get("test_config")
    expected_tool_calls = filter_expected_tool_calls_for_channel(
        test_case.get("expected_tool_calls"),
        "VOICE",
    )

    trace_id = gen_trace_id()
    with trace(
        f"Resident One Realtime Test Voice {test_id}",
        trace_id=trace_id,
    ):
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
            agent_context = patch_context(resident_context_voice_knck, test_config)
            agent_context.mcp_tool_calls = []
            agent_context.track_voice_thinker_runs = True
            agent_context.voice_thinker_runs = []
            async with ResidentRealtimeResponderAgent(agent_context) as resident_responder_agent:
                runner = RealtimeRunner(resident_responder_agent.agent_instance)
                session_context = await runner.run(
                    context=agent_context,
                    model_config=build_realtime_test_model_config(),
                )

                async with session_context as session:
                    if expected_tool_calls:
                        response_text, history = await get_realtime_response_with_history(session, input_text)
                        ordered_items = extract_ordered_items_from_history(history)
                        ordered_items = insert_voice_thinker_run_items_after_thinker(
                            ordered_items,
                            agent_context.voice_thinker_runs,
                            thinker_tool_name=THINKER_TOOL_NAME,
                            include_outer_thinker_call=False,
                        )
                        assert_expected_tool_calls(ordered_items, expected_tool_calls)
                    else:
                        response_text = await get_realtime_response(session, input_text)

                    print(  # noqa: T201
                        f"comparing actual output '{response_text}' to expected output '{expected_output}'"
                    )

                    await helpers.assert_semantic_equivalence_diff(
                        semantic_equivalence_judge=semantic_equivalence_judge,
                        output=response_text,
                        expected_output=expected_output,
                        expected_score=expected_score,
                    )

                    # storing input, expected output, and actual output for langsmith (if enabled)
                    log_test_data(
                        inputs={"input_text": input_text},
                        reference_outputs={"expected_output": expected_output},
                        outputs={"actual_output": response_text},
                    )


@conditional_langsmith_test_decorator(pytest.mark.parametrize("test_case", THINKER_DISPATCH_TEST_CASES))
@pytest.mark.pool(threshold=0.9)
async def test_thinker_dispatch_on_topic_change(
    resident_context_voice_knck,
    test_case,
):
    """Verify the voice responder calls the thinker on the first response after a topic change.

    The responder must not generate bare acknowledgment turns before dispatching to the thinker.
    Regression test for a production incident where the responder stalled 3 turns before calling
    the thinker after the caller switched from a balance inquiry to a maintenance complaint.
    """
    test_id = test_case["id"]
    input_text = test_case.get("input_text")
    multi_turn_messages = test_case.get("multi_turn_messages")
    expected_tool_calls = test_case.get("expected_tool_calls")
    agent_context = resident_context_voice_knck
    agent_context.mcp_tool_calls = []
    agent_context.voice_thinker_runs = []

    trace_id = gen_trace_id()
    with trace(f"Resident One Realtime Thinker Dispatch {test_id}", trace_id=trace_id):
        current_trace = get_current_trace()
        print(  # noqa: T201
            f"Trace: https://platform.openai.com/traces/trace?trace_id={current_trace.trace_id}"
        )

        async with ResidentRealtimeResponderAgent(agent_context) as resident_responder_agent:
            runner = RealtimeRunner(resident_responder_agent.agent_instance)
            session_context = await runner.run(
                context=agent_context,
                model_config=build_realtime_test_model_config(),
            )

            async with session_context as session:
                if multi_turn_messages:
                    _, history = await get_multi_turn_response_with_history(session, multi_turn_messages)
                else:
                    _, history = await get_realtime_response_with_history(session, input_text)

                ordered_items = extract_ordered_items_from_history(history, multi_turn=bool(multi_turn_messages))
                ordered_items = insert_voice_thinker_run_items_after_thinker(
                    ordered_items,
                    agent_context.voice_thinker_runs,
                    thinker_tool_name=THINKER_TOOL_NAME,
                )

                print(f"Ordered assistant-side items: {ordered_items}")  # noqa: T201

                if expected_tool_calls:
                    assert_expected_tool_calls(ordered_items, expected_tool_calls)

                thinker_indices = [
                    i for i, (t, n) in enumerate(ordered_items) if t == "function_call" and n == THINKER_TOOL_NAME
                ]
                assert thinker_indices, (
                    f"{THINKER_TOOL_NAME} was never called on topic change — responder stalled. "
                    f"Items in order: {ordered_items}"
                )

                turns_before_thinker = sum(1 for t, _ in ordered_items[: thinker_indices[0]] if t == "message")
                assert turns_before_thinker <= 1, (
                    f"Responder generated {turns_before_thinker} message turns before calling the thinker "
                    f"(expected at most 1 transition phrase). Items in order: {ordered_items}"
                )

                log_test_data(
                    inputs={
                        "input_text": str(input_text) if input_text else None,
                        "multi_turn_messages": str(multi_turn_messages) if multi_turn_messages else None,
                    },
                    reference_outputs={"expected_output": "thinker called on first response, no stalling"},
                    outputs={"ordered_items": str(ordered_items)},
                )


TRANSFER_TOOL_NAME = "transfer_to_staff_voice"

# Maximum assistant message turns allowed before transfer_to_staff_voice is called.
# Allows for: transition phrase + thinker relay + transfer transition.
_MAX_TURNS_BEFORE_TRANSFER = 3


@conditional_langsmith_test_decorator(pytest.mark.parametrize("test_case", VOICE_TRANSFER_HANDOFF_TEST_CASES))
@pytest.mark.pool(threshold=0.9)
async def test_voice_transfer_no_looping(
    resident_context_voice_knck,
    test_case,
):
    """Verify transfer_to_staff_voice is called within a bounded number of turns.

    Regression test for KNCK-38978: the responder handled summary collection on its own
    (without re-calling the transfer tool), looped asking for a summary, and the caller
    waited 59 seconds before being transferred.

    Asserts:
    - transfer_to_staff_voice is actually called
    - It is called within _MAX_TURNS_BEFORE_TRANSFER assistant message turns (no looping)
    """
    test_id = test_case["id"]
    input_text = test_case.get("input_text")
    multi_turn_messages = test_case.get("multi_turn_messages")
    expected_tool_calls = test_case.get("expected_tool_calls")
    test_config = test_case.get("test_config")
    match_full_history = test_case.get("match_full_history", False)
    agent_context = patch_context(resident_context_voice_knck, test_config)
    agent_context.mcp_tool_calls = []
    agent_context.voice_thinker_runs = []

    trace_id = gen_trace_id()
    with trace(f"Resident One Realtime Voice Transfer {test_id}", trace_id=trace_id):
        current_trace = get_current_trace()
        print(  # noqa: T201
            f"Trace: https://platform.openai.com/traces/trace?trace_id={current_trace.trace_id}"
        )

        async with ResidentRealtimeResponderAgent(agent_context) as resident_responder_agent:
            runner = RealtimeRunner(resident_responder_agent.agent_instance)
            session_context = await runner.run(
                context=agent_context,
                model_config=build_realtime_test_model_config(),
            )

            async with session_context as session:
                if multi_turn_messages:
                    _, history = await get_multi_turn_response_with_history(session, multi_turn_messages)
                else:
                    _, history = await get_realtime_response_with_history(session, input_text)

                slice_to_last_user = bool(multi_turn_messages) and not match_full_history
                items = extract_ordered_items_from_history(history, multi_turn=slice_to_last_user)
                items = insert_voice_thinker_run_items_after_thinker(
                    items,
                    agent_context.voice_thinker_runs,
                    thinker_tool_name=THINKER_TOOL_NAME,
                )

                print(f"Ordered items: {items}")  # noqa: T201

                if expected_tool_calls:
                    assert_expected_tool_calls(items, expected_tool_calls)

                # Assert transfer_to_staff_voice was called
                transfer_indices = [
                    i for i, (t, n) in enumerate(items) if t == "function_call" and n == TRANSFER_TOOL_NAME
                ]
                assert transfer_indices, (
                    f"{TRANSFER_TOOL_NAME} was never called. "
                    f"The responder should call the transfer tool for handoff requests. "
                    f"Items: {items}"
                )

                # Assert transfer was called within a reasonable number of turns (no looping)
                turns_before_transfer = sum(1 for t, _ in items[: transfer_indices[0]] if t == "message")
                assert turns_before_transfer <= _MAX_TURNS_BEFORE_TRANSFER, (
                    f"Responder generated {turns_before_transfer} message turns before calling "
                    f"{TRANSFER_TOOL_NAME} (max {_MAX_TURNS_BEFORE_TRANSFER}). "
                    f"This indicates the responder may be looping. Items: {items}"
                )

                log_test_data(
                    inputs={
                        "input_text": str(input_text) if input_text else None,
                        "multi_turn_messages": str(multi_turn_messages) if multi_turn_messages else None,
                    },
                    reference_outputs={
                        "expected_output": (f"{TRANSFER_TOOL_NAME} called within {_MAX_TURNS_BEFORE_TRANSFER} turns")
                    },
                    outputs={"ordered_items": str(items)},
                )
