"""Wired-component integration tests for the analytics data path.

Exercises `add_metadata_into_context` + `publish_task_activity` end-to-end
against realistic SDK-shaped tool outputs and asserts on the captured
`task_activity_producer.produce()` calls. No LLM, no Kafka broker.

Catches the silent-drop / shape-drift class of bugs that pure unit tests
miss because the failure depends on the interplay between several tools'
output shapes in the same turn (issue #1541).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

# Pre-load agent.tools so the kafka.task_activity ↔ agent.tools.api_call
# import cycle resolves fully before this test imports from emit directly.
# (tests/unit/conftest.py side-loads `agent_leasing.server` for the same reason.)
import agent_leasing.agent.tools  # noqa: F401  # isort:skip
import pytest

from agent_leasing.kafka import kafka_context as kafka_context_mod
from agent_leasing.kafka.fire_and_forget import drain_pending_publishes
from agent_leasing.kafka.task_activity.emit import publish_task_activity
from agent_leasing.kafka.task_activity.extractors import (
    extract_facilities_thinker_events,
    extract_lease_info_events,
    extract_packages_events,
    extract_rent_balance_events,
)
from agent_leasing.services.analytics_service import add_metadata_into_context
from agent_leasing.settings import settings


def _mcp_text_output(payload: dict) -> list:
    """Mirror the openai-agents SDK's MCP wrapper output shape: a list of
    `{"type": "input_text", "text": "<json>"}` items arriving in
    `function_call_output.output` for any MCP tool call."""
    return [{"type": "input_text", "text": json.dumps(payload)}]


def _build_session() -> MagicMock:
    """Resident session shaped enough for build_common_event_context +
    publish_task_activity."""
    pi = MagicMock()
    pi.knock_company_id = "c-1"
    pi.knock_property_id = "p-2"
    pi.knock_resident_id = "r-3"
    pi.uc_first_name = "Alex"
    pi.uc_last_name = "Smith"
    pi.ab_unit_number = "204"
    pi.ab_building_number = "B"
    pi.call_sid = None
    pi.property_name = "Test Property"
    pi.property_timezone = "America/Chicago"
    pi.uc_portal_base_url = "https://example.loftliving.com"
    pi.static_paths = MagicMock(service_request="/portal/mr")
    pi.thread_id = "stream-1"
    pi.uc_company_id = MagicMock(id="company-id")
    pi.uc_property_id = MagicMock(id="property-id")
    pi.uc_resident_household_id = MagicMock(id="hh-id")
    pi.uc_resident_member_id = MagicMock(id="member-id")

    ask_request = MagicMock()
    ask_request.product_info = pi
    ask_request.product = "renter_ai_resident_sms"
    ask_request.chat_session_id = "session-1"
    ask_request.conversation_type = MagicMock(value="sms")

    session = MagicMock()
    session.ask_request = ask_request
    session.thread_id = "thread-1"
    session.logging_metadata = {}
    session.pending_activity_publishes = set()
    session.frustrated_user_emitted = False
    return session


@pytest.fixture
def captured_task_activity_producer(monkeypatch):
    """In-memory stub for the task-activity producer; records every event
    passed to `.produce()`."""
    producer = MagicMock()
    producer.produce = MagicMock()
    monkeypatch.setattr(kafka_context_mod.kafka_application_context, "task_activity_producer", producer)
    return producer


class TestMetadataExtractionAcrossMultiToolTurn:
    """A realistic turn calls several MCP tools (different output shapes) plus
    the facilities thinker for an SR. The thinker's SR data must land in
    `logging_metadata` regardless of what other tools' outputs look like."""

    def test_sr_metadata_extracted_despite_mixed_shape_outputs(self):
        sr_response = {
            "self_service_available": False,
            "service_request_numbers": [
                {"sr_id": "999-1", "priority_number": "1", "priority_name": "Emergency"},
            ],
            "action_taken": "service_request_created",
        }

        result = MagicMock()
        result.to_input_list.return_value = [
            # MCP list-shape (multi-content): get_rent_information
            {
                "type": "function_call_output",
                "call_id": "call_rent",
                "output": _mcp_text_output({"result": {"balance": "100.00"}}),
            },
            # MCP single-content (bare dict, not list-wrapped)
            {
                "type": "function_call_output",
                "call_id": "call_lease",
                "output": {"type": "input_text", "text": json.dumps({"result": {"lease_end": "2027-01-01"}})},
            },
            # Function-tool argument record for the thinker call
            {
                "type": "function_call",
                "name": "call_facilities_thinker_via_api",
                "call_id": "call_thinker",
                "arguments": json.dumps({"emergency": True, "message": "Gas leak"}),
            },
            # Thinker's API response — function tool serializes via str(dict)
            {
                "type": "function_call_output",
                "call_id": "call_thinker",
                "output": str(sr_response),
            },
        ]

        session = _build_session()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "facilities_thinker_api_enabled", True)
            add_metadata_into_context(session, result)

        # SR metadata for the thinker call must survive the loop.
        assert "call_thinker" in session.logging_metadata
        sr_meta = session.logging_metadata["call_thinker"]["service_request"]
        assert sr_meta[0] == "create_service_request"
        assert sr_meta[1] == {
            "created": True,
            "sr_id": "999-1",
            "priority_number": "1",
            "priority_name": "Emergency",
        }


