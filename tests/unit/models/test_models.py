import pytest
from pydantic import ValidationError

from agent_leasing.agent.util import ResidentResponderOutput


class TestResidentResponderOutput:
    def test_extract_flows(self):
        output = ResidentResponderOutput(
            response="test",
            reasoning="test",
            language_code="en",
            workflow_codes=["packages_flow"],
        )
        flows = output.extract_flows()
        assert flows[0].name == "PACKAGES_FLOW"
        assert flows[0].display_name == "Packages Flow"

    def test_extract_flows_valid_flow(self):
        output = ResidentResponderOutput(
            response="test",
            reasoning="test",
            language_code="en",
            workflow_codes=["junk"],
        )
        flows = output.extract_flows()
        assert not flows

    def test_qna_topics_default_empty(self):
        output = ResidentResponderOutput(response="hi")
        assert output.qna_topics == []

    def test_qna_topics_accepts_valid_taxonomy_values(self):
        output = ResidentResponderOutput(
            response="The pool is open until 9pm.",
            workflow_codes=["qna_flow"],
            qna_topics=["AMENITIES_AND_FACILITIES.POOL", "STAFF_AND_HOURS.OFFICE_HOURS"],
        )
        assert output.qna_topics == [
            "AMENITIES_AND_FACILITIES.POOL",
            "STAFF_AND_HOURS.OFFICE_HOURS",
        ]

    def test_qna_topics_accepts_top_level_other(self):
        output = ResidentResponderOutput(
            response="x",
            workflow_codes=["qna_flow"],
            qna_topics=["OTHER"],
        )
        assert output.qna_topics == ["OTHER"]

    def test_qna_topics_rejects_unknown_value(self):
        with pytest.raises(ValidationError):
            ResidentResponderOutput(
                response="x",
                workflow_codes=["qna_flow"],
                qna_topics=["AMENITIES_AND_FACILITIES.HOT_TUB"],
            )

    def test_qna_topics_rejects_lowercase_value(self):
        with pytest.raises(ValidationError):
            ResidentResponderOutput(
                response="x",
                workflow_codes=["qna_flow"],
                qna_topics=["amenities_and_facilities.pool"],
            )

    def test_qna_topics_rejects_partial_category(self):
        # Bare category without subtopic is not a valid taxonomy value.
        with pytest.raises(ValidationError):
            ResidentResponderOutput(
                response="x",
                workflow_codes=["qna_flow"],
                qna_topics=["AMENITIES_AND_FACILITIES"],
            )

    def test_qna_topics_rejects_cross_category_subtopic(self):
        # POOL is only valid under AMENITIES_AND_FACILITIES; mixing it
        # with a different category must be rejected. Pydantic doesn't
        # decompose the string — the literal enum simply has no
        # "PARKING.POOL" member, so cross-category combinations fail.
        with pytest.raises(ValidationError):
            ResidentResponderOutput(
                response="x",
                workflow_codes=["qna_flow"],
                qna_topics=["PARKING.POOL"],
            )

    def test_qna_topics_rejects_bare_subtopic_without_category(self):
        with pytest.raises(ValidationError):
            ResidentResponderOutput(
                response="x",
                workflow_codes=["qna_flow"],
                qna_topics=["POOL"],
            )
