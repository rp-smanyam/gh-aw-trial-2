"""Unit tests for the tool-call assertion DSL in tests/integration/helpers.py."""

import pytest
from agents import Agent, MessageOutputItem, ToolCallItem
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage

from tests.integration.helpers import (
    _insert_mcp_calls_after_thinker,
    assert_expected_tool_calls,
    extract_ordered_items_from_history,
    extract_ordered_items_from_run_result,
)

# ---------------------------------------------------------------------------
# Helpers to build fake history / run-result items
# ---------------------------------------------------------------------------


class _FakeHistoryItem:
    """Mimics a realtime history item (RealtimeToolCallItem or AssistantMessageItem)."""

    def __init__(self, item_type: str, *, role: str | None = None, name: str | None = None):
        self.type = item_type
        self.role = role
        self.name = name


# ---------------------------------------------------------------------------
# extract_ordered_items_from_history
# ---------------------------------------------------------------------------


class TestExtractOrderedItemsFromHistory:
    def test_single_turn_extracts_function_calls_and_messages(self):
        history = [
            _FakeHistoryItem("message", role="assistant"),
            _FakeHistoryItem("function_call", name="resident_thinker_tool"),
            _FakeHistoryItem("message", role="assistant"),
        ]
        result = extract_ordered_items_from_history(history)
        assert result == [
            ("message", None),
            ("function_call", "resident_thinker_tool"),
            ("message", None),
        ]

    def test_multi_turn_slices_after_last_user_message(self):
        history = [
            _FakeHistoryItem("message", role="user"),
            _FakeHistoryItem("message", role="assistant"),
            _FakeHistoryItem("message", role="user"),  # last user msg
            _FakeHistoryItem("function_call", name="transfer_to_staff_voice"),
            _FakeHistoryItem("message", role="assistant"),
        ]
        result = extract_ordered_items_from_history(history, multi_turn=True)
        assert result == [
            ("function_call", "transfer_to_staff_voice"),
            ("message", None),
        ]

    def test_filters_out_non_assistant_non_function_items(self):
        history = [
            _FakeHistoryItem("message", role="user"),
            _FakeHistoryItem("function_call_output"),
            _FakeHistoryItem("function_call", name="some_tool"),
        ]
        result = extract_ordered_items_from_history(history)
        assert result == [("function_call", "some_tool")]

    def test_empty_history(self):
        assert extract_ordered_items_from_history([]) == []


# ---------------------------------------------------------------------------
# _insert_mcp_calls_after_thinker
# ---------------------------------------------------------------------------


class TestInsertMcpCallsAfterThinker:
    def test_inserts_mcp_calls_after_thinker(self):
        ordered = [
            ("message", None),
            ("function_call", "resident_thinker_tool"),
            ("message", None),
        ]
        mcp_calls = [
            {"tool_name": "create_service_request", "mcp_server": "facilities"},
        ]
        result = _insert_mcp_calls_after_thinker(ordered, mcp_calls)
        assert result == [
            ("message", None),
            ("function_call", "resident_thinker_tool"),
            ("function_call", "create_service_request"),
            ("message", None),
        ]

    def test_no_mcp_calls_returns_unchanged(self):
        ordered = [("function_call", "resident_thinker_tool")]
        assert _insert_mcp_calls_after_thinker(ordered, []) == ordered

    def test_no_thinker_in_list_leaves_mcp_calls_uninserted(self):
        ordered = [("function_call", "transfer_to_staff_voice")]
        mcp_calls = [{"tool_name": "get_rent_information", "mcp_server": "onesite"}]
        # MCP calls only insert after thinker — if no thinker, they don't appear
        result = _insert_mcp_calls_after_thinker(ordered, mcp_calls)
        assert result == [("function_call", "transfer_to_staff_voice")]


# ---------------------------------------------------------------------------
# assert_expected_tool_calls
# ---------------------------------------------------------------------------


