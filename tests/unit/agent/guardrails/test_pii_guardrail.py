"""
Test PII Guardrail functionality

This test demonstrates how the PII guardrail prevents personal information
from being exposed in agent responses.
"""

import inspect
import random
from random import choices, randint
from unittest.mock import MagicMock

import pytest
import structlog
from agents import Agent, RunContextWrapper

from agent_leasing.agent.guardrails.pii_guardrail.pii_guardrail import (
    detect_pii,
    pii_input_guardrail,
    pii_output_guardrail,
)

logger = structlog.get_logger(__name__)

random.seed(42)  # make the tests repeatable


def gen_rand_digits(n: int):
    return "".join(choices("0123456789", k=n))


TEST_CASES = [
    pytest.param(
        "pii_email_1",
        "My email is john.doe@example.com",
        ["email address"],
        False,
        id="pii_email_1",
    ),
    pytest.param(
        "pii_email_2",
        "Contact me at test@company.org",
        ["email address"],
        False,
        id="pii_email_2",
    ),
    pytest.param(
        "pii_email_3",
        "No email here",
        [],
        False,
        id="pii_email_3",
    ),
    pytest.param("pii_email_4", "Invalid email@", [], False, id="pii_email_4"),
    pytest.param(
        "pii_phone_1",
        "Call me at 317-842-5961",
        [],
        False,
        id="pii_phone_1",
    ),
    pytest.param(
        "pii_phone_2",
        "My number is +1-408-222-9483",
        [],
        False,
        id="pii_phone_2",
    ),
    pytest.param(
        "pii_phone_3",
        "Phone: +1-712-555-3804",
        [],
        False,
        id="pii_phone_3",
    ),
    pytest.param("pii_phone_4", "Call 9302174836", [], False, id="pii_phone_4"),
    pytest.param("pii_phone_5", "No phone here", [], False, id="pii_phone_5"),
    # Business phone numbers should be allowed (not flagged as PII)
    pytest.param(
        "business_phone_1",
        "Katie's Towing at 972-820-8000",
        [],
        False,
        id="business_phone_towing_service_1",
    ),
    pytest.param(
        "business_phone_2",
        "For towing service, contact 972-820-8000",
        [],
        False,
        id="business_phone_towing_service_2",
    ),
    # License plates in parking/vehicle context should NOT be flagged as driver's license PII
    pytest.param(
        "license_plate_1",
        "My guest license plate is TRX-W011",
        [],
        False,
        id="license_plate_parking_context_1",
    ),
    pytest.param(
        "license_plate_2",
        "I need a parking pass for plate number W234",
        [],
        False,
        id="license_plate_parking_context_2",
    ),
    pytest.param(
        "license_plate_3",
        "Guest vehicle tag is B5921",
        [],
        False,
        id="license_plate_vehicle_context",
    ),
    pytest.param(
        "license_plate_5",
        "Guest pass for registration P4410",
        [],
        False,
        id="license_plate_registration_context",
    ),
    # Edge: DL-like number in license plate context should still be allowed
    pytest.param(
        "license_plate_6",
        "My license plate is D1234567",
        [],
        False,
        id="license_plate_dl_like_number_in_plate_context",
    ),
    # Real driver's license numbers should still be flagged
    pytest.param(
        "real_drivers_license_1",
        "My driver license number is W0114567890",
        ["driver license"],
        True,
        id="real_drivers_license_still_flagged",
    ),
    pytest.param(
        "real_drivers_license_2",
        "My driver's license is D1234567",
        ["driver license"],
        True,
        id="real_drivers_license_with_apostrophe",
    ),
    pytest.param(
        "real_drivers_license_4",
        "License ID for identification: S5678901",
        ["driver license"],
        True,
        id="real_drivers_license_identification_context",
    ),
    pytest.param(
        "real_drivers_license_6",
        "My driver license is AB12345",
        ["driver license"],
        True,
        id="real_drivers_license_two_letter_prefix",
    ),
    pytest.param(
        "real_drivers_license_8",
        "My driver license is W0112345",
        ["driver license"],
        True,
        id="real_drivers_license_short_with_driver_context",
    ),
    pytest.param(
        "real_drivers_license_9",
        "My license number is K87654321",
        ["driver license"],
        True,
        id="real_drivers_license_license_number_phrasing",
    ),
    pytest.param(
        "real_drivers_license_5",
        "I need to update my driving permit, it is K12345678",
        ["driver license"],
        True,
        id="real_drivers_license_driving_permit",
    ),
    # Edge: DL in non-plate "car" context should still be flagged
    pytest.param(
        "real_drivers_license_car_context",
        "my driver license is in my car: D1234567",
        ["driver license"],
        True,
        id="real_drivers_license_car_not_plate",
    ),
    # Mixed PII: SSN should still be detected alongside a license plate
    pytest.param(
        "mixed_pii_ssn_and_plate",
        "My license plate is TRX-W011 and my SSN is 782-64-2198",
        ["social security number"],
        True,
        id="mixed_pii_ssn_with_license_plate",
    ),
    pytest.param(
        "pii_ssn_1",
        "My SSN is 782-64-2198",
        ["social security number"],
        True,
        id="pii_ssn_1",
    ),
    pytest.param(
        "pii_ssn_2",
        "Social: 203 31 5690",
        ["social security number"],
        True,
        id="pii_ssn_2",
    ),
    pytest.param("pii_ssn_3", "No SSN here", [], False, id="pii_ssn_3"),
    pytest.param("pii_ssn_4", "Invalid: 000-00-0000", [], False, id="pii_ssn_4"),
    pytest.param(
        "pii_credit_card_1",
        "Card: 3726 727374 35387",  # amex style
        ["credit card number"],
        True,
        id="pii_credit_card_1",
    ),
    pytest.param(
        "pii_credit_card_2",
        "Credit Card: 5169-5467-1982-8815",  # visa style
        ["credit card number"],
        True,
        marks=pytest.mark.repeat(10),
        id="pii_credit_card_2",
    ),
    pytest.param(
        "pii_credit_card_3",
        "Number: 4165317319538881",
        ["credit card number"],
        True,
        id="pii_credit_card_3",
    ),
    pytest.param("pii_credit_card_4", "No card here", [], False, id="pii_credit_card_4"),
    pytest.param(
        "pii_multiple_1",
        "Email me at alice@sample.com or call 821-555-3927",
        ["email address"],
        False,
        id="pii_multiple_1",
    ),
    pytest.param(
        "pii_none_1",
        "The apartment has two bedrooms and three bathrooms.",
        [],
        False,
        id="pii_none_1",
    ),
]

