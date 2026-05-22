import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_leasing.api.model import Channel, Flow
from agent_leasing.kafka.kafka_recorder import Author
from agent_leasing.services.analytics_service import add_metadata_into_context, log_conversation_exchange
from agent_leasing.settings import settings


class TestAddMetadataIntoContext:
    """Test add_metadata_into_context function."""

    def test_add_metadata_with_create_service_request_call(self):
        """Test metadata is added for create_service_request function call."""
        context = MagicMock()
        context.logging_metadata = {}

        result = MagicMock()
        result.to_input_list.return_value = [
            {"type": "function_call", "name": "create_service_request", "call_id": "call_123"}
        ]

        add_metadata_into_context(context, result)

        assert "call_123" in context.logging_metadata
        assert context.logging_metadata["call_123"] == {
            "service_request": ["create_service_request", {"created": False}]
        }

    def test_add_metadata_with_create_service_request_call_with_user_input(self):
        """Test metadata is added for create_service_request function call."""
        context = MagicMock()
        context.logging_metadata = {}

        result = MagicMock()
        result.to_input_list.return_value = [
            {"type": "function_call", "name": "create_service_request", "call_id": "call_123"}
        ]

        add_metadata_into_context(context, result, user_input="create service request")

        assert "create service request" in context.logging_metadata
        assert "call_123" not in context.logging_metadata
        assert context.logging_metadata["create service request"] == {
            "service_request": ["create_service_request", {"created": False}]
        }

    def test_add_metadata_with_service_request_created_legacy(self):
        """Test metadata is updated when service request is created."""
        context = MagicMock()
        context.logging_metadata = {}

        output_data = {
            "text": json.dumps(
                {
                    "service_request_created": True,
                    "service_request_id": "SR-456",
                    "priority_number": "1",
                    "priority_name": "Emergency",
                }
            )
        }

        result = MagicMock()
        result.to_input_list.return_value = [
            {"type": "function_call", "name": "create_service_request", "call_id": "call_123"},
            {
                "type": "function_call_output",
                "call_id": "call_123",
                "output": json.dumps(output_data),
            },
        ]

        with patch.object(settings, "facilities_thinker_api_enabled", False):
            add_metadata_into_context(context, result)

        assert "call_123" in context.logging_metadata
        assert context.logging_metadata["call_123"] == {
            "service_request": [
                "create_service_request",
                {
                    "created": True,
                    "sr_id": "SR-456",
                    "priority_number": "1",
                    "priority_name": "Emergency",
                },
            ]
        }

    def test_add_metadata_with_service_request_created_legacy_output_as_list(self):
        """Regression: upstream sometimes hands us output already deserialized as a list
        instead of a JSON string. Previously raised TypeError and silently dropped metadata
        (auto-flagged GitHub issues #1337, #1377, #1532, #1541)."""
        context = MagicMock()
        context.logging_metadata = {}

        # output arrives as a Python list (not a JSON-encoded string)
        output_list = [
            {
                "text": json.dumps(
                    {
                        "service_request_created": True,
                        "service_request_id": "SR-789",
                        "priority_number": "2",
                        "priority_name": "High",
                    }
                )
            }
        ]

        result = MagicMock()
        result.to_input_list.return_value = [
            {"type": "function_call", "name": "create_service_request", "call_id": "call_456"},
            {
                "type": "function_call_output",
                "call_id": "call_456",
                "output": output_list,
            },
        ]

        with patch.object(settings, "facilities_thinker_api_enabled", False):
            add_metadata_into_context(context, result)

        assert "call_456" in context.logging_metadata
        assert context.logging_metadata["call_456"] == {
            "service_request": [
                "create_service_request",
                {
                    "created": True,
                    "sr_id": "SR-789",
                    "priority_number": "2",
                    "priority_name": "High",
                },
            ]
        }

    def test_add_metadata_with_service_request_created_legacy_output_as_dict(self):
        """Same regression — output may arrive as a dict already."""
        context = MagicMock()
        context.logging_metadata = {}

        output_dict = {
            "text": json.dumps(
                {
                    "service_request_created": True,
                    "service_request_id": "SR-999",
                    "priority_number": "3",
                    "priority_name": "Standard",
                }
            )
        }

        result = MagicMock()
        result.to_input_list.return_value = [
            {"type": "function_call", "name": "create_service_request", "call_id": "call_789"},
            {
                "type": "function_call_output",
                "call_id": "call_789",
                "output": output_dict,
            },
        ]

        with patch.object(settings, "facilities_thinker_api_enabled", False):
            add_metadata_into_context(context, result)

        assert "call_789" in context.logging_metadata
        assert context.logging_metadata["call_789"]["service_request"][1]["sr_id"] == "SR-999"

    def test_add_metadata_with_service_request_created_and_tool_output_as_list_legacy(self):
        """Test metadata is updated when service request is created."""
        context = MagicMock()
        context.logging_metadata = {}

        output_data = [
            {
                "text": json.dumps(
                    [
                        {
                            "service_request_created": True,
                            "service_request_id": "SR-456",
                            "priority_number": "1",
                            "priority_name": "Emergency",
                        }
                    ]
                )
            }
        ]

        result = MagicMock()
        result.to_input_list.return_value = [
            {"type": "function_call", "name": "create_service_request", "call_id": "call_123"},
            {
                "type": "function_call_output",
                "call_id": "call_123",
                "output": json.dumps(output_data),
            },
        ]

        with patch.object(settings, "facilities_thinker_api_enabled", False):
            add_metadata_into_context(context, result)

        assert "call_123" in context.logging_metadata
        assert context.logging_metadata["call_123"] == {
            "service_request": [
                "create_service_request",
                {
                    "created": True,
                    "sr_id": "SR-456",
                    "priority_number": "1",
                    "priority_name": "Emergency",
                },
            ]
        }

    def test_add_metadata_with_service_request_not_created_legacy(self):
        """Test metadata shows service request not created."""
        context = MagicMock()
        context.logging_metadata = {}

        output_data = {"text": json.dumps({"service_request_created": False})}

        result = MagicMock()
        result.to_input_list.return_value = [
            {"type": "function_call", "name": "create_service_request", "call_id": "call_789"},
            {
                "type": "function_call_output",
                "call_id": "call_789",
                "output": json.dumps(output_data),
            },
        ]

        with patch.object(settings, "facilities_thinker_api_enabled", False):
            add_metadata_into_context(context, result)

        # Should have the initial function_call metadata, but not updated by output
        assert "call_789" in context.logging_metadata
        assert context.logging_metadata["call_789"] == {
            "service_request": ["create_service_request", {"created": False}]
        }

    def test_add_metadata_with_invalid_output_json(self):
        """Test handles invalid JSON in output gracefully."""
        context = MagicMock()
        context.logging_metadata = {}

        result = MagicMock()
        result.to_input_list.return_value = [
            {"type": "function_call_output", "call_id": "call_invalid", "output": "invalid json"}
        ]

        # Should not raise exception
        with patch.object(settings, "facilities_thinker_api_enabled", False):
            add_metadata_into_context(context, result)

        # Should not add any metadata
        assert "call_invalid" not in context.logging_metadata

    def test_add_metadata_with_invalid_text_json(self):
        """Test handles invalid JSON in text field gracefully."""
        context = MagicMock()
        context.logging_metadata = {}

        result = MagicMock()
        result.to_input_list.return_value = [
            {
                "type": "function_call_output",
                "call_id": "call_invalid_text",
                "output": json.dumps({"text": "invalid json"}),
            }
        ]

        # Should not raise exception
        with patch.object(settings, "facilities_thinker_api_enabled", False):
            add_metadata_into_context(context, result)

        # Should not add any metadata
        assert "call_invalid_text" not in context.logging_metadata

    def test_add_metadata_with_multiple_function_calls(self):
        """Test metadata is added for multiple function calls."""
        context = MagicMock()
        context.logging_metadata = {}

        result = MagicMock()
        result.to_input_list.return_value = [
            {"type": "function_call", "name": "create_service_request", "call_id": "call_1"},
            {"type": "function_call", "name": "create_service_request", "call_id": "call_2"},
        ]

        add_metadata_into_context(context, result)

        assert "call_1" in context.logging_metadata
        assert "call_2" in context.logging_metadata
        assert context.logging_metadata["call_1"] == {
            "service_request": ["create_service_request", {"created": False}]
        }
        assert context.logging_metadata["call_2"] == {
            "service_request": ["create_service_request", {"created": False}]
        }

    def test_add_metadata_with_other_function_calls(self):
        """Test other function calls are ignored."""
        context = MagicMock()
        context.logging_metadata = {}

        result = MagicMock()
        result.to_input_list.return_value = [
            {"type": "function_call", "name": "some_other_function", "call_id": "call_other"}
        ]

        add_metadata_into_context(context, result)

        # Should not add metadata for other functions
        assert "call_other" not in context.logging_metadata

    def test_add_metadata_with_empty_input_list(self):
        """Test with empty input list."""
        context = MagicMock()
        context.logging_metadata = {}

        result = MagicMock()
        result.to_input_list.return_value = []

        add_metadata_into_context(context, result)

        # Should have empty metadata
        assert len(context.logging_metadata) == 0

    def test_add_metadata_updates_existing_context_metadata(self):
        """Test that metadata is merged with existing context metadata."""
        context = MagicMock()
        context.logging_metadata = {"existing_key": "existing_value"}

        result = MagicMock()
        result.to_input_list.return_value = [
            {"type": "function_call", "name": "create_service_request", "call_id": "call_new"}
        ]

        add_metadata_into_context(context, result)

        # Should have both existing and new metadata
        assert "existing_key" in context.logging_metadata
        assert "call_new" in context.logging_metadata
        assert context.logging_metadata["existing_key"] == "existing_value"

    def test_add_metadata_with_missing_call_id(self):
        """Test handles missing call_id gracefully."""
        context = MagicMock()
        context.logging_metadata = {}

        result = MagicMock()
        result.to_input_list.return_value = [
            {"type": "function_call", "name": "create_service_request"}  # No call_id
        ]

        # Should not raise exception
        add_metadata_into_context(context, result)

        # Should add metadata with None key
        assert None in context.logging_metadata

    def test_add_metadata_with_output_missing_text_field_legacy(self):
        """Test handles function_call_output without text field."""
        context = MagicMock()
        context.logging_metadata = {}

        result = MagicMock()
        result.to_input_list.return_value = [
            {
                "type": "function_call_output",
                "call_id": "call_no_text",
                "output": json.dumps({"other_field": "value"}),
            }
        ]

        # Should not raise exception
        with patch.object(settings, "facilities_thinker_api_enabled", False):
            add_metadata_into_context(context, result)

        # Should not add any metadata because text parsing fails
        assert "call_no_text" not in context.logging_metadata

    def test_add_metadata_full_workflow(self):
        """Test complete workflow with function call and successful output."""
        context = MagicMock()
        context.logging_metadata = {}

        output_data = {
            "text": json.dumps(
                {
                    "service_request_created": True,
                    "service_request_id": "SR-XYZ-123",
                    "priority_number": "2",
                    "priority_name": "High",
                }
            )
        }

        result = MagicMock()
        result.to_input_list.return_value = [
            {"type": "function_call", "name": "create_service_request", "call_id": "workflow_call"},
            {
                "type": "function_call_output",
                "call_id": "workflow_call",
                "output": json.dumps(output_data),
            },
        ]

        with patch.object(settings, "facilities_thinker_api_enabled", False):
            add_metadata_into_context(context, result)

        # Should have the final updated state
        assert context.logging_metadata["workflow_call"] == {
            "service_request": [
                "create_service_request",
                {
                    "created": True,
                    "sr_id": "SR-XYZ-123",
                    "priority_number": "2",
                    "priority_name": "High",
                },
            ]
        }

    @patch.object(settings, "facilities_thinker_api_enabled", True)
    def test_add_metadata_with_sr_numbers_enhanced_json(self):
        """Enhanced parsing should capture SR numbers from JSON string output."""
        context = MagicMock()
        context.logging_metadata = {}

        result = MagicMock()
        result.to_input_list.return_value = [
            {
                "type": "function_call_output",
                "call_id": "call_enh_json",
                "output": json.dumps(
                    {
                        "self_service_available": False,
                        "service_request_numbers": [
                            {"sr_id": "599-1", "priority_number": "1", "priority_name": "Emergency"},
                            {"sr_id": "599-2", "priority_number": "2", "priority_name": "High"},
                        ],
                        "instructions": "Created requests",
                        "action_taken": "service_request_created",
                    }
                ),
            }
        ]

        add_metadata_into_context(context, result)

        assert context.logging_metadata["call_enh_json"] == {
            "service_request": [
                "create_service_request",
                {"created": True, "sr_id": "599-1", "priority_number": "1", "priority_name": "Emergency"},
                {"created": True, "sr_id": "599-2", "priority_number": "2", "priority_name": "High"},
            ]
        }

    @patch.object(settings, "facilities_thinker_api_enabled", True)
    def test_add_metadata_with_sr_numbers_enhanced_string_literal(self):
        """Enhanced parsing should capture SR numbers from python-literal string output."""
        context = MagicMock()
        context.logging_metadata = {}

        raw = "{'self_service_available': False, 'service_request_numbers': [{'sr_id': '600-1', 'priority_number': '1', 'priority_name': 'Emergency'}], 'instructions': 'ok', 'action_taken': 'service_request_created'}"
        result = MagicMock()
        result.to_input_list.return_value = [
            {
                "type": "function_call_output",
                "call_id": "call_enh_lit",
                "output": raw,
            }
        ]

        add_metadata_into_context(context, result)

        assert context.logging_metadata["call_enh_lit"] == {
            "service_request": [
                "create_service_request",
                {"created": True, "sr_id": "600-1", "priority_number": "1", "priority_name": "Emergency"},
            ]
        }

    def test_add_metadata_with_self_service_flags_json(self):
        """self-service flags from JSON arguments are recorded."""
        context = MagicMock()
        context.logging_metadata = {}

        result = MagicMock()
        result.to_input_list.return_value = [
            {
                "type": "function_call",
                "name": "call_facilities_thinker_via_api",
                "call_id": "123",
                "arguments": json.dumps(
                    {
                        "issue_resolved_with_self_service": True,
                        "self_service_steps_requested": False,
                    }
                ),
            }
        ]

        add_metadata_into_context(context, result)

        assert context.logging_metadata["123"] == {
            "service_request": [
                "self_service",
                {"issue_resolved_with_self_service": True},
                {"self_service_steps_requested": False},
            ]
        }

    def test_add_metadata_with_self_service_flags_literal_string(self):
        """self-service flags from python-literal arguments are recorded."""
        context = MagicMock()
        context.logging_metadata = {}

        result = MagicMock()
        result.to_input_list.return_value = [
            {
                "type": "function_call",
                "name": "call_facilities_thinker_via_api",
                "call_id": "123",
                "arguments": "{'issue_resolved_with_self_service': False, 'self_service_steps_requested': True}",
            }
        ]

        add_metadata_into_context(context, result)

        assert context.logging_metadata["123"] == {
            "service_request": [
                "self_service",
                {"issue_resolved_with_self_service": False},
                {"self_service_steps_requested": True},
            ]
        }

    def test_add_metadata_with_self_service_missing_flags_is_ignored(self):
        """Missing or None self-service flags should not add metadata."""
        context = MagicMock()
        context.logging_metadata = {}

        result = MagicMock()
        result.to_input_list.return_value = [
            {
                "type": "function_call",
                "name": "call_facilities_thinker_via_api",
                "call_id": "123",
                "arguments": json.dumps({"issue_resolved_with_self_service": None}),
            }
        ]

        add_metadata_into_context(context, result)

        assert "123" not in context.logging_metadata

    # Regression for #1541: _parse_json caught only JSONDecodeError, so any
    # non-string output earlier in the turn raised TypeError on json.loads,
    # escaped the catch, and was swallowed by add_metadata_into_context's
    # broad except — silently killing metadata extraction for the WHOLE turn
    # (including SR creation done by a later tool in the same turn).

    @patch.object(settings, "facilities_thinker_api_enabled", True)
    def test_mcp_list_output_does_not_drop_sr_metadata_in_same_turn(self):
        """An earlier function_call_output with a list-shaped output (here:
        a get_rent_information call) used to crash _parse_json and swallow the
        SR creation metadata that comes from a later item in the same turn."""
        context = MagicMock()
        context.logging_metadata = {}

        sr_response = {
            "self_service_available": False,
            "service_request_numbers": [
                {"sr_id": "999-1", "priority_number": "1", "priority_name": "Emergency"},
            ],
            "action_taken": "service_request_created",
        }

        result = MagicMock()
        result.to_input_list.return_value = [
            # MCP-style output from get_rent_information: list of {"type","text"} items.
            {
                "type": "function_call_output",
                "call_id": "call_rent",
                "output": [{"type": "text", "text": json.dumps({"result": {"balance": "100.00"}})}],
            },
            # Real SR creation from call_facilities_thinker_via_api (function tool
            # → str(dict) → Python repr).
            {
                "type": "function_call_output",
                "call_id": "call_sr",
                "output": str(sr_response),
            },
        ]

        add_metadata_into_context(context, result)

        assert context.logging_metadata.get("call_sr") == {
            "service_request": [
                "create_service_request",
                {"created": True, "sr_id": "999-1", "priority_number": "1", "priority_name": "Emergency"},
            ]
        }

    @patch.object(settings, "facilities_thinker_api_enabled", True)
    def test_mcp_dict_output_does_not_drop_sr_metadata_in_same_turn(self):
        """Same regression with a bare-dict-shaped output instead of list.
        _parse_json(dict) used to raise TypeError too."""
        context = MagicMock()
        context.logging_metadata = {}

        sr_response = {
            "self_service_available": False,
            "service_request_numbers": [
                {"sr_id": "888-1", "priority_number": "2", "priority_name": "High"},
            ],
            "action_taken": "service_request_created",
        }

        result = MagicMock()
        result.to_input_list.return_value = [
            # Single-content MCP output: a bare {"type","text"} dict.
            {
                "type": "function_call_output",
                "call_id": "call_lease",
                "output": {"type": "text", "text": json.dumps({"result": {"lease_end": "2027-01-01"}})},
            },
            {
                "type": "function_call_output",
                "call_id": "call_sr",
                "output": str(sr_response),
            },
        ]

        add_metadata_into_context(context, result)

        assert context.logging_metadata.get("call_sr", {}).get("service_request", [None, None])[1] == {
            "created": True,
            "sr_id": "888-1",
            "priority_number": "2",
            "priority_name": "High",
        }

    @patch.object(settings, "facilities_thinker_api_enabled", True)
    def test_extract_sr_metadata_with_predeserialized_dict_output(self):
        """Some upstream paths hand the API response back already deserialized
        as a dict rather than as a str(dict). Must extract metadata, not crash."""
        context = MagicMock()
        context.logging_metadata = {}

        result = MagicMock()
        result.to_input_list.return_value = [
            {
                "type": "function_call_output",
                "call_id": "call_sr",
                "output": {
                    "service_request_numbers": [
                        {"sr_id": "777-1", "priority_number": "3", "priority_name": "Routine"},
                    ],
                    "action_taken": "service_request_created",
                },
            },
        ]

        add_metadata_into_context(context, result)

        assert context.logging_metadata.get("call_sr") == {
            "service_request": [
                "create_service_request",
                {"created": True, "sr_id": "777-1", "priority_number": "3", "priority_name": "Routine"},
            ]
        }

    @patch.object(settings, "facilities_thinker_api_enabled", True)
    def test_extract_self_service_with_predeserialized_dict_arguments(self):
        """The function_call arguments path also runs through _parse_json. Make
        sure it tolerates an already-deserialized dict."""
        context = MagicMock()
        context.logging_metadata = {}

        result = MagicMock()
        result.to_input_list.return_value = [
            {
                "type": "function_call",
                "name": "call_facilities_thinker_via_api",
                "call_id": "call_ss",
                "arguments": {
                    "issue_resolved_with_self_service": True,
                    "self_service_steps_requested": False,
                },
            }
        ]

        add_metadata_into_context(context, result)

        assert context.logging_metadata.get("call_ss") == {
            "service_request": [
                "self_service",
                {"issue_resolved_with_self_service": True},
                {"self_service_steps_requested": False},
            ]
        }


