import datetime
import json
import os
import uuid
from copy import deepcopy
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest
from agents import Agent, Runner, get_current_trace, set_tracing_disabled
from dotenv import load_dotenv
from httpx import AsyncClient
from langchain_openai import ChatOpenAI
from langsmith import testing as langsmith_testing
from openai.types.responses import EasyInputMessageParam
from openevals.llm import create_async_llm_as_judge
from openevals.prompts import HALLUCINATION_PROMPT

load_dotenv()

_openai_api_key_set = bool(os.getenv("OPENAI_API_KEY"))
if not _openai_api_key_set:
    os.environ["OPENAI_API_KEY"] = "test"

# Disable langsmith @traceable decorator before importing agent modules.
# The decorator has a signature-inspection bug with Annotated kwargs on CI.
import langsmith  # noqa: E402

_original_traceable = langsmith.traceable
langsmith.traceable = lambda *args, **kwargs: (args[0] if args and callable(args[0]) else lambda fn: fn)

# NOTE: Must set OPENAI_API_KEY before importing agent modules (OpenAI client init).
from agent_leasing.agent.util import SessionScope  # noqa: E402
from agent_leasing.api.model import (  # noqa: E402
    AIConfig,
    AskRequest,
    Product,
    ProductInfo,
    UCReference,
    examples,
)  # noqa: E402
from agent_leasing.settings import settings  # noqa: E402

# Restore original @traceable now that all agent modules are imported.
langsmith.traceable = _original_traceable

if not _openai_api_key_set:
    os.environ.pop("OPENAI_API_KEY", None)


PARALLEL_MARKS: tuple[str, ...] = tuple(f"parallel{index}" for index in range(1, 16))