class TestAssertExpectedToolCalls:
    def test_single_tool_called_passes(self):
        items = [
            ("message", None),
            ("function_call", "resident_thinker_tool"),
            ("message", None),
        ]
        assert_expected_tool_calls(items, [{"name": "resident_thinker_tool"}])

    def test_single_tool_missing_fails(self):
        items = [("message", None)]
        with pytest.raises(AssertionError, match="never called"):
            assert_expected_tool_calls(items, [{"name": "resident_thinker_tool"}])

    def test_ordered_tools_in_correct_order_passes(self):
        items = [
            ("function_call", "resident_thinker_tool"),
            ("message", None),
            ("function_call", "transfer_to_staff_voice"),
        ]
        assert_expected_tool_calls(
            items,
            [
                {"name": "resident_thinker_tool"},
                {"name": "transfer_to_staff_voice"},
            ],
        )

    def test_ordered_tools_in_wrong_order_fails(self):
        items = [
            ("function_call", "transfer_to_staff_voice"),
            ("function_call", "resident_thinker_tool"),
        ]
        with pytest.raises(AssertionError, match="order violation"):
            assert_expected_tool_calls(
                items,
                [
                    {"name": "resident_thinker_tool"},
                    {"name": "transfer_to_staff_voice"},
                ],
            )

    def test_called_false_tool_absent_passes(self):
        items = [
            ("function_call", "resident_thinker_tool"),
            ("message", None),
        ]
        assert_expected_tool_calls(
            items,
            [
                {"name": "resident_thinker_tool"},
                {"name": "transfer_to_staff_voice", "called": False},
            ],
        )

    def test_called_false_tool_present_fails(self):
        items = [
            ("function_call", "resident_thinker_tool"),
            ("function_call", "transfer_to_staff_voice"),
        ]
        with pytest.raises(AssertionError, match="should NOT have been called"):
            assert_expected_tool_calls(
                items,
                [{"name": "transfer_to_staff_voice", "called": False}],
            )

    def test_empty_expected_list_is_noop(self):
        items = [("function_call", "anything")]
        assert_expected_tool_calls(items, [])

    def test_multiple_occurrences_picks_first_valid(self):
        """When a tool is called twice, ordering uses the first occurrence after last_pos."""
        items = [
            ("function_call", "resident_thinker_tool"),
            ("function_call", "resident_thinker_tool"),
            ("function_call", "transfer_to_staff_voice"),
        ]
        assert_expected_tool_calls(
            items,
            [
                {"name": "resident_thinker_tool"},
                {"name": "transfer_to_staff_voice"},
            ],
        )


# ---------------------------------------------------------------------------
# extract_ordered_items_from_run_result — real SDK types
# ---------------------------------------------------------------------------

_AGENT = Agent(name="test-agent")


def _make_tool_call_item(name: str) -> ToolCallItem:
    return ToolCallItem(
        agent=_AGENT,
        raw_item=ResponseFunctionToolCall(
            type="function_call", id=f"fc_{name}", call_id=f"call_{name}", name=name, arguments="{}"
        ),
    )


def _make_message_item() -> MessageOutputItem:
    return MessageOutputItem(
        agent=_AGENT,
        raw_item=ResponseOutputMessage(type="message", id="msg_1", role="assistant", content=[], status="completed"),
    )


class TestExtractOrderedItemsFromRunResult:
    def test_extracts_tool_calls_and_messages(self):
        result = type("R", (), {"new_items": [_make_tool_call_item("resident_thinker_tool"), _make_message_item()]})()
        ordered = extract_ordered_items_from_run_result(result)
        assert ordered == [("function_call", "resident_thinker_tool"), ("message", None)]

    def test_multiple_tools_preserve_order(self):
        result = type(
            "R",
            (),
            {
                "new_items": [
                    _make_tool_call_item("resident_thinker_tool"),
                    _make_message_item(),
                    _make_tool_call_item("transfer_to_staff_voice"),
                ]
            },
        )()
        ordered = extract_ordered_items_from_run_result(result)
        assert ordered == [
            ("function_call", "resident_thinker_tool"),
            ("message", None),
            ("function_call", "transfer_to_staff_voice"),
        ]

    def test_empty_run_result(self):
        result = type("R", (), {"new_items": []})()
        assert extract_ordered_items_from_run_result(result) == []

    def test_end_to_end_with_assert(self):
        """Full pipeline: extract from real SDK types, then assert tool calls."""
        result = type(
            "R",
            (),
            {
                "new_items": [
                    _make_message_item(),
                    _make_tool_call_item("resident_thinker_tool"),
                    _make_message_item(),
                    _make_tool_call_item("transfer_to_staff_voice"),
                ]
            },
        )()
        ordered = extract_ordered_items_from_run_result(result)
        assert_expected_tool_calls(
            ordered,
            [
                {"name": "resident_thinker_tool"},
                {"name": "transfer_to_staff_voice"},
            ],
        )

    def test_end_to_end_called_false(self):
        """Extract from real SDK types, assert a tool was NOT called."""
        result = type("R", (), {"new_items": [_make_tool_call_item("resident_thinker_tool"), _make_message_item()]})()
        ordered = extract_ordered_items_from_run_result(result)
        assert_expected_tool_calls(
            ordered,
            [
                {"name": "resident_thinker_tool"},
                {"name": "transfer_to_staff_voice", "called": False},
            ],
        )
