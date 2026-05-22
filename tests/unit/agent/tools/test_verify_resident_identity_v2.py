"""Tests for the candidate-generation verifier (#1491).

Covers the sub-gap A/B/C cases that v1 rejects. The autouse fixture
enables the v2 dispatch for every test in this module; the v1 baseline
is covered by `test_verify_resident_identity.py`, which is parametrized
across both engines.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from agents import RunContextWrapper

from agent_leasing.agent.tools.verify_resident_identity import (
    _verify_resident_identity_impl,
)
from agent_leasing.agent.tools.verify_resident_identity_v2 import _generate_variants
from agent_leasing.settings import settings


@pytest.fixture(autouse=True)
def _use_v2(monkeypatch):
    """Enable the candidate-generation engine for every test in this module."""
    monkeypatch.setattr(settings, "use_candidate_generation_verifier", True)


class _VerificationContext(SimpleNamespace):
    def is_identity_verified(self, channel: str) -> bool:
        return self.identity_verified.get(channel, False)

    def is_identity_verified_with_birth_year(self, channel: str) -> bool:
        return self.identity_verified_with_birth_year.get(channel, False)

    def get_verification_attempts(self, channel: str) -> int:
        return self.verification_attempts.get(channel, 0)

    def set_identity_verified(self, channel: str, value: bool = True) -> None:
        self.identity_verified[channel] = value

    def set_identity_verified_with_birth_year(self, channel: str, value: bool = True) -> None:
        self.identity_verified_with_birth_year[channel] = value

    def increment_verification_attempts(self, channel: str) -> None:
        self.verification_attempts[channel] = self.verification_attempts.get(channel, 0) + 1


def _voice_ctx(ab_unit_number: str, **product_info_kwargs) -> RunContextWrapper:
    product_info = SimpleNamespace(
        ab_unit_number=ab_unit_number,
        date_of_birth="1990-01-01",
        **product_info_kwargs,
    )
    ask_request = SimpleNamespace(product="RESIDENT_ONE_VOICE", product_info=product_info)
    context = _VerificationContext(
        ask_request=ask_request,
        identity_verified={},
        identity_verified_with_birth_year={},
        verification_attempts={},
    )
    wrapper = MagicMock(spec=RunContextWrapper)
    wrapper.context = context
    return wrapper


class TestIssue1491SubGapA:
    """Hyphen + building prefix: stored "1561-2402" + caller "2402"."""

    @pytest.mark.asyncio
    async def test_hyphen_building_prefix_with_4digit_caller_unit(self):
        """Verbatim case from #1491 sub-gap A original trace."""
        wrapper = _voice_ctx("1561-2402")
        result = await _verify_resident_identity_impl(wrapper, unit_number="2402")
        assert result["verified"] is True


class TestIssue1491SubGapB:
    """Trailing-letter symmetric: stored has letter, caller dropped it."""

    @pytest.mark.asyncio
    async def test_stored_103U_caller_103(self):
        """Prod trace 019e2436."""
        wrapper = _voice_ctx("103U")
        result = await _verify_resident_identity_impl(wrapper, unit_number="103")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_stored_182U_caller_182(self):
        """Prod trace 019e384b, Montabella at Oak Forest, 2026-05-17."""
        wrapper = _voice_ctx("182U", ab_building_number="01")
        result = await _verify_resident_identity_impl(wrapper, unit_number="182")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_stored_182U_caller_182_no_building_context(self):
        """Sub-gap B is independent of building presence."""
        wrapper = _voice_ctx("182U")
        result = await _verify_resident_identity_impl(wrapper, unit_number="182")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_stored_182U_caller_1820_still_fails(self):
        """Bare-digit strip requires exact digit-core match — '1820' != '182'."""
        wrapper = _voice_ctx("182U", ab_building_number="01")
        result = await _verify_resident_identity_impl(wrapper, unit_number="1820")
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]

    @pytest.mark.asyncio
    async def test_stored_7421C_caller_7421_still_fails(self):
        """Address-style 4+ digit core is not synonymous with the bare digits.

        Same canonical pattern as 182U but with a 4-digit core — gated out by
        the candidate generator. This matches the existing negative test in
        test_verify_resident_identity.py.
        """
        wrapper = _voice_ctx("7421C")
        result = await _verify_resident_identity_impl(wrapper, unit_number="7421")
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]


