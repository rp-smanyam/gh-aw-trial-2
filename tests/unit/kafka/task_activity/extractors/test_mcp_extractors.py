"""Unit tests for the five tool-driven MCP activity extractors:
rent/balance, lease info, guest parking, community-event signup, packages.

The pure extractors are exercised here; wire-in (post-processor placement
in `agent.py`) is verified by the existing agent unit tests.
"""

import pytest

from agent_leasing.kafka.task_activity.extractors.community_event_signup import (
    extract_community_event_signup_events,
)
from agent_leasing.kafka.task_activity.extractors.guest_parking import extract_guest_parking_events
from agent_leasing.kafka.task_activity.extractors.lease_info import extract_lease_info_events
from agent_leasing.kafka.task_activity.extractors.packages import extract_packages_events
from agent_leasing.kafka.task_activity.extractors.rent_balance import extract_rent_balance_events


def _ref_types(event):
    return [r["type"] for r in event["references"]]


def _resident_id(event):
    resident_ref = next((r for r in event["references"] if r["type"] == "RESIDENT"), None)
    return resident_ref["id"] if resident_ref else None


class TestExtractRentBalanceEvents:
    def test_emits_one_event_with_balance_fields(self, make_session):
        output = {
            "current_balance": "$123.45",
            "past_due_balance": "$0.00",
            "rent": "$1,899.00",
            "rent_due_date": "2026-05-01T00:00:00+00:00",
        }
        events = extract_rent_balance_events(output, context=make_session())
        assert len(events) == 1
        e = events[0]
        assert e["activity"]["summary"] == "Rent and Balance"
        assert e["activity"]["detail"] == "Fetched rent and balance"
        assert e["extra"]["current_balance"] == "$123.45"
        assert e["extra"]["past_due_balance"] == "$0.00"
        assert e["extra"]["rent"] == "$1,899.00"
        assert e["extra"]["rent_due_date"] == "2026-05-01T00:00:00+00:00"
        assert _ref_types(e) == ["COMPANY", "PROPERTY", "RESIDENT"]
        assert _resident_id(e) == "r-3"

    def test_user_request_from_mcp_arguments_lands_in_detail_and_extras(self, make_session):
        events = extract_rent_balance_events(
            {"rent": "$1,000"},
            context=make_session(),
            mcp_arguments={"chat_summary": "How much do I owe?"},
        )
        assert events[0]["activity"]["detail"] == "Fetched rent and balance for: How much do I owe?"
        assert events[0]["extra"]["user_request"] == "How much do I owe?"

    def test_returns_empty_for_non_dict(self, make_session):
        assert extract_rent_balance_events(None, context=make_session()) == []
        assert extract_rent_balance_events("not a dict", context=make_session()) == []

    def test_omits_missing_fields(self, make_session):
        events = extract_rent_balance_events({"rent": "$100"}, context=make_session())
        extra = events[0]["extra"]
        assert extra["rent"] == "$100"
        assert "current_balance" not in extra
        assert "past_due_balance" not in extra
        assert "rent_due_date" not in extra


class TestExtractLeaseInfoEvents:
    def test_emits_with_lease_fields_and_occupants_count(self, make_session):
        output = {
            "result": {
                "lease_start": "2025-06-01",
                "lease_end": "2026-05-31",
                "unit": "Apt 1203",
                "occupants": ["John Doe", "Jane Doe"],
                "buildingNumber": "125",
            },
            "status_code": "ok",
        }
        events = extract_lease_info_events(output, context=make_session())
        assert len(events) == 1
        e = events[0]
        assert e["activity"]["summary"] == "Lease Info"
        assert e["activity"]["detail"] == "Fetched lease information"
        assert e["extra"]["lease_start"] == "2025-06-01"
        assert e["extra"]["lease_end"] == "2026-05-31"
        assert e["extra"]["lease_unit"] == "Apt 1203"
        assert e["extra"]["lease_building_number"] == "125"
        assert e["extra"]["occupants_count"] == "2"
        assert _ref_types(e) == ["COMPANY", "PROPERTY", "RESIDENT"]

    def test_user_request_lands_in_detail(self, make_session):
        events = extract_lease_info_events(
            {"result": {"lease_end": "2026-05-31"}},
            context=make_session(),
            mcp_arguments={"chat_summary": "When does my lease end?"},
        )
        assert events[0]["activity"]["detail"] == "Fetched lease information for: When does my lease end?"

    def test_skips_when_result_block_missing(self, make_session):
        assert extract_lease_info_events({"status_code": "ok"}, context=make_session()) == []

    def test_returns_empty_for_non_dict(self, make_session):
        assert extract_lease_info_events(None, context=make_session()) == []