for i in range(10):
    TEST_CASES.append(
        pytest.param(
            f"pii_none_2-{i}",
            f"Tracking Number: {gen_rand_digits(10)}",
            [],
            False,
            id=f"pii_none_2-{i}",
        )
    )

for i in range(10):
    TEST_CASES.append(
        pytest.param(
            f"pii_none_3-{i}",
            f"ID Number: {gen_rand_digits(10)}",
            [],
            False,
            id=f"pii_none_3-{i}",
        )
    )

for i in range(10):
    TEST_CASES.append(
        pytest.param(
            f"pii_none_4-{i}",
            f"reference number: {gen_rand_digits(10)}",
            [],
            False,
            id=f"pii_none_4-{i}",
        )
    )

for i in range(10):
    TEST_CASES.append(
        pytest.param(
            f"pii_none_5-{i}",
            f"Your rent is ${randint(1000, 4000)} per month.",
            [],
            False,
            id=f"pii_none_5-{i}",
        )
    )
for i in range(10):
    TEST_CASES.append(
        pytest.param(
            f"pii_none_6-{i}",
            f"Your balance is ${randint(100, 1000)}.{gen_rand_digits(2)}.  Please pay by 2025-{randint(1, 12)}-{randint(1, 28)}.",
            [],
            False,
            id=f"pii_none_6-{i}",
        )
    )

