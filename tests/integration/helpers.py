import asyncio
import json
from contextlib import ExitStack
from typing import Any
from unittest import mock

import structlog
from agents import (
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    Runner,
)
from agents.realtime import RealtimeModelConfig
from agents.realtime.session import RealtimeSession

from agent_leasing.settings import settings

logger = structlog.get_logger(__name__)

MAX_WAIT_FOR_TOOL_OR_AGENT = 60.0


def build_realtime_test_model_config() -> RealtimeModelConfig | None:
    """Build realtime model config for integration tests.

    Local test runs need an explicit websocket URL when using regional OpenAI
    endpoints. The SDK's default realtime transport ignores the AsyncOpenAI
    client's HTTP base_url and otherwise falls back to the global websocket host.
    """
    config: RealtimeModelConfig = {}

    if settings.openai_api_key:
        config["api_key"] = settings.openai_api_key

    if settings.openai_base_wss_url:
        config["url"] = settings.openai_wss_full_endpoint

    return config or None


def patch_context(context: Any, test_config: dict[str, Any] | None) -> Any:
    """Apply a per-test config dict to a context object.

    Keys may be dotted attribute paths (e.g. "ask_request.product_info.dispatch_schedule_active").
    This mutates `context` and returns it for convenience.
    """
    if not test_config:
        return context

    for path, value in test_config.items():
        _set_path_value(context, path, value)

    return context


def _set_path_value(target: Any, path: str, value: Any) -> None:
    parts = [part for part in str(path).split(".") if part]
    if not parts:
        raise ValueError("test_config key must be a non-empty string")

    current = target
    for part in parts[:-1]:
        if isinstance(current, dict):
            current = current[part]
        else:
            current = getattr(current, part)

    leaf = parts[-1]
    if isinstance(current, dict):
        current[leaf] = value
    else:
        setattr(current, leaf, value)


def mock_enabled_modules_from_disabled_modules(disabled_modules: list[str] | None = None):
    """Patch LDP module fetching to simulate disabled modules in tests.

    This patches `agent_leasing.clients.ldp.fetch_ldp_property_data` (async) to return
    a dict with `ALL_MODULES` minus `disabled_modules`, along with default PTE and summary values.
    """
    from agent_leasing.clients.ldp import ALL_MODULES

    enabled_modules = [module for module in ALL_MODULES if module not in (disabled_modules or [])]
    return mock.patch(
        "agent_leasing.clients.ldp.fetch_ldp_property_data",
        return_value={"enabled_modules": enabled_modules, "pte_setting": False, "resident_summary": None},
    )


def apply_tool_mocks(tool_mocks: dict[str, dict[str, Any]] | None):
    """Create a composed context manager for mocking local and MCP tools.

    Expected shape:
        tool_mocks = {
            "local:transfer_to_staff_voice": {"return_value": "Call transferred successfully."},
            "local:transfer_to_staff_voice": {"error": "Transfer failed."},
            "mcp:loft:get_residents_packages": {"error": "Connection failed"},
            "mcp:onesite:get_rent_information": {"return_value": {"rent_amount": 1234}},
        }

    Rules:
    - Local tools must be prefixed with "local:" and reference a FunctionTool name.
    - MCP tools must be prefixed with "mcp:<server>:<tool>" where <server> is matched as a
      case-insensitive substring against the MCP server's configured name.
    - For MCP tools, "return_value" is converted into a CallToolResult.
    """
    if not tool_mocks:
        return ExitStack()

    actionable_mocks = _filter_actionable_tool_mocks(tool_mocks)
    local_mocks, mcp_mocks = _partition_tool_mocks(actionable_mocks)

    stack = ExitStack()
    if local_mocks:
        stack.enter_context(_patch_local_tools(local_mocks))
    if mcp_mocks:
        stack.enter_context(_patch_mcp_tools(mcp_mocks))
    return stack