def pytest_configure(config: pytest.Config) -> None:
    """Register dynamic parallel markers so pytest does not warn during collection."""

    for mark in PARALLEL_MARKS:
        config.addinivalue_line("markers", f"{mark}: auto-assigned shard for CI parallelization")

    # Pool plugin: pool-based pass/fail for non-deterministic (LLM) tests.
    from tests import pool_plugin

    config.pluginmanager.register(pool_plugin)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Distribute collected tests across the configured parallel shards automatically.

    Pool-marked tests are assigned to shards first (one shard per pool) so all
    members of a pool land on the same CI job.  Remaining tests fill in after
    via round-robin.
    """
    from tests.pool_plugin import _pool_name_from_item

    assignable_items = []
    for item in items:
        if item.get_closest_marker("serial"):
            continue
        if any(marker.name in PARALLEL_MARKS for marker in item.iter_markers()):
            continue
        assignable_items.append(item)

    assignable_items.sort(key=lambda item: item.nodeid)

    # Assign pool groups to shards first so every member stays together.
    pool_groups: dict[str, list[pytest.Item]] = {}
    non_pool_items: list[pytest.Item] = []
    for item in assignable_items:
        pool_name = _pool_name_from_item(item)
        if pool_name is not None:
            pool_groups.setdefault(pool_name, []).append(item)
        else:
            non_pool_items.append(item)

    shard_idx = 0
    for _pool_name, pool_items in sorted(pool_groups.items()):
        mark_name = PARALLEL_MARKS[shard_idx % len(PARALLEL_MARKS)]
        for item in pool_items:
            item.add_marker(mark_name)
        shard_idx += 1

    # Round-robin the rest, continuing the counter so shards stay balanced.
    for index, item in enumerate(non_pool_items):
        mark_name = PARALLEL_MARKS[(shard_idx + index) % len(PARALLEL_MARKS)]
        item.add_marker(mark_name)


def _maybe_create_openevals_llm() -> ChatOpenAI | None:
    """Return an OpenAI chat model for openevals if credentials are present."""
    if not os.getenv("OPENAI_API_KEY"):
        return None

    return ChatOpenAI(
        model=os.getenv("OPENEVALS_MODEL", "openai:gpt-4.1-mini").replace("openai:", ""),
        temperature=0,
    )


@pytest.fixture(scope="session")
def openevals_llm():
    llm = _maybe_create_openevals_llm()
    if llm is None:
        pytest.skip("OPENAI_API_KEY not set; skipping tests that require the OpenAI judge.")
    return llm


def _patch_json_encoder_for_mocks() -> None:
    """Allow JSON serialization of mocks in tracing payloads.

    OpenAI Agents SDK tracing serializes rich objects; some test fixtures include
    `Mock`/`MagicMock` instances that are not JSON serializable by default.
    """
    original_default = json.JSONEncoder.default

    def custom_default(self, obj):  # noqa: ANN001
        if isinstance(obj, (Mock, MagicMock)):
            return f"<Mock: {type(obj).__name__}>"

        if hasattr(obj, "__dict__"):
            try:
                return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
            except (TypeError, ValueError):
                return f"<{type(obj).__module__}.{type(obj).__name__} object>"

        try:
            return original_default(self, obj)
        except TypeError:
            return str(obj)

    json.JSONEncoder.default = custom_default


@pytest.fixture(scope="session", autouse=True)
def tests_setup_and_teardown():
    settings.kafka_reporting_enabled = False
    settings.otel_enabled = False

    # Disable LangSmith to prevent 401 errors from invalid/missing API keys.
    # Both the settings and env vars must be cleared because the LangSmith SDK
    # reads LANGSMITH_API_KEY directly from the environment (set by load_dotenv).
    settings.langsmith_tracing = False
    settings.langsmith_api_key = ""
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ.pop("LANGSMITH_API_KEY", None)

    _patch_json_encoder_for_mocks()
    set_tracing_disabled(False)

    # disable MCP authentication for testing
    settings.facilities_mcp_auth_enabled = False
    settings.knock_mcp_auth_enabled = False
    settings.loft_mcp_auth_enabled = False
    settings.onesite_mcp_auth_enabled = False

    # force use of stubbed MCP server during testing
    settings.facilities_mcp_server = "http://127.0.0.1:8042"
    settings.knock_mcp_server = "http://127.0.0.1:8042"
    settings.loft_mcp_server = "http://127.0.0.1:8042"
    settings.onesite_mcp_server = "http://127.0.0.1:8042"

    # disable books authentication for testing
    settings.books_auth_enabled = False
    settings.books_auth_client_secret = "SECRET"

    # defensive session cleanup settings
    settings.max_voice_session_duration_seconds = 3600

    # force use of local emergency dispatch server for testing
    settings.emergency_dispatch_url = "http://localhost:1080/inboundIVR/api/voice/ResAICreateEngineDispatch/PropertyID"

    # force use of local mockserver facilities thinker API for testing
    settings.facilities_thinker_api_host = "http://127.0.0.1:1080"

    yield


@pytest.fixture(scope="session")
def current_time():
    return datetime.datetime(2025, 9, 2, 11, 00)


@pytest.fixture
async def correctness_judge(openevals_llm):
    """
    openevals judge implementation.

    Note how the judge's prompt does not have to specify the type of score, nor whether to include reasoning. That is handled by openevals.
    Note also how the judge's prompt automatically interpolates outputs, reference_outputs (it could also use inputs, if needed).
    """
    prompt = """
    Grade the following ANSWER.
    The ANSWER should be correct and factually accurate.  Use the REFERENCE_ANSWER to determine correctness.
    If the ANSWER is correct, return a score of 1.0.
    If the ANSWER is incorrect, return a score of 0.0.
    Borderline cases or partially correct answers should be scored in between.

    <ANSWER>{outputs}</ANSWER><REFERENCE_ANSWER>{reference_outputs}</REFERENCE_ANSWER>.
    """
    return create_async_llm_as_judge(
        prompt=prompt,
        feedback_key="correctness",  # This is used to post the feedback to LangSmith.
        use_reasoning=True,
        continuous=True,
        model=openevals_llm,
    )


@pytest.fixture
async def semantic_equivalence_judge(openevals_llm):
    prompt = """
    You are a judge who grades the similarity between an expected response and an actual response.
    Additional information in a response should not detract from the score.
    The structure of the response is not important; it's the data in the response that matters.
    If the expected outputs are a list, the actual output could match ANY of the expected outputs.
    <ACTUAL>{outputs}</ACTUAL><EXPECTED>{reference_outputs}</EXPECTED>
    """
    return create_async_llm_as_judge(
        prompt=prompt,
        feedback_key="semantic_equivalence",  # This is used to post the feedback to LangSmith.
        use_reasoning=True,
        continuous=True,
        judge=openevals_llm,
    )


@pytest.fixture
async def hallucination_judge(openevals_llm):
    """openevals hallucination judge."""
    return create_async_llm_as_judge(
        prompt=HALLUCINATION_PROMPT,
        feedback_key="hallucination",  # This is used to post the feedback to LangSmith.
        use_reasoning=True,
        continuous=True,
        judge=openevals_llm,
    )


async def assert_semantic_equivalence(
    aclient: AsyncClient,
    semantic_equivalence_judge,
    ask_request: AskRequest,
    input: str,
    expected_output: str,
    expected_score: float = 0.4,
):
    """Convenience test method for simple cases."""
    ask_request.prompt = input
    response = await aclient.post("/v1/agent/ask", json=ask_request.model_dump())
    try:
        evaluator_result = await semantic_equivalence_judge(outputs=response.text, reference_outputs=expected_output)
        score = evaluator_result["score"]
        if score < expected_score:
            message = f"Score too low: {score:.2f}. Output: {response.text}. Expected Output: {expected_output}. Reason: {evaluator_result['comment']}\nTrace: https://platform.openai.com/traces/trace?trace_id={get_current_trace().trace_id}"
            assert False, message
    except KeyError:
        # Sometimes openevals fails with a KeyError because there is no score, but that's not our problem
        pass


async def assert_semantic_equivalence_multi_turn(
    aclient: AsyncClient,
    semantic_equivalence_judge,
    ask_request: AskRequest,
    input: str,
    expected_output: str,
    expected_score: float = 0.5,
    reset_history: bool = True,
):
    """Convenience test method for multi-turn cases."""
    if reset_history:
        ask_request.chat_session_id = uuid.uuid4().hex  # Reset history
    ask_request.prompt = input
    response = await aclient.post("/v1/agent/ask", json=ask_request.model_dump())
    try:
        evaluator_result = await semantic_equivalence_judge(outputs=response.text, reference_outputs=expected_output)
        score = evaluator_result["score"]
        if score < expected_score:
            message = f"Score too low: {score:.2f}. Output: {response.text}. Expected Output: {expected_output}. Reason: {evaluator_result['comment']}\nTrace: https://platform.openai.com/traces/trace?trace_id={get_current_trace().trace_id}"
            assert False, message
    except KeyError:
        # Sometimes openevals fails with a KeyError because there is no score, but that's not our problem
        pass


async def assert_semantic_equivalence_diff(
    semantic_equivalence_judge,
    output: str | dict,
    expected_output: str | dict,
    expected_score: float = 0.5,
):
    """Convenience test method for simple cases."""
    await assert_semantic_equivalence_diff_multi_turn(
        semantic_equivalence_judge, [output], [expected_output], expected_score
    )


async def assert_semantic_equivalence_diff_multi_turn(
    semantic_equivalence_judge,
    outputs: list,
    expected_outputs: list,
    expected_score: float = 0.5,
):
    """Convenience test method for simple cases."""
    try:
        evaluator_result = await semantic_equivalence_judge(outputs=outputs, reference_outputs=expected_outputs)
        score = evaluator_result["score"]
        if score < expected_score:
            message = f"Score too low: {score:.2f}. Output: {outputs}. Expected Output: {expected_outputs}. Reason: {evaluator_result['comment']}\nTrace: https://platform.openai.com/traces/trace?trace_id={get_current_trace().trace_id}"
            assert False, message
    except KeyError:
        # Sometimes openevals fails with a KeyError because there is no score, but that's not our problem
        pass


async def assert_semantic_equivalence_diff_multi_turn_pairs(
    semantic_equivalence_judge,
    agent: Agent,
    context: Any,
    input_output_pairs: list[tuple[str]],
    expected_score: float = 0.5,
):
    """
    Convenience test method for multi-turn tests.

    input_output_pairs contains tuples of (input, expected_output) pairs.

    If `LANGSMITH_TRACING`, `LANGSMITH_ENDPOINT`, and `LANGSMITH_API_KEY` are set, the outputs
    will be logged to langsmith.
    """
    output_items = []
    input_items = []
    user_inputs = []
    reference_outputs = []
    for inx, pair in enumerate(input_output_pairs, start=1):
        input = pair[0]
        user_inputs.append(input)
        reference_output = pair[1]
        reference_outputs.append(reference_output)
        input_items.append(EasyInputMessageParam(content=input, role="user"))
        result = await Runner.run(agent, input, context=context)
        output_items.append(result.final_output)
        input_items = result.to_input_list()
    try:
        evaluator_result = await semantic_equivalence_judge(outputs=output_items, reference_outputs=reference_outputs)

        if (
            os.getenv("LANGSMITH_TRACING", "false").lower() == "true"
            and os.getenv("LANGSMITH_ENDPOINT")
            and os.getenv("LANGSMITH_API_KEY")
        ):
            langsmith_testing.log_inputs({"inputs": user_inputs})
            langsmith_testing.log_reference_outputs({"response": reference_outputs})
            langsmith_testing.log_outputs({"response": output_items})

        score = evaluator_result["score"]
        if score < expected_score:
            message = f"Score too low: {score:.2f}. Input: {input_items}. Expected Output: {reference_outputs}. Actual Output: {output_items}. Reason: {evaluator_result['comment']}\nTrace: https://platform.openai.com/traces/trace?trace_id={get_current_trace().trace_id}"
            assert False, message
    except KeyError:
        # Sometimes openevals fails with a KeyError because there is no score, but that's not our problem
        pass


@pytest.fixture
def helpers():
    return Helpers


class Helpers:
    @staticmethod
    async def assert_semantic_equivalence_diff(
        semantic_equivalence_judge,
        output: str,
        expected_output: str,
        expected_score: float = 0.5,
    ):
        """Convenience test method for simple cases."""
        return await assert_semantic_equivalence_diff(
            semantic_equivalence_judge, output, expected_output, expected_score
        )

    @staticmethod
    async def assert_semantic_equivalence_diff_multi(
        semantic_equivalence_judge,
        output: str,
        expected_output: str,
        expected_score: float = 0.5,
    ):
        """Convenience test method for simple cases."""
        return await assert_semantic_equivalence_diff_multi_turn(
            semantic_equivalence_judge, output, expected_output, expected_score
        )

    @staticmethod
    async def assert_semantic_equivalence_diff_multi_pairs(
        semantic_equivalence_judge,
        agent: Agent,
        context: Any,
        input_output_pairs: list[tuple[str]],
        expected_score: float = 0.5,
    ):
        """Convenience test method for simple cases."""
        return await assert_semantic_equivalence_diff_multi_turn_pairs(
            semantic_equivalence_judge,
            agent,
            context,
            input_output_pairs,
            expected_score,
        )


@pytest.fixture(scope="function")
def resident_context(ask_request_resident, current_time) -> SessionScope:
    """Context for resident agent."""
    return SessionScope(current_time=current_time, ask_request=ask_request_resident)


@pytest.fixture(scope="function")
def resident_context_chat_ll(ask_request_resident_chat_ll, current_time) -> SessionScope:
    """Context for resident agent chat LL."""
    return SessionScope(current_time=current_time, ask_request=ask_request_resident_chat_ll)


@pytest.fixture(scope="function")
def resident_context_unified_chat_ll(ask_request_resident_chat_ll, current_time) -> SessionScope:
    """Context for unified resident agent chat LL."""
    ask_request_resident_unified_chat_ll = deepcopy(ask_request_resident_chat_ll)
    ask_request_resident_unified_chat_ll.product = Product.RESIDENT_ONE_CHAT.value
    return SessionScope(current_time=current_time, ask_request=ask_request_resident_unified_chat_ll)


@pytest.fixture(scope="function")
def resident_context_sms_knck(ask_request_resident_sms_knck, current_time) -> SessionScope:
    """Context for resident agent SMS KNCK."""
    return SessionScope(current_time=current_time, ask_request=ask_request_resident_sms_knck)


@pytest.fixture(scope="function")
def resident_context_sms_ll(ask_request_resident_sms_ll, current_time) -> SessionScope:
    """Context for resident agent SMS LL."""
    return SessionScope(current_time=current_time, ask_request=ask_request_resident_sms_ll)


@pytest.fixture(scope="function")
def resident_context_email_knck(
    ask_request_resident_email_knck,
    current_time,
) -> SessionScope:
    """Context for resident agent email KNCK."""
    return SessionScope(
        current_time=current_time,
        ask_request=ask_request_resident_email_knck,
    )


@pytest.fixture(scope="function")
def resident_context_email_ll(
    ask_request_resident_email_ll,
    current_time,
) -> SessionScope:
    """Context for resident agent email LL."""
    return SessionScope(
        current_time=current_time,
        ask_request=ask_request_resident_email_ll,
    )


@pytest.fixture(scope="function")
def resident_context_voice_knck(ask_request_resident_voice_knck, current_time) -> SessionScope:
    """Context for resident agent voice KNCK.

    Pre-verified for VOICE because voice instructions mandate asking for
    verification in every turn regardless of inline credentials, which causes
    shared-list tests (that provide inline verification) to fail
    deterministically on voice.  Voice-specific verification tests use
    multi-turn conversation history and are unaffected by this default.
    """
    ctx = SessionScope(current_time=current_time, ask_request=ask_request_resident_voice_knck)
    ctx.set_identity_verified("VOICE")
    ctx.set_identity_verified_with_birth_year("VOICE")
    return ctx


@pytest.fixture(scope="function")
def voice_context(ask_request_resident_voice_knck, current_time) -> SessionScope:
    """Context for voice."""
    return SessionScope(current_time=current_time, ask_request=ask_request_resident_voice_knck)


@pytest.fixture(scope="function")
def ask_request_simple() -> AskRequest:
    """Settings for simple agent"""
    return AskRequest(
        chat_session_id=uuid.uuid4().hex,
        product=Product.SIMPLE.value,
        product_info=ProductInfo(
            knock_property_id="123",
            ai_config=AIConfig(pna_va_enabled=False),
            knock_prospect_id="1",
        ),
    )


@pytest.fixture(scope="function")
def ask_request_resident() -> AskRequest:
    """Settings for resident agent"""
    return AskRequest(
        chat_session_id=uuid.uuid4().hex,
        product=Product.RESIDENT_ONE_CHAT.value,
        product_info=ProductInfo(
            knock_property_id="21521",
            knock_resident_id="1",
            uc_portal_base_url="https://cassidysouth.qa1.loftliving.com",
            ai_config=AIConfig(pna_va_enabled=False),
            uc_resident_member_id=UCReference(id=1, source=""),
            uc_resident_household_id=UCReference(id=2, source=""),
            uc_company_id=UCReference(id=3, source=""),
            uc_property_id=UCReference(id=4, source=""),
            ab_resident_id=UCReference(id="res_1", source="AB"),
            uc_lease_id=UCReference(id=6, source=""),
        ),
    )


@pytest.fixture(scope="function")
def ask_request_resident_chat_ll() -> AskRequest:
    """Settings for resident agent chat LL."""
    return AskRequest(**examples.ASK_REQUEST_RESIDENT_CHAT_LL)


@pytest.fixture(scope="function")
def ask_request_resident_sms_knck() -> AskRequest:
    """Settings for resident agent SMS KNCK."""
    return AskRequest(**examples.ASK_REQUEST_RESIDENT_SMS_KNCK)


@pytest.fixture(scope="function")
def ask_request_resident_sms_ll() -> AskRequest:
    """Settings for resident agent SMS LL."""
    return AskRequest(**examples.ASK_REQUEST_RESIDENT_SMS_LL)


@pytest.fixture(scope="function")
def ask_request_resident_email_knck() -> AskRequest:
    """Settings for resident agent email KNCK."""
    return AskRequest(**examples.ASK_REQUEST_RESIDENT_EMAIL_KNCK)


@pytest.fixture(scope="function")
def ask_request_resident_email_ll() -> AskRequest:
    """Settings for resident agent email LL."""
    return AskRequest(**examples.ASK_REQUEST_RESIDENT_EMAIL_LL)


@pytest.fixture(scope="function")
def ask_request_resident_voice_knck() -> AskRequest:
    """Settings for resident agent voice KNCK."""
    return AskRequest(**examples.ASK_REQUEST_RESIDENT_VOICE_KNCK)
