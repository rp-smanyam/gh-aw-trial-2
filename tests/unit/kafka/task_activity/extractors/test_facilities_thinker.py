"""Tests for the facilities-thinker meta-extractor — dispatch on
`action_taken`. Per-sub-extractor event shape is covered in
`test_sr_created.py`.
"""

from agent_leasing.kafka.task_activity.extractors.facilities_thinker import (
    extract_facilities_thinker_events,
)


def _sr_created_response(sr_id="X", priority_number="3", priority_name="Routine"):
    return {
        "service_request_numbers": [
            {"sr_id": sr_id, "priority_number": priority_number, "priority_name": priority_name}
        ],
        "action_taken": "service_request_created",
    }


class TestExtractFacilitiesThinkerEvents:
    def test_dispatches_to_sr_created_on_action_service_request_created(self, make_session):
        events = extract_facilities_thinker_events(_sr_created_response(), context=make_session())
        assert len(events) == 1
        assert events[0]["activity"]["summary"] == "Create SR - Non-Emergency"

    def test_threads_user_request_through_to_sub_extractor(self, make_session):
        events = extract_facilities_thinker_events(
            _sr_created_response(),
            context=make_session(),
            user_request="Toilet leaking",
        )
        assert events[0]["extra"]["user_request"] == "Toilet leaking"
        assert events[0]["activity"]["detail"].endswith("for: Toilet leaking")

    def test_returns_empty_for_self_service_action(self, make_session):
        # Self-service is registered as an action but not (yet) wired to an
        # extractor — empty list signals "nothing to emit" to the publisher.
        response = {"action_taken": "self_service_offered", "service_request_numbers": []}
        assert extract_facilities_thinker_events(response, context=make_session()) == []

    def test_returns_empty_when_action_taken_missing(self, make_session):
        response = {"service_request_numbers": [{"sr_id": "X"}]}
        assert extract_facilities_thinker_events(response, context=make_session()) == []

    def test_returns_empty_when_response_not_a_dict(self, make_session):
        assert extract_facilities_thinker_events("Error: no response", context=make_session()) == []
        assert extract_facilities_thinker_events(None, context=make_session()) == []

    def test_returns_empty_for_unregistered_action(self, make_session):
        response = {"action_taken": "some_brand_new_action"}
        assert extract_facilities_thinker_events(response, context=make_session()) == []