class TestParseJson:
    """Direct tests for _parse_json — the helper that was crashing on non-string inputs."""

    def test_returns_dict_unchanged(self):
        from agent_leasing.services.analytics_service import _parse_json

        payload = {"a": 1, "b": [2, 3]}
        assert _parse_json(payload) is payload

    def test_returns_list_unchanged(self):
        from agent_leasing.services.analytics_service import _parse_json

        payload = [{"type": "text", "text": "{}"}]
        assert _parse_json(payload) is payload

    def test_parses_json_string(self):
        from agent_leasing.services.analytics_service import _parse_json

        assert _parse_json('{"a": 1}') == {"a": 1}

    def test_parses_python_repr_string(self):
        """str(dict) (Python repr) — what function tools' output fields look
        like after the SDK's _convert_tool_output stringifies a dict return."""
        from agent_leasing.services.analytics_service import _parse_json

        assert _parse_json("{'a': 1}") == {"a": 1}

    def test_returns_none_for_empty(self):
        from agent_leasing.services.analytics_service import _parse_json

        assert _parse_json("") is None
        assert _parse_json(None) is None

    def test_returns_none_for_unparseable_string(self):
        from agent_leasing.services.analytics_service import _parse_json

        assert _parse_json("not json or python") is None

    def test_returns_none_for_unsupported_type(self):
        from agent_leasing.services.analytics_service import _parse_json

        assert _parse_json(123) is None