class TestDigitThresholdBoundaries:
    """Boundary tests for the 2–3 digit gate on stored-side trailing-letter strip.

    The gate is the central design decision behind sub-gap B (see v2 module
    comment). These tests lock the boundaries in so a future tweak (e.g.
    relaxing to ≤3 or extending to ≤4) surfaces here first.
    """

    @pytest.mark.asyncio
    async def test_1_digit_does_not_strip(self):
        """Stored '2A' + caller '2' must fail — '2A' could be 'address 2, unit A'."""
        wrapper = _voice_ctx("2A")
        result = await _verify_resident_identity_impl(wrapper, unit_number="2")
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]

    @pytest.mark.asyncio
    async def test_2_digit_strips(self):
        """Stored '12U' + caller '12' verifies — same pattern as sub-gap B."""
        wrapper = _voice_ctx("12U")
        result = await _verify_resident_identity_impl(wrapper, unit_number="12")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_1_digit_letter_still_matches_caller_says_letter(self):
        """Stored '2A' + caller 'A' still verifies via digit-prefix-with-letter-tail."""
        wrapper = _voice_ctx("2A")
        result = await _verify_resident_identity_impl(wrapper, unit_number="A")
        assert result["verified"] is True


class TestIssue1491SubGapC:
    """Alternate-slot caller_building never applied against stored value."""

    @pytest.mark.asyncio
    async def test_stored_W1007_alternate_1007_caller_building_W(self):
        """Prod trace 019e3363, Intown Apartments, 2026-05-17.

        Primary unit_number is corrupted by the responder; alternate carries
        the raw transcript "1007"; caller mentioned building "W". Stored
        "W1007" must reduce to "1007" via the caller_building strip and match
        the alternate.
        """
        wrapper = _voice_ctx("W1007", ab_building_number="")
        result = await _verify_resident_identity_impl(
            wrapper,
            unit_number="1207",
            alternate_unit_number="1007",
            caller_building="W",
        )
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_wrong_caller_building_does_not_falsely_match(self):
        """If caller_building doesn't actually appear in stored, no strip happens."""
        wrapper = _voice_ctx("W1007", ab_building_number="")
        result = await _verify_resident_identity_impl(
            wrapper,
            unit_number="1207",
            alternate_unit_number="9999",
            caller_building="W",
        )
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]


class TestGenerateVariants:
    """Direct unit tests for `_generate_variants`.

    Mirrors the existing pattern of testing helpers directly
    (`TestNormalizeUnitNumber`, `TestStripBuildingFromInput`). Locks in the
    exact variant set per input so v2-internal refactors fail here rather
    than downstream in the integration cases.
    """

    def test_empty_string(self):
        assert _generate_variants("", side="stored") == set()

    def test_none(self):
        assert _generate_variants(None, side="stored") == set()

    def test_bare_digit_stored(self):
        assert _generate_variants("182", side="stored") == {"182"}

    def test_sub_gap_b_3_digit_stored(self):
        """Sub-gap B fires at 3 digits; letter-tail rule adds the 'U'."""
        assert _generate_variants("182U", side="stored") == {"182U", "182", "U"}

    def test_sub_gap_b_2_digit_stored(self):
        """Sub-gap B fires at 2 digits."""
        assert _generate_variants("12U", side="stored") == {"12U", "12", "U"}

    def test_sub_gap_b_1_digit_does_not_fire(self):
        """Below the gate — only the letter-tail variant is produced."""
        assert _generate_variants("2A", side="stored") == {"2A", "A"}

    def test_sub_gap_b_4_digit_does_not_fire(self):
        """Above the gate — only the letter-tail variant is produced."""
        assert _generate_variants("7421C", side="stored") == {"7421C", "C"}

    def test_input_side_letter_strip_has_no_digit_gate(self):
        """Input-side strip is unconditional — preserves existing behavior."""
        assert _generate_variants("19C", side="input") == {"19C", "19", "C"}

    def test_input_side_letter_strip_at_4_digits(self):
        """Input-side strip works for 4-digit core too (e.g., stored '1082' + caller '1082U')."""
        assert _generate_variants("1082U", side="input") == {"1082U", "1082", "U"}

    def test_stored_hyphen_numeric_prefix(self):
        assert _generate_variants("1561-2402", side="stored") == {"15612402", "2402"}

    def test_stored_letter_section_prefix(self):
        """'MC 141' produces 141 (from MC-style rule) and MC (letter-tail on canon)."""
        assert _generate_variants("MC 141", side="stored") == {"141MC", "141", "MC"}

    def test_stored_building_strip_with_ab_building(self):
        assert _generate_variants("B-2", side="stored", ab_building_number="B") == {"2B", "B", "2"}

    def test_stored_building_strip_with_caller_building(self):
        """Sub-gap C path — caller_building strips the stored letter prefix."""
        assert _generate_variants("W1007", side="stored", caller_building="W") == {"1007W", "W", "1007"}

    def test_input_side_does_not_apply_stored_side_rules(self):
        """Stored-only rules (hyphen, MC-style, sub-gap B) should not fire on input side."""
        # Hyphen-numeric: wouldn't strip if input side
        assert _generate_variants("1561-2402", side="input") == {"15612402"}
        # MC-style: wouldn't strip
        assert _generate_variants("MC 141", side="input") == {"141MC", "MC"}
        # Sub-gap B: input-side has its own (unconditional) rule, which DOES
        # strip "182U" -> "182". This is intentional — see v2 module comment.
        assert _generate_variants("182U", side="input") == {"182U", "182", "U"}