@pytest.mark.asyncio
class TestTaskActivityFanout:
    """Every tool we extract activity events from must reach the producer
    with the expected `activity.summary`. Catches: an extractor silently
    returning [] because of an unguarded shape mismatch."""

    async def test_all_resident_tools_emit_their_activity_summary(self, captured_task_activity_producer):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "task_activity_event_publishing_enabled", True)
            mp.setattr(settings, "task_activity_publish_timeout_seconds", 1.0)

            session = _build_session()

            publish_task_activity(
                extract_facilities_thinker_events,
                {
                    "service_request_numbers": [
                        {"sr_id": "999-1", "priority_number": "1", "priority_name": "Emergency"},
                    ],
                    "action_taken": "service_request_created",
                },
                session,
                user_request="Gas leak",
            )
            publish_task_activity(
                extract_rent_balance_events,
                {
                    "current_balance": "100.00",
                    "rent": "1500.00",
                    "rent_due_date": "2026-06-01",
                },
                session,
                mcp_arguments={"chat_summary": "rent?"},
            )
            publish_task_activity(
                extract_lease_info_events,
                {
                    "result": {
                        "lease_start": "2025-06-01",
                        "lease_end": "2027-01-01",
                        "unit": "204",
                        "buildingNumber": "B",
                    }
                },
                session,
                mcp_arguments={"chat_summary": "lease?"},
            )
            publish_task_activity(
                extract_packages_events,
                {"packages_list": [], "packages_count": 0},
                session,
                mcp_arguments={},
            )

            await drain_pending_publishes(session.pending_activity_publishes)

        produced_events = [c.args[0] for c in captured_task_activity_producer.produce.call_args_list]
        summaries = [e["activity"]["summary"] for e in produced_events]

        assert "Create SR - Emergency" in summaries
        assert "Rent and Balance" in summaries
        assert "Lease Info" in summaries
        assert "Package Questions Asked" in summaries

    async def test_non_emergency_sr_emits_non_emergency_summary(self, captured_task_activity_producer):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "task_activity_event_publishing_enabled", True)
            mp.setattr(settings, "task_activity_publish_timeout_seconds", 1.0)

            session = _build_session()
            publish_task_activity(
                extract_facilities_thinker_events,
                {
                    "service_request_numbers": [
                        {"sr_id": "777-1", "priority_number": "3", "priority_name": "Routine"},
                    ],
                    "action_taken": "service_request_created",
                },
                session,
                user_request="Leaky faucet",
            )
            await drain_pending_publishes(session.pending_activity_publishes)

        produced_events = [c.args[0] for c in captured_task_activity_producer.produce.call_args_list]
        summaries = [e["activity"]["summary"] for e in produced_events]
        assert summaries == ["Create SR - Non-Emergency"]