class TestLogConversationExchange:
    """Test log_conversation_exchange function."""

    @pytest.mark.asyncio
    async def test_logs_both_contact_and_bot_messages(self):
        """Test that both contact and bot messages are logged."""
        flows = [Flow(name="test_flow")]

        with patch(
            "agent_leasing.services.analytics_service.log_data_curation_event", new_callable=AsyncMock
        ) as mock_log:
            await log_conversation_exchange(
                chat_session_id="session_123",
                conversation_type=Channel.CHAT,
                user_message="Hello bot",
                bot_message="Hello user",
                call_sid="call_456",
                property_id="prop_789",
                applicant_id="app_012",
                bot_type="LEASING",
                flows=flows,
                language="en",
            )

            # Should be called twice - once for contact, once for bot
            assert mock_log.call_count == 2

            # Check first call (contact message)
            first_call = mock_log.call_args_list[0]
            assert first_call.kwargs["chat_session_id"] == "session_123"
            assert first_call.kwargs["body"] == "Hello bot"
            assert first_call.kwargs["author"] == Author.CONTACT
            assert first_call.kwargs["conversation_type"] == Channel.CHAT

            # Check second call (bot message)
            second_call = mock_log.call_args_list[1]
            assert second_call.kwargs["chat_session_id"] == "session_123"
            assert second_call.kwargs["body"] == "Hello user"
            assert second_call.kwargs["author"] == Author.BOT

    @pytest.mark.asyncio
    async def test_includes_optional_parameters(self):
        """Test that optional parameters are passed through correctly."""
        flows = [Flow(name="test_flow")]
        metadata = [{"key": "value"}]

        with patch(
            "agent_leasing.services.analytics_service.log_data_curation_event", new_callable=AsyncMock
        ) as mock_log:
            await log_conversation_exchange(
                chat_session_id="session_123",
                conversation_type=Channel.CHAT,
                user_message="Test message",
                bot_message="Test response",
                call_sid="call_456",
                property_id="prop_789",
                applicant_id="app_012",
                bot_type="LEASING",
                flows=flows,
                language="es",
                bot_metadata=metadata,
                openai_trace_url="https://openai.trace",
                langsmith_trace_url="https://langsmith.trace",
            )

            # Check first call (contact) - should not have metadata
            first_call = mock_log.call_args_list[0]
            assert first_call.kwargs["language"] == "es"
            assert first_call.kwargs["openai_trace_url"] == "https://openai.trace"
            assert first_call.kwargs["langsmith_trace_url"] == "https://langsmith.trace"
            assert "metadata" not in first_call.kwargs

            # Check second call (bot) - should have metadata
            second_call = mock_log.call_args_list[1]
            assert second_call.kwargs["language"] == "es"
            assert second_call.kwargs["metadata"] == metadata
            assert second_call.kwargs["openai_trace_url"] == "https://openai.trace"
            assert second_call.kwargs["langsmith_trace_url"] == "https://langsmith.trace"

    @pytest.mark.asyncio
    async def test_default_language_is_en(self):
        """Test that default language is 'en'."""
        flows = [Flow(name="test_flow")]

        with patch(
            "agent_leasing.services.analytics_service.log_data_curation_event", new_callable=AsyncMock
        ) as mock_log:
            await log_conversation_exchange(
                chat_session_id="session_123",
                conversation_type=Channel.CHAT,
                user_message="Test",
                bot_message="Response",
                call_sid=None,
                property_id="prop_789",
                applicant_id="app_012",
                bot_type="LEASING",
                flows=flows,
            )

            # Both calls should have default language
            for call in mock_log.call_args_list:
                assert call.kwargs["language"] == "en"

    @pytest.mark.asyncio
    async def test_default_bot_metadata_is_empty_list(self):
        """Test that default bot_metadata is an empty list."""
        flows = [Flow(name="test_flow")]

        with patch(
            "agent_leasing.services.analytics_service.log_data_curation_event", new_callable=AsyncMock
        ) as mock_log:
            await log_conversation_exchange(
                chat_session_id="session_123",
                conversation_type=Channel.CHAT,
                user_message="Test",
                bot_message="Response",
                call_sid=None,
                property_id="prop_789",
                applicant_id="app_012",
                bot_type="LEASING",
                flows=flows,
            )

            # Second call (bot) should have empty metadata list
            second_call = mock_log.call_args_list[1]
            assert second_call.kwargs["metadata"] == []

    @pytest.mark.asyncio
    async def test_handles_none_call_sid(self):
        """Test that None call_sid is handled correctly."""
        flows = [Flow(name="test_flow")]

        with patch(
            "agent_leasing.services.analytics_service.log_data_curation_event", new_callable=AsyncMock
        ) as mock_log:
            await log_conversation_exchange(
                chat_session_id="session_123",
                conversation_type=Channel.CHAT,
                user_message="Test",
                bot_message="Response",
                call_sid=None,
                property_id="prop_789",
                applicant_id="app_012",
                bot_type="LEASING",
                flows=flows,
            )

            # Both calls should have None call_sid
            for call in mock_log.call_args_list:
                assert call.kwargs["call_sid"] is None

    @pytest.mark.asyncio
    async def test_preserves_all_required_fields(self):
        """Test that all required fields are preserved in both calls."""
        flows = [Flow(name="test_flow")]

        with patch(
            "agent_leasing.services.analytics_service.log_data_curation_event", new_callable=AsyncMock
        ) as mock_log:
            await log_conversation_exchange(
                chat_session_id="session_123",
                conversation_type=Channel.SMS,
                user_message="User message",
                bot_message="Bot response",
                call_sid="call_456",
                property_id="prop_789",
                applicant_id="app_012",
                bot_type="RESIDENT",
                flows=flows,
            )

            # Check both calls have all required fields
            for call in mock_log.call_args_list:
                assert call.kwargs["chat_session_id"] == "session_123"
                assert call.kwargs["conversation_type"] == Channel.SMS
                assert call.kwargs["call_sid"] == "call_456"
                assert call.kwargs["property_id"] == "prop_789"
                assert call.kwargs["applicant_id"] == "app_012"
                assert call.kwargs["bot_type"] == "RESIDENT"
                assert call.kwargs["flows"] == flows
