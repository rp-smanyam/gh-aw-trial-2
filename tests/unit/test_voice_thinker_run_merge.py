from tests.integration.helpers import (
    _insert_mcp_calls_after_thinker,
    extract_ordered_items_from_serialized_new_items,
    filter_expected_tool_calls_for_channel,
    insert_voice_thinker_run_items_after_thinker,
)


class TestExtractOrderedItemsFromSerializedNewItems:
    def test_keeps_function_calls_and_assistant_messages_in_order(self):
        new_items = [
            {"type": "message", "role": "user", "content": []},
            {"type": "function_call", "name": "get_residents_packages", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
            {"type": "message", "role": "assistant", "content": []},
            {"type": "function_call", "name": "create_link", "arguments": "{}"},
        ]

        assert extract_ordered_items_from_serialized_new_items(new_items) == [
            ("function_call", "get_residents_packages"),
            ("message", None),
            ("function_call", "create_link"),
        ]


class TestInsertVoiceThinkerRunItemsAfterThinker:
    def test_inserts_each_inner_run_after_matching_outer_thinker_call(self):
        ordered = [
            ("message", None),
            ("function_call", "resident_thinker_tool"),
            ("message", None),
            ("function_call", "resident_thinker_tool"),
            ("message", None),
            ("function_call", "transfer_to_staff_voice"),
        ]
        thinker_runs = [
            {
                "new_items": [
                    {"type": "function_call", "name": "get_residents_packages", "arguments": "{}"},
                    {"type": "message", "role": "assistant", "content": []},
                ]
            },
            {
                "new_items": [
                    {"type": "function_call", "name": "fetch_community_events", "arguments": "{}"},
                    {"type": "message", "role": "assistant", "content": []},
                    {"type": "function_call", "name": "create_link", "arguments": "{}"},
                ]
            },
        ]

        assert insert_voice_thinker_run_items_after_thinker(ordered, thinker_runs) == [
            ("message", None),
            ("function_call", "resident_thinker_tool"),
            ("function_call", "get_residents_packages"),
            ("message", None),
            ("function_call", "resident_thinker_tool"),
            ("function_call", "fetch_community_events"),
            ("function_call", "create_link"),
            ("message", None),
            ("function_call", "transfer_to_staff_voice"),
        ]

    def test_can_hide_outer_thinker_wrapper_call(self):
        ordered = [
            ("message", None),
            ("function_call", "resident_thinker_tool"),
            ("message", None),
        ]
        thinker_runs = [
            {
                "new_items": [
                    {"type": "function_call", "name": "get_residents_packages", "arguments": "{}"},
                ]
            }
        ]

        assert insert_voice_thinker_run_items_after_thinker(
            ordered,
            thinker_runs,
            include_outer_thinker_call=False,
        ) == [
            ("message", None),
            ("function_call", "get_residents_packages"),
            ("message", None),
        ]

    def test_falls_back_to_mcp_calls_when_serialized_items_are_empty(self):
        ordered = [
            ("function_call", "resident_thinker_tool"),
            ("message", None),
        ]
        thinker_runs = [
            {
                "new_items": [],
                "mcp_tool_calls": [
                    {"tool_name": "get_residents_packages"},
                    {"tool_name": "create_link"},
                ],
            }
        ]

        assert insert_voice_thinker_run_items_after_thinker(ordered, thinker_runs) == [
            ("function_call", "resident_thinker_tool"),
            ("function_call", "get_residents_packages"),
            ("function_call", "create_link"),
            ("message", None),
        ]


class TestInsertMcpCallsAfterThinkerCompatibility:
    def test_accepts_structured_voice_thinker_runs(self):
        ordered = [
            ("function_call", "resident_thinker_tool"),
            ("message", None),
        ]
        thinker_runs = [
            {
                "new_items": [
                    {"type": "function_call", "name": "fetch_community_events", "arguments": "{}"},
                ]
            }
        ]

        assert _insert_mcp_calls_after_thinker(ordered, thinker_runs) == [
            ("function_call", "resident_thinker_tool"),
            ("function_call", "fetch_community_events"),
            ("message", None),
        ]


class TestFilterExpectedToolCallsForChannel:
    def test_keeps_specs_without_channel_filter_for_all_channels(self):
        expected_tool_calls = [
            {"name": "get_residents_packages"},
        ]

        assert filter_expected_tool_calls_for_channel(expected_tool_calls, "VOICE") == expected_tool_calls

    def test_filters_and_strips_channel_metadata(self):
        expected_tool_calls = [
            {"name": "get_residents_packages"},
            {"name": "create_link", "channels": ["CHAT", "SMS", "EMAIL"]},
            {"name": "transfer_to_staff_voice", "channels": "VOICE"},
        ]

        assert filter_expected_tool_calls_for_channel(expected_tool_calls, "VOICE") == [
            {"name": "get_residents_packages"},
            {"name": "transfer_to_staff_voice"},
        ]