class TestExtractGuestParkingEvents:
    PASS_OUTPUT = {
        "data": {
            "addParkingPass": {
                "id": "2047431",
                "downloadUrl": "https://cassidysouth.qa2.loftliving.com/portal/parking-passes/print/passId/2047431",
                "validFrom": "2026-05-01T05:00:00-07:00",
                "validTo": "2026-05-02T17:00:00-07:00",
                "vehicleMake": "RAM",
                "vehicleModel": "TRX",
                "vehicleLicensePlate": "TX-6666",
            }
        }
    }

    def test_emits_with_vehicle_descriptor_and_loft_living_link(self, make_session):
        session = make_session()
        # Add parking_passes to static_paths for this test.
        session.ask_request.product_info.static_paths.parking_passes = "/portal/parking-passes"
        events = extract_guest_parking_events(self.PASS_OUTPUT, context=session)
        assert len(events) == 1
        e = events[0]
        assert e["activity"]["summary"] == "Created Guest Parking Pass"
        assert e["activity"]["detail"] == "Created guest parking pass for RAM TRX (TX-6666)"
        assert e["extra"]["parking_pass_id"] == "2047431"
        assert e["extra"]["vehicle_make"] == "RAM"
        assert e["extra"]["vehicle_model"] == "TRX"
        assert e["extra"]["vehicle_license_plate"] == "TX-6666"
        assert "downloadUrl" not in e["extra"]
        assert e["extra"]["download_url"].endswith("/passId/2047431")
        assert e["extra"]["loft_living_link"] == "https://example.loftliving.com/portal/parking-passes"
        assert _ref_types(e) == ["COMPANY", "PROPERTY", "RESIDENT"]

    def test_omits_loft_living_link_when_static_paths_missing(self, make_session):
        session = make_session()
        # Mock returns a MagicMock for parking_passes by default; clear it.
        session.ask_request.product_info.static_paths.parking_passes = None
        events = extract_guest_parking_events(self.PASS_OUTPUT, context=session)
        assert "loft_living_link" not in events[0]["extra"]

    def test_skips_when_addParkingPass_missing(self, make_session):
        assert extract_guest_parking_events({"data": {}}, context=make_session()) == []

    def test_skips_when_id_missing(self, make_session):
        bad = {"data": {"addParkingPass": {"vehicleMake": "RAM"}}}
        assert extract_guest_parking_events(bad, context=make_session()) == []

    def test_returns_empty_for_non_dict(self, make_session):
        assert extract_guest_parking_events(None, context=make_session()) == []


class TestExtractCommunityEventSignupEvents:
    SIGNUP_OUTPUT = {
        "registerEvent": {
            "eventId": "384387",
            "eventSignupId": "591304",
            "guests": 2,
            "successText": "Sunset Rooftop Yoga Session 04/30 14:00 - 04/30 16:00",
            "paymentText": " You are now signed up!",
            "attendeesCount": 4,
            "totalCost": "0",
        }
    }

    def test_emits_with_event_fields(self, make_session):
        events = extract_community_event_signup_events(self.SIGNUP_OUTPUT, context=make_session())
        assert len(events) == 1
        e = events[0]
        assert e["activity"]["summary"] == "Signed Up for Community Event"
        assert "Sunset Rooftop Yoga Session" in e["activity"]["detail"]
        assert e["extra"]["event_id"] == "384387"
        assert e["extra"]["event_signup_id"] == "591304"
        assert e["extra"]["guests"] == "2"
        assert e["extra"]["attendees_count"] == "4"
        assert e["extra"]["total_cost"] == "0"
        assert _ref_types(e) == ["COMPANY", "PROPERTY", "RESIDENT"]

    def test_skips_when_register_event_null(self, make_session):
        assert extract_community_event_signup_events({"registerEvent": None}, context=make_session()) == []

    def test_skips_when_event_id_missing(self, make_session):
        bad = {"registerEvent": {"eventSignupId": "591304"}}
        assert extract_community_event_signup_events(bad, context=make_session()) == []

    def test_returns_empty_for_non_dict(self, make_session):
        assert extract_community_event_signup_events(None, context=make_session()) == []

    def test_falls_back_to_event_id_in_detail_when_success_text_missing(self, make_session):
        out = {"registerEvent": {"eventId": "X-1"}}
        events = extract_community_event_signup_events(out, context=make_session())
        assert events[0]["activity"]["detail"] == "Signed up for community event X-1"


class TestExtractPackagesEvents:
    PACKAGES_OUTPUT = {
        "packages_list": [
            {
                "packageType": "Box",
                "packageStation": "Station A",
                "trackingNumber": "123456789",
            },
            {
                "packageType": "Envelope",
                "packageStation": "Station B",
                "trackingNumber": "987654321",
            },
        ],
        "packages_count": 2,
    }

    def test_emits_with_count_and_unique_descriptors(self, make_session):
        events = extract_packages_events(self.PACKAGES_OUTPUT, context=make_session())
        assert len(events) == 1
        e = events[0]
        assert e["activity"]["summary"] == "Package Questions Asked"
        assert "Resident asked about 2 package" in e["activity"]["detail"]
        assert e["extra"]["packages_count"] == "2"
        assert e["extra"]["package_types"] == "Box,Envelope"
        assert e["extra"]["package_stations"] == "Station A,Station B"
        assert _ref_types(e) == ["COMPANY", "PROPERTY", "RESIDENT"]

    def test_zero_packages_still_emits_with_no_packages_message(self, make_session):
        events = extract_packages_events({"packages_list": [], "packages_count": 0}, context=make_session())
        e = events[0]
        assert e["extra"]["packages_count"] == "0"
        assert e["activity"]["detail"] == "Resident asked about packages (none on file)"
        assert "package_types" not in e["extra"]

    def test_dedupes_repeated_types(self, make_session):
        out = {
            "packages_list": [
                {"packageType": "Box", "packageStation": "A"},
                {"packageType": "Box", "packageStation": "A"},
                {"packageType": "Envelope", "packageStation": "B"},
            ],
            "packages_count": 3,
        }
        events = extract_packages_events(out, context=make_session())
        assert events[0]["extra"]["package_types"] == "Box,Envelope"
        assert events[0]["extra"]["package_stations"] == "A,B"

    def test_returns_empty_for_non_dict(self, make_session):
        assert extract_packages_events(None, context=make_session()) == []

    @pytest.mark.parametrize("missing_key", ["packages_list", "packages_count"])
    def test_handles_missing_top_level_keys(self, missing_key, make_session):
        out = {k: v for k, v in self.PACKAGES_OUTPUT.items() if k != missing_key}
        events = extract_packages_events(out, context=make_session())
        # Always emits — empty/missing list is "no packages on file" not "skip".
        assert len(events) == 1
