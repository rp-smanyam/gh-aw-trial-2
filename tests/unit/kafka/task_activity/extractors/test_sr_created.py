import pytest

from agent_leasing.kafka.task_activity.extractors.sr_created import (
    ACTIVITY_SUMMARY_EMERGENCY,
    ACTIVITY_SUMMARY_NON_EMERGENCY,
    SRCreatedFacts,
    build_sr_created_event,
    extract_sr_created_events,
    parse_sr_created_facts,
)


def _sr(sr_id="4216-1", priority_number="3", priority_name="Routine"):
    return {"sr_id": sr_id, "priority_number": priority_number, "priority_name": priority_name}


def _output(srs, action_taken="service_request_created"):
    return {
        "self_service_available": False,
        "service_request_numbers": srs,
        "action_taken": action_taken,
        "instructions": "stub",
    }


class TestParseSRCreatedFacts:
    def test_single_sr(self):
        facts = parse_sr_created_facts(_output([_sr()]))
        assert facts == [SRCreatedFacts(sr_number="4216-1", priority_number="3", priority_name="Routine")]

    def test_multiple_srs_in_one_call(self):
        facts = parse_sr_created_facts(
            _output(
                [
                    _sr(sr_id="100-1", priority_number="3", priority_name="Routine"),
                    _sr(sr_id="100-2", priority_number="1", priority_name="Emergency"),
                ]
            )
        )
        assert [f.sr_number for f in facts] == ["100-1", "100-2"]
        assert [f.priority_number for f in facts] == ["3", "1"]

    def test_skips_when_action_is_not_creation(self):
        assert parse_sr_created_facts(_output([_sr()], action_taken="self_service_offered")) == []

    def test_skips_when_action_taken_missing(self):
        out = _output([_sr()])
        del out["action_taken"]
        assert parse_sr_created_facts(out) == []

    def test_skips_when_sr_numbers_missing(self):
        out = _output([])
        del out["service_request_numbers"]
        assert parse_sr_created_facts(out) == []

    def test_skips_entries_without_sr_id(self):
        # Defensive: if upstream ever returns an SR row missing sr_id,
        # don't emit garbage.
        facts = parse_sr_created_facts(_output([{"priority_number": "1"}, _sr(sr_id="X")]))
        assert [f.sr_number for f in facts] == ["X"]

    def test_returns_empty_for_non_dict(self):
        assert parse_sr_created_facts(None) == []
        assert parse_sr_created_facts("not a dict") == []
        assert parse_sr_created_facts(42) == []

    def test_optional_priority_fields_missing(self):
        facts = parse_sr_created_facts(_output([{"sr_id": "no-priority"}]))
        assert facts == [SRCreatedFacts(sr_number="no-priority", priority_number=None, priority_name=None)]


class TestParseSRCreatedFactsLegacyMCPShape:
    """The MCP `create_service_request` tool returns a flat shape (single SR
    per call): `service_request_id`, `service_request_created`, `priority_number`,
    `priority_name`. Different from the facilities-thinker-API list shape.
    """

    def _mcp(self, sr_id="14111-1", priority_number="2", priority_name="Standard", created=True):
        return {
            "service_request_id": sr_id,
            "service_request_created": created,
            "priority_number": priority_number,
            "priority_name": priority_name,
            "agent_response": "Service request created successfully.",
        }

    def test_single_sr(self):
        facts = parse_sr_created_facts(self._mcp())
        assert facts == [SRCreatedFacts(sr_number="14111-1", priority_number="2", priority_name="Standard")]

    def test_emergency_priority_preserved(self):
        facts = parse_sr_created_facts(self._mcp(priority_number="1", priority_name="Emergency"))
        assert facts == [SRCreatedFacts(sr_number="14111-1", priority_number="1", priority_name="Emergency")]

    def test_skips_when_creation_failed(self):
        out = self._mcp(created=False)
        # Real MCP failure shape: id None, created False
        out["service_request_id"] = None
        assert parse_sr_created_facts(out) == []

    def test_skips_when_service_request_created_missing(self):
        out = self._mcp()
        del out["service_request_created"]
        assert parse_sr_created_facts(out) == []

    def test_skips_when_sr_id_missing(self):
        out = self._mcp()
        out["service_request_id"] = None
        assert parse_sr_created_facts(out) == []

    def test_optional_priority_fields_missing(self):
        out = {"service_request_id": "X", "service_request_created": True}
        assert parse_sr_created_facts(out) == [SRCreatedFacts(sr_number="X", priority_number=None, priority_name=None)]