def _filter_actionable_tool_mocks(tool_mocks: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Keep only mocks that specify a behavior."""
    return {
        name: cfg
        for name, cfg in tool_mocks.items()
        if isinstance(cfg, dict) and ("error" in cfg or "return_value" in cfg)
    }


def _partition_tool_mocks(tool_mocks: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict]]:
    """Split mocks into local and MCP buckets."""
    local_mocks: dict[str, dict[str, Any]] = {}
    mcp_mocks: dict[str, dict[str, dict[str, Any]]] = {}

    for full_name, cfg in tool_mocks.items():
        if full_name.startswith("local:"):
            local_mocks[full_name.removeprefix("local:")] = cfg
            continue

        if full_name.startswith("mcp:"):
            _, server, tool = full_name.split(":", maxsplit=2)
            mcp_mocks.setdefault(server, {})[tool] = cfg
            continue

        raise ValueError(f"Invalid tool mock key '{full_name}'. Use 'local:' or 'mcp:<server>:' prefixes.")

    return local_mocks, mcp_mocks


def _patch_local_tools(local_mocks: dict[str, dict[str, Any]]):
    """Patch local FunctionTools by replacing their on_invoke_tool handler."""
    from agents.tool import FunctionTool

    import agent_leasing.agent.tools as tools_module

    stack = ExitStack()

    for tool_name, cfg in local_mocks.items():
        tool_obj = getattr(tools_module, tool_name, None)
        if tool_obj is None:
            raise ValueError(f"Unknown local tool '{tool_name}'.")
        if not isinstance(tool_obj, FunctionTool):
            raise TypeError(f"Local tool '{tool_name}' is not a FunctionTool (got {type(tool_obj)}).")

        async def _mocked_on_invoke_tool(tool_context, args_json, *, _cfg=cfg):  # noqa: ARG001
            if "error" in _cfg:
                if _cfg.get("raise", False):
                    raise Exception(_cfg["error"])
                return str(_cfg["error"])
            return _cfg.get("return_value", "")

        stack.enter_context(mock.patch.object(tool_obj, "on_invoke_tool", new=_mocked_on_invoke_tool))

    return stack


def _patch_mcp_tools(mcp_mocks: dict[str, dict[str, dict[str, Any]]]):
    """Patch MCP tool calls by intercepting CachingMCPServer._run_mcp_tool."""
    from mcp.types import CallToolResult, TextContent

    from agent_leasing.clients.mcp import CachingMCPServer

    original_run_mcp_tool = CachingMCPServer._run_mcp_tool

    def _server_matches(pattern: str, server_name: str) -> bool:
        pattern = (pattern or "").strip().lower()
        if not pattern:
            return False
        if pattern == "*":
            return True
        return pattern in server_name.lower()

    def _select_mock_config(server_name: str, tool_name: str) -> dict[str, Any] | None:
        exact_matches = [pattern for pattern in mcp_mocks if pattern == server_name]
        patterns = exact_matches or [pattern for pattern in mcp_mocks if _server_matches(pattern, server_name)]
        if not patterns:
            return None

        selected_pattern = max(patterns, key=len)
        return mcp_mocks.get(selected_pattern, {}).get(tool_name)

    def _to_call_tool_result(value: Any) -> CallToolResult:
        if isinstance(value, CallToolResult):
            return value
        if isinstance(value, dict):
            return CallToolResult(
                structuredContent=value,
                content=[TextContent(text=json.dumps(value), type="text")],
                isError=False,
            )
        return CallToolResult(content=[TextContent(text=str(value), type="text")], isError=False)

    async def _mocked_run_mcp_tool(self, tool_name: str, arguments: dict[str, Any], **kwargs):  # noqa: ANN001
        # Track MCP call in context BEFORE checking mock config —
        # mocked calls still need to appear in context.mcp_tool_calls for assertion extraction.
        if self.context:
            from datetime import datetime

            self.context.mcp_tool_calls.append(
                {
                    "mcp_server": self.name,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "timestamp": datetime.now().isoformat(),
                }
            )

        cfg = _select_mock_config(self.name, tool_name)
        if cfg:
            if "error" in cfg:
                raise Exception(cfg["error"])
            if "return_value" in cfg:
                return _to_call_tool_result(cfg["return_value"])
        return await original_run_mcp_tool(self, tool_name, arguments, **kwargs)

    return mock.patch("agent_leasing.clients.mcp.CachingMCPServer._run_mcp_tool", new=_mocked_run_mcp_tool)


async def run_agent_with_guardrails(agent, input_text: str, context, *, return_result: bool = False):
    """Run an agent with Runner.run and handle guardrail exceptions gracefully.

    If a guardrail trips, this function returns the safe_response instead of
    raising an exception, allowing tests to continue and validate the response.

    Args:
        agent: The agent instance to run
        input_text: The input text to send to the agent
        context: The session context for the agent
        return_result: When True, also return the underlying RunResult

    Returns:
        The response text from the agent (either normal response or safe_response),
        and optionally the underlying RunResult.
    """
    try:
        result = await Runner.run(
            agent,
            input_text,
            context=context,
        )
        # Handle both string outputs and structured outputs with .response attribute
        if isinstance(result.final_output, str):
            output_text = result.final_output
        else:
            output_text = result.final_output.response
        if return_result:
            return output_text, result
        return output_text
    except (
        InputGuardrailTripwireTriggered,
        OutputGuardrailTripwireTriggered,
    ) as exc:
        safe_response = exc.guardrail_result.output.output_info.safe_response
        if return_result:
            return safe_response, None
        return safe_response


async def get_realtime_response(session: RealtimeSession, input_text: str | list, timeout: float = 10.0) -> str:
    """
    Wrapper function to handle retries for getting a response from a realtime session.
    Sometimes the session times out and returns an empty response, so we retry with different timeouts.
    """
    result = ""
    for timeout in [1.0, 5.0, 10.0, 60.0]:
        # converting input_text to string to allow this to work with lists of messages
        # specifically, for multi-turn tests
        result = await _get_realtime_response(session, str(input_text), timeout)
        if result != "":
            return result

    return result


async def get_realtime_response_with_history(
    session: RealtimeSession, input_text: str | list, timeout: float = 10.0
) -> tuple[str, list]:
    """Like get_realtime_response but also returns the raw history.

    The history contains ordered RealtimeMessageItem and RealtimeToolCallItem entries.
    Tool call items have ``item.type == "function_call"`` and ``item.name`` for the tool name,
    allowing callers to assert which tools were called and in what order.
    """
    history: list = []
    result = ""
    for t in [1.0, 5.0, 10.0, 60.0]:
        result, history = await _get_realtime_response_with_history(session, str(input_text), t)
        if result != "":
            return result, history

    return result, history


async def _get_realtime_response_with_history(
    session: RealtimeSession, input_text: str, timeout: float = 10.0
) -> tuple[str, list]:
    """Send a single message and collect events until idle. Returns (response_text, history)."""
    await session.send_message(input_text)
    return await _collect_realtime_events(session, timeout)


async def _collect_realtime_events(session: RealtimeSession, timeout: float = 10.0) -> tuple[str, list]:
    """Consume session events until idle timeout. Does NOT send a message."""
    iterator = session.__aiter__()

    tool_is_running = False
    agent_is_running = False
    history = []

    while True:
        try:
            wait_longer = tool_is_running or agent_is_running
            current_timeout = timeout if not wait_longer else MAX_WAIT_FOR_TOOL_OR_AGENT
            event = await asyncio.wait_for(iterator.__anext__(), timeout=current_timeout)
            logger.info(f"event.type: {event.type}\n\tevent.data: {event.data if hasattr(event, 'data') else None}")
            if event.type == "history_updated" and event.history:
                history = event.history
            elif event.type == "agent_start":
                agent_is_running = True
            elif event.type == "agent_end":
                agent_is_running = False
            elif event.type == "tool_start":
                tool_is_running = True
            elif event.type == "tool_end":
                tool_is_running = False

        except (StopAsyncIteration, asyncio.TimeoutError):
            logger.info("Timeout waiting for events")
            break

    responses = _extract_assistant_responses_from_history(history)

    if responses == "":
        logger.warning(f"No assistant responses received from session\nhistory: {history}")
    return responses, history


async def get_multi_turn_response_with_history(session: RealtimeSession, messages: list[str]) -> tuple[str, list]:
    """Send multiple user messages one at a time and return the final response with full history.

    Each message is sent exactly once. The function waits for a new assistant response
    before proceeding to the next message, retrying with escalating timeouts.
    Raises AssertionError if no new assistant response arrives for any message.
    """
    history: list = []
    prev_assistant_count = 0

    for msg in messages:
        await session.send_message(msg)
        got_response = False
        for t in [1.0, 5.0, 10.0, 60.0]:
            _, history = await _collect_realtime_events(session, t)
            current_assistant_count = sum(
                1 for item in history if item.type == "message" and getattr(item, "role", None) == "assistant"
            )
            if current_assistant_count > prev_assistant_count:
                prev_assistant_count = current_assistant_count
                got_response = True
                break
        assert got_response, f"No assistant response received for message: {msg!r}"

    last_response = _extract_assistant_responses_from_history(history)
    return last_response, history


async def _get_realtime_response(session: RealtimeSession, input_text: str, timeout: float = 10.0) -> str:
    """Helper function to send a message to a realtime session and extract the text response."""
    response, _ = await _get_realtime_response_with_history(session, input_text, timeout)
    return response


def _extract_assistant_responses_from_history(history: list | None) -> str:
    """Extracts all assistant text and audio transcript responses from a history list."""
    if not history:
        return ""

    response_texts = []
    for item in history:
        if hasattr(item, "role") and item.role == "assistant" and hasattr(item, "content") and item.content:
            for content_part in item.content:
                text = None
                if content_part.type == "text" and hasattr(content_part, "text") and content_part.text:
                    text = content_part.text
                elif content_part.type == "audio" and hasattr(content_part, "transcript") and content_part.transcript:
                    text = content_part.transcript

                if text:
                    response_texts.append(text)

    return " ".join(response_texts)


# ---------------------------------------------------------------------------
# Tool-call assertion DSL
# ---------------------------------------------------------------------------

# Canonical item format: list[tuple[str, str | None]]
#   ("function_call", "tool_name") or ("message", None)
OrderedItems = list[tuple[str, str | None]]


def extract_ordered_items_from_history(history: list, *, multi_turn: bool = False) -> OrderedItems:
    """Extract ordered (type, name) tuples from realtime session history.

    For multi-turn conversations, only items after the last user message are included.
    """
    items = history
    if multi_turn:
        last_user_idx = max(
            (i for i, item in enumerate(history) if item.type == "message" and getattr(item, "role", None) == "user"),
            default=-1,
        )
        items = history[last_user_idx + 1 :]

    return [
        (item.type, getattr(item, "name", None))
        for item in items
        if item.type == "function_call" or (item.type == "message" and getattr(item, "role", None) == "assistant")
    ]


def extract_ordered_items_from_run_result(result: Any) -> OrderedItems:
    """Extract ordered (type, name) tuples from a non-realtime RunResult.

    Iterates ``result.new_items``, emitting ToolCallItem as ``("function_call", name)``
    and MessageOutputItem as ``("message", None)``.
    """
    from agents import MessageOutputItem, ToolCallItem

    ordered: OrderedItems = []
    for item in result.new_items:
        if isinstance(item, ToolCallItem):
            ordered.append(("function_call", getattr(item.raw_item, "name", None)))
        elif isinstance(item, MessageOutputItem):
            ordered.append(("message", None))
    return ordered


def extract_ordered_items_from_serialized_new_items(new_items: list[dict[str, Any]] | None) -> OrderedItems:
    """Extract ordered (type, name) tuples from serialized ``new_items`` dicts.

    The voice thinker stores ``item.to_input_item()`` output on the shared context so
    realtime tests can reconstruct downstream tool calls without depending on SDK types.
    """
    if not new_items:
        return []

    ordered: OrderedItems = []
    for item in new_items:
        item_type = item.get("type")
        if item_type == "function_call":
            ordered.append(("function_call", item.get("name")))
        elif item_type == "message" and item.get("role") == "assistant":
            ordered.append(("message", None))

    return ordered


def insert_voice_thinker_run_items_after_thinker(
    ordered: OrderedItems,
    thinker_runs: list[dict[str, Any]],
    thinker_tool_name: str = "resident_thinker_tool",
    *,
    include_outer_thinker_call: bool = True,
) -> OrderedItems:
    """Insert inner thinker tool calls after each outer thinker invocation.

    The outer realtime history shows only responder-side tools. Voice thinker runs are
    captured separately on ``context.voice_thinker_runs`` in invocation order. This
    helper splices each run's inner tool-call sequence after the corresponding
    ``resident_thinker_tool`` item so assertions can operate on one chronological list.
    """
    if not thinker_runs:
        return ordered

    result: OrderedItems = []
    thinker_run_iter = iter(thinker_runs)

    for item_type, name in ordered:
        is_outer_thinker_call = item_type == "function_call" and name == thinker_tool_name
        if include_outer_thinker_call or not is_outer_thinker_call:
            result.append((item_type, name))
        if item_type != "function_call" or name != thinker_tool_name:
            continue

        thinker_run = next(thinker_run_iter, None)
        if thinker_run is None:
            continue

        inner_items = extract_ordered_items_from_serialized_new_items(thinker_run.get("new_items"))
        inner_tool_calls = [item for item in inner_items if item[0] == "function_call"]

        if not inner_tool_calls:
            inner_tool_calls = [
                ("function_call", call["tool_name"])
                for call in thinker_run.get("mcp_tool_calls", [])
                if call.get("tool_name")
            ]

        result.extend(inner_tool_calls)

    return result


def filter_expected_tool_calls_for_channel(
    expected_tool_calls: list[dict] | None,
    channel_name: str,
) -> list[dict]:
    """Return only the expected tool calls that apply to the given channel.

    Each tool spec may optionally declare ``channels`` as either a string or a
    list of channel names. Specs without ``channels`` apply to every channel.
    """
    if not expected_tool_calls:
        return []

    normalized_channel = channel_name.upper()
    filtered_specs: list[dict] = []

    for spec in expected_tool_calls:
        channels = spec.get("channels")
        if channels is None:
            filtered_specs.append(spec)
            continue

        if isinstance(channels, str):
            allowed_channels = {channels.upper()}
        else:
            allowed_channels = {channel.upper() for channel in channels}

        if normalized_channel not in allowed_channels:
            continue

        filtered_specs.append({key: value for key, value in spec.items() if key != "channels"})

    return filtered_specs


def _insert_mcp_calls_after_thinker(
    ordered: OrderedItems,
    mcp_tool_calls: list[dict],
    thinker_tool_name: str = "resident_thinker_tool",
) -> OrderedItems:
    """Insert downstream thinker tool calls after the matching thinker invocation.

    Preferred input is ``context.voice_thinker_runs``. For backward compatibility,
    a flat ``mcp_tool_calls`` list is still accepted and inserted in recorded order.
    """
    if not mcp_tool_calls:
        return ordered

    if isinstance(mcp_tool_calls[0], dict) and (
        "new_items" in mcp_tool_calls[0] or "mcp_tool_calls" in mcp_tool_calls[0]
    ):
        return insert_voice_thinker_run_items_after_thinker(ordered, mcp_tool_calls, thinker_tool_name)

    mcp_items: OrderedItems = [("function_call", call["tool_name"]) for call in mcp_tool_calls]

    result: OrderedItems = []
    for item_type, name in ordered:
        result.append((item_type, name))
        if item_type == "function_call" and name == thinker_tool_name:
            result.extend(mcp_items)
    return result


def assert_expected_tool_calls(
    ordered_items: OrderedItems,
    expected_tool_calls: list[dict],
) -> None:
    """Assert tool calls match the expected list from a test case.

    Each entry in ``expected_tool_calls``:
      - ``name`` (str): tool name
      - ``called`` (bool, default True): assert called / NOT called

    For ``called=True`` entries, order is enforced — each must appear after the
    previous expected tool's position.
    """
    tool_names_in_items = [name for item_type, name in ordered_items if item_type == "function_call"]
    last_pos = -1

    for spec in expected_tool_calls:
        name = spec["name"]
        should_be_called = spec.get("called", True)

        if not should_be_called:
            assert name not in tool_names_in_items, (
                f"Tool '{name}' should NOT have been called but was found in: {ordered_items}"
            )
            continue

        # Find all positions of this tool in ordered_items (not just tool_names_in_items)
        positions = [i for i, (t, n) in enumerate(ordered_items) if t == "function_call" and n == name]
        assert positions, f"Tool '{name}' was expected but never called. Items: {ordered_items}"

        # Must appear after the previous expected tool
        valid = [p for p in positions if p > last_pos]
        assert valid, (
            f"Tool '{name}' was called but not after position {last_pos} (order violation). "
            f"Positions found: {positions}. Items: {ordered_items}"
        )
        last_pos = valid[0]