for i in range(10):
    TEST_CASES.append(
        pytest.param(
            f"pii_none_7-{i}",
            f"You have two packages at the package stations. One is a fragile Box at Station A with tracking number {gen_rand_digits(10)}, and the other is an urgent Envelope at Station B with tracking number {gen_rand_digits(10)}. Let me know if you need more details or help!",
            [],
            False,
            id=f"pii_none_7-{i}",
        )
    )


class TestPIIDetection:
    """Test PII detection patterns."""

    @pytest.mark.parametrize(
        "id,text,expected_types,expected_bool",
        TEST_CASES,
    )
    def test_pii_detection(self, id, text, expected_types, expected_bool):
        result = detect_pii(text)
        assert result.contains_pii == expected_bool, f"Failed for: {text}"
        if expected_bool:
            logger.info(f"Expected: {expected_types}, Found: {result.pii_types_found}")
            for expected_type in expected_types:
                assert expected_type in result.pii_types_found  # false positives are okay


@pytest.mark.asyncio
async def _invoke_guardrail(guardrail, ctx, agent, payload):
    """Call a guardrail's underlying function, awaiting if needed."""

    result = guardrail.guardrail_function(ctx, agent, payload)
    if inspect.isawaitable(result):
        return await result
    return result


class TestPIIInputGuardrail:
    """Test PII input guardrail functionality."""

    @pytest.mark.parametrize(
        "id,text,expected_types,expected_bool",
        TEST_CASES,
    )
    async def test_input_guardrail(self, id, text, expected_types, expected_bool):
        """Test that input guardrail catches PII in user input."""
        ctx = MagicMock(spec=RunContextWrapper)
        ctx.context = MagicMock(language_code="en")
        agent = MagicMock(spec=Agent)

        result = await _invoke_guardrail(pii_input_guardrail, ctx, agent, text)

        assert result.tripwire_triggered == expected_bool, f"Failed for: {text}"

        if expected_bool:
            # PII detected - should block
            assert result.output_info.safe_response
            assert len(result.output_info.pii_types_found) > 0
            # Verify expected PII types were found
            for expected_type in expected_types:
                assert expected_type in result.output_info.pii_types_found
        else:
            # No PII - should allow through
            assert result.output_info == text


@pytest.mark.asyncio
class TestPIIOutputGuardrail:
    """Test PII output guardrail functionality."""

    @pytest.mark.parametrize(
        "id,text,expected_types,expected_bool",
        TEST_CASES,
    )
    async def test_output_guardrail(self, id, text, expected_types, expected_bool):
        """Test that output guardrail catches PII in agent output."""
        ctx = MagicMock(spec=RunContextWrapper)
        ctx.context = MagicMock(language_code="en")
        agent = MagicMock(spec=Agent)

        result = await _invoke_guardrail(pii_output_guardrail, ctx, agent, text)

        assert result.tripwire_triggered == expected_bool, f"Failed for: {text}"

        if expected_bool:
            # PII detected - should block
            assert result.output_info.safe_response
            assert len(result.output_info.pii_types_found) > 0
            # Verify expected PII types were found
            for expected_type in expected_types:
                assert expected_type in result.output_info.pii_types_found
        else:
            # No PII - should allow through
            assert result.output_info == text

    async def test_guardrail_with_structured_output(self):
        """Test guardrail with structured output objects."""
        ctx = MagicMock(spec=RunContextWrapper)
        ctx.context = MagicMock(language_code="en")
        agent = MagicMock(spec=Agent)

        # Test with object that has 'content' attribute
        output = MagicMock()
        output.content = "My credit card number is 4532 1488 0343 6467"
        result = await _invoke_guardrail(pii_output_guardrail, ctx, agent, output)

        assert result.tripwire_triggered is True
        assert "credit card number" in result.output_info.pii_types_found