class TestBuildSRCreatedEvent:
    @pytest.fixture
    def context(self):
        return dict(
            task_id="task-uuid",
            channel="CHAT",
            knock_company_id="c-1",
            knock_property_id="p-2",
            knock_resident_id="r-3",
            first_name="Alex",
            last_name="Smith",
            ab_unit_number="204",
            ab_building_number="B",
            chat_session_id="cs-1",
        )

    def test_emergency_priority_uses_emergency_summary(self, context):
        facts = SRCreatedFacts(sr_number="4216-1", priority_number="1", priority_name="Emergency")
        event = build_sr_created_event(facts, **context)
        assert event["activity"]["summary"] == ACTIVITY_SUMMARY_EMERGENCY

    def test_non_emergency_priority_uses_non_emergency_summary(self, context):
        facts = SRCreatedFacts(sr_number="4216-1", priority_number="3", priority_name="Routine")
        event = build_sr_created_event(facts, **context)
        assert event["activity"]["summary"] == ACTIVITY_SUMMARY_NON_EMERGENCY

    def test_missing_priority_treated_as_non_emergency(self, context):
        facts = SRCreatedFacts(sr_number="4216-1", priority_number=None, priority_name=None)
        event = build_sr_created_event(facts, **context)
        assert event["activity"]["summary"] == ACTIVITY_SUMMARY_NON_EMERGENCY

    def test_detail_includes_sr_number_and_priority(self, context):
        facts = SRCreatedFacts(sr_number="4216-1", priority_number="1", priority_name="Emergency")
        event = build_sr_created_event(facts, **context)
        assert event["activity"]["detail"] == "Created Emergency SR 4216-1"

    def test_detail_omits_priority_when_name_missing(self, context):
        facts = SRCreatedFacts(sr_number="X", priority_number=None, priority_name=None)
        event = build_sr_created_event(facts, **context)
        assert event["activity"]["detail"] == "Created SR X"

    def test_extra_includes_sr_keys_and_common_context(self, context):
        facts = SRCreatedFacts(sr_number="42", priority_number="3", priority_name="Routine")
        event = build_sr_created_event(facts, **context)
        extra = event["extra"]
        assert extra["sr_number"] == "42"
        assert extra["sr_priority_number"] == "3"
        assert extra["sr_priority_name"] == "Routine"
        # Common context fields land here too. Resident identity is in
        # `references` (type RESIDENT), not `extra.map`.
        assert extra["channel"] == "CHAT"
        assert extra["originating_source"] == "RESIDENT_AI"
        assert "knock_resident_id" not in extra
        assert extra["chat_session_id"] == "cs-1"

    def test_user_request_appears_in_detail_and_extras(self, context):
        facts = SRCreatedFacts(sr_number="42", priority_number="3", priority_name="Routine")
        event = build_sr_created_event(facts, **context, user_request="Pantry light is out")
        assert event["activity"]["detail"] == "Created Routine SR 42 for: Pantry light is out"
        assert event["extra"]["user_request"] == "Pantry light is out"

    def test_loft_living_link_lands_in_extras(self, context):
        facts = SRCreatedFacts(sr_number="42", priority_number="3", priority_name="Routine")
        event = build_sr_created_event(facts, **context, loft_living_link="https://example.loftliving.com/portal/mr")
        assert event["extra"]["loft_living_link"] == "https://example.loftliving.com/portal/mr"

    def test_extra_omits_priority_keys_when_facts_lack_them(self, context):
        facts = SRCreatedFacts(sr_number="X", priority_number=None, priority_name=None)
        event = build_sr_created_event(facts, **context)
        extra = event["extra"]
        assert "sr_priority_number" not in extra
        assert "sr_priority_name" not in extra
        assert extra["sr_number"] == "X"

    def test_references_seeded_with_company_property_resident(self, context):
        facts = SRCreatedFacts(sr_number="X", priority_number="3", priority_name="Routine")
        event = build_sr_created_event(facts, **context)
        types = [r["type"] for r in event["references"]]
        assert types == ["COMPANY", "PROPERTY", "RESIDENT"]
        resident_ref = next(r for r in event["references"] if r["type"] == "RESIDENT")
        assert resident_ref["id"] == "r-3"
        assert "knock_resident_id" not in event["extra"]


class TestExtractSRCreatedEvents:
    """Wire-up layer: takes `context: SessionScope` + caller kwargs and
    derives common identifiers + the SR portal link itself. The pure
    `build_sr_created_event` is exercised via flat kwargs above.
    """

    def test_returns_one_event_per_sr(self, make_session):
        events = extract_sr_created_events(
            _output(
                [
                    _sr(sr_id="100-1", priority_number="3", priority_name="Routine"),
                    _sr(sr_id="100-2", priority_number="1", priority_name="Emergency"),
                ]
            ),
            context=make_session(),
        )
        assert len(events) == 2
        assert events[0]["activity"]["summary"] == ACTIVITY_SUMMARY_NON_EMERGENCY
        assert events[1]["activity"]["summary"] == ACTIVITY_SUMMARY_EMERGENCY

    def test_returns_empty_for_non_creation_outputs(self, make_session):
        events = extract_sr_created_events(
            _output([_sr()], action_taken="self_service_offered"),
            context=make_session(),
        )
        assert events == []

    def test_returns_empty_for_none_input(self, make_session):
        assert extract_sr_created_events(None, context=make_session()) == []

    def test_pulls_user_request_from_mcp_arguments(self, make_session):
        events = extract_sr_created_events(
            _output([_sr()]),
            context=make_session(),
            mcp_arguments={"chat_summary": "Pantry light is out"},
        )
        assert events[0]["extra"]["user_request"] == "Pantry light is out"
        assert events[0]["activity"]["detail"].endswith("for: Pantry light is out")

    def test_pulls_user_request_from_thinker_kwarg(self, make_session):
        events = extract_sr_created_events(
            _output([_sr()]),
            context=make_session(),
            user_request="Toilet leaking",
        )
        assert events[0]["extra"]["user_request"] == "Toilet leaking"

    def test_user_request_kwarg_wins_when_both_paths_provide(self, make_session):
        events = extract_sr_created_events(
            _output([_sr()]),
            context=make_session(),
            mcp_arguments={"chat_summary": "From MCP"},
            user_request="From thinker",
        )
        assert events[0]["extra"]["user_request"] == "From thinker"

    def test_loft_living_link_derived_from_session(self, make_session):
        events = extract_sr_created_events(_output([_sr()]), context=make_session())
        assert events[0]["extra"]["loft_living_link"] == "https://example.loftliving.com/portal/mr"

    def test_loft_living_link_omitted_when_portal_config_missing(self, make_session):
        session = make_session()
        session.ask_request.product_info.uc_portal_base_url = None
        events = extract_sr_created_events(_output([_sr()]), context=session)
        assert "loft_living_link" not in events[0]["extra"]
