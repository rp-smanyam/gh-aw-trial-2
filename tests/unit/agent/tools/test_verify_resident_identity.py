"""Tests for verify_resident_identity tool."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from agents import RunContextWrapper

from agent_leasing.agent.tools.verify_resident_identity import (
    ACTION_FAILED,
    ACTION_MISSING_DATA,
    ACTION_RETRY,
    ACTION_VERIFIED,
    _verify_resident_identity_impl,
    extract_birth_year,
    normalize_unit_number,
    strip_building_from_input,
    strip_trailing_letter_suffix_from_input,
)
from agent_leasing.settings import settings


@pytest.fixture(autouse=True, params=["v1", "v2"], ids=["v1", "v2"])
def _verifier_engine(request, monkeypatch):
    """Run every test in this module against both v1 and v2 (#1491).

    `_verify_resident_identity_impl` dispatches on
    settings.use_candidate_generation_verifier; flipping it here re-runs
    each case under the candidate-generation engine.
    """
    monkeypatch.setattr(settings, "use_candidate_generation_verifier", request.param == "v2")


class _VerificationContext(SimpleNamespace):
    """SimpleNamespace with channel-aware verification methods for testing."""

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


def _make_run_context(product: str, ab_unit_number: str, date_of_birth: str, **product_info_kwargs):
    """Build a RunContextWrapper with channel-aware verification context."""
    product_info = SimpleNamespace(ab_unit_number=ab_unit_number, date_of_birth=date_of_birth, **product_info_kwargs)
    ask_request = SimpleNamespace(product=product, product_info=product_info)
    context = _VerificationContext(
        ask_request=ask_request,
        identity_verified={},
        identity_verified_with_birth_year={},
        verification_attempts={},
    )
    wrapper = MagicMock(spec=RunContextWrapper)
    wrapper.context = context
    return wrapper


@pytest.fixture
def run_context_chat():
    """Create a RunContextWrapper for CHAT channel."""
    return _make_run_context("RESIDENT_ONE_CHAT", "64", "09/09/1960")


@pytest.fixture
def run_context_sms():
    """Create a RunContextWrapper for SMS channel."""
    return _make_run_context("RESIDENT_ONE_SMS", "64", "09/09/1960")


@pytest.fixture
def run_context_voice():
    """Create a RunContextWrapper for VOICE channel."""
    return _make_run_context("RESIDENT_ONE_VOICE", "12A", "1985-03-15")


class TestNormalizeUnitNumber:
    """Tests for normalize_unit_number function."""

    def test_basic_normalization(self):
        assert normalize_unit_number("64") == "64"
        assert normalize_unit_number("  64  ") == "64"
        assert normalize_unit_number("64a") == "64A"

    def test_removes_prefixes(self):
        assert normalize_unit_number("Unit 64") == "64"
        assert normalize_unit_number("Apt 64") == "64"
        assert normalize_unit_number("Apt. 64") == "64"
        assert normalize_unit_number("#64") == "64"
        assert normalize_unit_number("apartment 12A") == "12A"

    def test_collapses_spaces(self):
        assert normalize_unit_number("12 A") == "12A"
        assert normalize_unit_number("12  A") == "12A"

    def test_collapses_padded_zeros(self):
        assert normalize_unit_number("03") == "3"
        assert normalize_unit_number("00000001") == "1"
        # Alphanumeric units with leading zeros (hyphens also stripped)
        assert normalize_unit_number("0812-H") == "812H"
        assert normalize_unit_number("0315") == "315"
        assert normalize_unit_number("01A") == "1A"

    def test_handles_none(self):
        assert normalize_unit_number(None) == ""

    def test_handles_numeric(self):
        assert normalize_unit_number(64) == "64"

    def test_strips_colons_from_stt_artifacts(self):
        assert normalize_unit_number("6:30") == "630"
        assert normalize_unit_number("1:15") == "115"
        assert normalize_unit_number("12:30A") == "1230A"


class TestStripBuildingFromInput:
    """Tests for strip_building_from_input function."""

    def test_separator_dash(self):
        assert strip_building_from_input("B-2", "B") == "2"

    def test_separator_dot(self):
        assert strip_building_from_input("B.2", "B") == "2"

    def test_separator_space(self):
        assert strip_building_from_input("B 2", "B") == "2"

    def test_numeric_building(self):
        assert strip_building_from_input("10-5A", "10") == "5A"

    def test_concatenated(self):
        assert strip_building_from_input("B2", "B") == "2"

    def test_case_insensitive(self):
        assert strip_building_from_input("b-2", "B") == "2"

    def test_none_building(self):
        assert strip_building_from_input("B-2", None) is None

    def test_no_building_pattern(self):
        assert strip_building_from_input("Unit 2", "B") is None

    def test_numeric_building_alpha_remainder(self):
        """Numeric building '7421' + alpha remainder 'C' — unambiguous split."""
        assert strip_building_from_input("7421C", "7421") == "C"

    def test_numeric_building_alpha_remainder_multichar(self):
        assert strip_building_from_input("100AB", "100") == "AB"

    def test_alpha_building_alpha_remainder_rejected(self):
        """Alpha building + alpha remainder is ambiguous — should not strip."""
        assert strip_building_from_input("BC", "B") is None

    def test_numeric_building_numeric_remainder_rejected(self):
        """Numeric building '1' + numeric remainder '3' is ambiguous — should not strip."""
        assert strip_building_from_input("13", "1") is None

    def test_numeric_building_numeric_remainder_longer_rejected(self):
        """Numeric building '4' + numeric remainder '104' is ambiguous — should not strip."""
        assert strip_building_from_input("4104", "4") is None

    def test_empty_value(self):
        assert strip_building_from_input("", "B") is None

    def test_empty_building(self):
        assert strip_building_from_input("B-2", "") is None


class TestExtractBirthYear:
    """Tests for extract_birth_year function."""

    def test_mm_dd_yyyy(self):
        assert extract_birth_year("09/09/1960") == "1960"
        assert extract_birth_year("1/1/1985") == "1985"

    def test_yyyy_mm_dd(self):
        assert extract_birth_year("1960-09-09") == "1960"
        assert extract_birth_year("1985-3-15") == "1985"

    def test_year_only(self):
        assert extract_birth_year("1960") == "1960"

    def test_handles_none(self):
        assert extract_birth_year(None) is None

    def test_invalid_format(self):
        assert extract_birth_year("invalid") is None


class TestStripTrailingLetterSuffixFromInput:
    """Tests for numeric core fallback."""

    def test_suffix_stripped_when_expected_is_numeric(self):
        assert strip_trailing_letter_suffix_from_input("19C", "19") == "19"

    def test_non_matching_numeric_core_is_unchanged(self):
        assert strip_trailing_letter_suffix_from_input("18C", "19") is None

    def test_alphanumeric_expected_is_unchanged(self):
        assert strip_trailing_letter_suffix_from_input("7421C", "7421C") is None

    def test_multi_letter_suffix_is_unchanged(self):
        assert strip_trailing_letter_suffix_from_input("19AB", "19") is None

    def test_leading_letter_building_prefix_is_not_stripped(self):
        assert strip_trailing_letter_suffix_from_input("B-2", "2") is None


class TestVerifyResidentIdentity:
    """Tests for verify_resident_identity tool."""

    @pytest.mark.asyncio
    async def test_chat_channel_skips_verification(self, run_context_chat):
        """CHAT channel returns verified=true without checking values."""
        result = await _verify_resident_identity_impl(run_context_chat, unit_number="wrong")
        assert result["verified"] is True
        assert result["mismatched_fields"] == []
        assert result["action"] == ACTION_VERIFIED

    @pytest.mark.asyncio
    async def test_unit_match_sets_verified(self, run_context_sms):
        """Matching unit sets identity_verified=True."""
        result = await _verify_resident_identity_impl(run_context_sms, unit_number="64")
        assert result["verified"] is True
        assert result["action"] == ACTION_VERIFIED
        assert run_context_sms.context.is_identity_verified("SMS") is True
        assert run_context_sms.context.is_identity_verified_with_birth_year("SMS") is False

    @pytest.mark.asyncio
    async def test_unit_mismatch(self, run_context_sms):
        """Mismatched unit returns verified=false."""
        result = await _verify_resident_identity_impl(run_context_sms, unit_number="99")
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]
        assert run_context_sms.context.is_identity_verified("SMS") is False

    @pytest.mark.asyncio
    async def test_normalized_unit_match(self, run_context_sms):
        """Unit matches after normalization."""
        result = await _verify_resident_identity_impl(run_context_sms, unit_number="Unit 64")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_unit_and_birth_year_match(self, run_context_sms):
        """Both match sets identity_verified_with_birth_year=True."""
        result = await _verify_resident_identity_impl(run_context_sms, unit_number="64", birth_year="1960")
        assert result["verified"] is True
        assert run_context_sms.context.is_identity_verified("SMS") is True
        assert run_context_sms.context.is_identity_verified_with_birth_year("SMS") is True

    @pytest.mark.asyncio
    async def test_birth_year_mismatch(self, run_context_sms):
        """Unit matches but birth year doesn't."""
        result = await _verify_resident_identity_impl(run_context_sms, unit_number="64", birth_year="1970")
        assert result["verified"] is False
        assert "birth_year" in result["mismatched_fields"]
        assert "unit_number" not in result["mismatched_fields"]

    @pytest.mark.asyncio
    async def test_both_mismatch(self, run_context_sms):
        """Both fields mismatch."""
        result = await _verify_resident_identity_impl(run_context_sms, unit_number="99", birth_year="1970")
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]
        assert "birth_year" in result["mismatched_fields"]

    @pytest.mark.asyncio
    async def test_case_insensitive(self, run_context_voice):
        """Unit matching is case-insensitive."""
        result = await _verify_resident_identity_impl(run_context_voice, unit_number="12a")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_building_separator_with_building_number(self):
        """Separator format 'B-2' with building number set should verify."""
        wrapper = _make_run_context("RESIDENT_ONE_SMS", "2", "1990-01-01", ab_building_number="B")
        result = await _verify_resident_identity_impl(wrapper, unit_number="B-2")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_building_separator_without_building_number(self):
        """Separator format 'B-2' without ab_building_number should mismatch."""
        wrapper = _make_run_context("RESIDENT_ONE_SMS", "2", "1990-01-01")
        result = await _verify_resident_identity_impl(wrapper, unit_number="B-2")
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]

    @pytest.mark.asyncio
    async def test_building_concatenated_with_building_number(self):
        """Concatenated format 'B2' with building number set should verify."""
        wrapper = _make_run_context("RESIDENT_ONE_SMS", "2", "1990-01-01", ab_building_number="B")
        result = await _verify_resident_identity_impl(wrapper, unit_number="B2")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_reverse_building_in_stored_unit(self):
        """User says '2' but stored unit is 'B-2' with building 'B' — should verify."""
        wrapper = _make_run_context("RESIDENT_ONE_SMS", "B-2", "1990-01-01", ab_building_number="B")
        result = await _verify_resident_identity_impl(wrapper, unit_number="2")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_alternate_unit_ignored_when_primary_matches(self, run_context_voice):
        """When primary unit matches, alternate is not needed."""
        result = await _verify_resident_identity_impl(run_context_voice, unit_number="12A", alternate_unit_number="99")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_alternate_unit_matches_when_primary_mismatches(self, run_context_voice):
        """Primary mismatches but alternate matches — should verify."""
        result = await _verify_resident_identity_impl(
            run_context_voice, unit_number="TT01", alternate_unit_number="12A"
        )
        assert result["verified"] is True
        assert run_context_voice.context.is_identity_verified("VOICE") is True

    @pytest.mark.asyncio
    async def test_alternate_unit_both_mismatch(self, run_context_voice):
        """Both primary and alternate mismatch — should fail."""
        result = await _verify_resident_identity_impl(
            run_context_voice, unit_number="TT01", alternate_unit_number="99"
        )
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]

    @pytest.mark.asyncio
    async def test_colon_stt_artifact_matches_numeric_unit(self):
        """Voice STT sometimes renders '630' as '6:30' — stripping the colon should verify."""
        wrapper = _make_run_context("RESIDENT_ONE_VOICE", "630", "1985-03-15")
        result = await _verify_resident_identity_impl(wrapper, unit_number="6:30")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_alternate_unit_none_no_regression(self, run_context_voice):
        """When alternate is None, behavior is unchanged from before."""
        result = await _verify_resident_identity_impl(run_context_voice, unit_number="99", alternate_unit_number=None)
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]

    @pytest.mark.asyncio
    async def test_alternate_unit_with_building_strip(self):
        """Alternate matches after building prefix is stripped."""
        wrapper = _make_run_context("RESIDENT_ONE_VOICE", "2", "1990-01-01", ab_building_number="B")
        result = await _verify_resident_identity_impl(wrapper, unit_number="TT01", alternate_unit_number="B-2")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_reverse_building_in_stored_unit_no_building_number(self):
        """User says '2' but stored unit is 'B-2' without ab_building_number — should mismatch."""
        wrapper = _make_run_context("RESIDENT_ONE_SMS", "B-2", "1990-01-01")
        result = await _verify_resident_identity_impl(wrapper, unit_number="2")
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]

    @pytest.mark.asyncio
    async def test_both_have_building_prefix(self):
        """User says 'B-2' and stored is 'B-2' with building 'B' — direct match."""
        wrapper = _make_run_context("RESIDENT_ONE_SMS", "B-2", "1990-01-01", ab_building_number="B")
        result = await _verify_resident_identity_impl(wrapper, unit_number="B-2")
        assert result["verified"] is True


def _make_voice_context(ab_unit_number, ab_building_number=None):
    """Helper to build a VOICE RunContextWrapper for parameterized tests."""
    kwargs = {}
    if ab_building_number is not None:
        kwargs["ab_building_number"] = ab_building_number
    return _make_run_context("RESIDENT_ONE_VOICE", ab_unit_number, "1990-01-01", **kwargs)


class TestVerificationNormalizationMismatches:
    """Formatting/normalization false negatives from prod voice.

    Callers said the correct unit but strict string comparison rejects them.
    These need improvements to normalize_unit_number (hyphen stripping,
    interior zero stripping, letter-position-agnostic comparison).
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "on_file, heard",
        [
            pytest.param("0107-A", "107A", id="leading-zero-plus-hyphen"),
            pytest.param("11308", "11-308", id="hyphen-vs-no-hyphen"),
        ],
    )
    async def test_hyphen_mismatch(self, on_file, heard):
        """Hyphens should be ignored during comparison."""
        wrapper = _make_voice_context(on_file)
        result = await _verify_resident_identity_impl(wrapper, unit_number=heard)
        assert result["verified"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "on_file, heard",
        [
            pytest.param("R01", "R1", id="leading-zero-after-letter"),
        ],
    )
    async def test_interior_leading_zero(self, on_file, heard):
        """Leading zeros after a letter prefix should be stripped (R01 -> R1)."""
        wrapper = _make_voice_context(on_file)
        result = await _verify_resident_identity_impl(wrapper, unit_number=heard)
        assert result["verified"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "on_file, heard",
        [
            pytest.param("W208", "208W", id="letter-position-swap"),
        ],
    )
    async def test_letter_position_swap(self, on_file, heard):
        """Letter at start vs end should be treated as equivalent (W208 == 208W)."""
        wrapper = _make_voice_context(on_file)
        result = await _verify_resident_identity_impl(wrapper, unit_number=heard)
        assert result["verified"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "alternate_unit_number",
        [
            pytest.param(None, id="no-alternate-transcript"),
            pytest.param("19th Street", id="misheard-ordinal-transcript"),
        ],
    )
    @pytest.mark.parametrize(
        "product",
        [
            pytest.param("RESIDENT_ONE_VOICE", id="voice"),
            pytest.param("RESIDENT_ONE_SMS", id="sms"),
            pytest.param("RESIDENT_ONE_EMAIL", id="email"),
        ],
    )
    async def test_numeric_unit_matches_same_unit_with_letter_suffix(self, alternate_unit_number, product):
        """KNCK-39596: trailing unit letters should match a stored numeric core across channels."""
        wrapper = _make_run_context(product, "19", "1990-01-01")
        kwargs = {"unit_number": "19C"}
        if alternate_unit_number is not None:
            kwargs["alternate_unit_number"] = alternate_unit_number

        result = await _verify_resident_identity_impl(
            wrapper,
            **kwargs,
        )
        assert result["verified"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "product",
        [
            pytest.param("RESIDENT_ONE_VOICE", id="voice"),
            pytest.param("RESIDENT_ONE_SMS", id="sms"),
            pytest.param("RESIDENT_ONE_EMAIL", id="email"),
        ],
    )
    async def test_numeric_unit_does_not_match_different_numeric_core_with_letter_suffix(self, product):
        """Trailing-letter fallback still requires the same numeric core."""
        wrapper = _make_run_context(product, "19", "1990-01-01")
        result = await _verify_resident_identity_impl(wrapper, unit_number="18C")
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]


class TestVerificationBuildingPrefixWithBuildingNumber:
    """Building prefix false negatives from prod voice.

    Callers include or omit building numbers. When ab_building_number is set
    in product_info, strip_building_from_input should handle these.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "on_file, heard, building",
        [
            pytest.param("107", "1-107", "1", id="caller-prepends-building-with-dash"),
            pytest.param("0302", "18-302", "18", id="caller-says-building-dash-unit"),
            pytest.param("312", "2-312", "2", id="on-file-missing-building-prefix"),
        ],
    )
    async def test_should_verify_with_building(self, on_file, heard, building):
        """When ab_building_number is set, building prefix stripping should resolve the mismatch."""
        wrapper = _make_voice_context(on_file, ab_building_number=building)
        result = await _verify_resident_identity_impl(wrapper, unit_number=heard)
        assert result["verified"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "on_file, heard, building",
        [
            pytest.param("104", "4104", None, id="no-building-104-vs-4104"),
            pytest.param("107", "1-107", None, id="no-building-107-vs-1-107"),
            pytest.param("0302", "18-302", None, id="no-building-0302-vs-18-302"),
            pytest.param("104", "4104", "4", id="numeric-building-concatenated-ambiguous"),
        ],
    )
    async def test_should_fail_without_building(self, on_file, heard, building):
        """Without ab_building_number or with ambiguous numeric concatenation — should fail."""
        wrapper = _make_voice_context(on_file, ab_building_number=building)
        result = await _verify_resident_identity_impl(wrapper, unit_number=heard)
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]


class TestVerificationCallerBuilding:
    """Tests for caller_building parameter — caller-provided building used as stripping hint."""

    @pytest.mark.asyncio
    async def test_compound_building_with_caller_building(self):
        """Stored building is '5-18' (compound), caller says '18-302'. caller_building='18' resolves it."""
        wrapper = _make_voice_context("0302", ab_building_number="5-18")
        result = await _verify_resident_identity_impl(wrapper, unit_number="18-302", caller_building="18")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_caller_building_without_stored_building(self):
        """caller_building works even when ab_building_number is not set."""
        wrapper = _make_voice_context("0302")
        result = await _verify_resident_identity_impl(wrapper, unit_number="18-302", caller_building="18")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_wrong_caller_building_does_not_make_stricter(self):
        """Wrong caller_building is ignored — direct unit match still works."""
        wrapper = _make_voice_context("302")
        result = await _verify_resident_identity_impl(wrapper, unit_number="302", caller_building="99")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_caller_building_strips_from_stored_unit(self):
        """Stored 'I-03', user says '3', caller_building='I' — strip 'I' from stored unit (KNCK-38919 reopen)."""
        wrapper = _make_voice_context("I-03", ab_building_number="0")
        result = await _verify_resident_identity_impl(wrapper, unit_number="3", caller_building="I")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_caller_building_strips_from_stored_unit_separator(self):
        """Stored 'B-5', user says '5', caller_building='B' — strip 'B' from stored unit."""
        wrapper = _make_voice_context("B-5")
        result = await _verify_resident_identity_impl(wrapper, unit_number="5", caller_building="B")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_caller_building_none_no_regression(self):
        """caller_building=None doesn't change existing behavior."""
        wrapper = _make_voice_context("64")
        result = await _verify_resident_identity_impl(wrapper, unit_number="64", caller_building=None)
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_caller_building_with_separator(self):
        """Caller says '4-104', caller_building='4', stored is '104'."""
        wrapper = _make_voice_context("104")
        result = await _verify_resident_identity_impl(wrapper, unit_number="4-104", caller_building="4")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_caller_building_concatenated_numeric_rejected(self):
        """Caller says '4104', caller_building='4', stored is '104' — ambiguous, should fail."""
        wrapper = _make_voice_context("104")
        result = await _verify_resident_identity_impl(wrapper, unit_number="4104", caller_building="4")
        assert result["verified"] is False


# NOTE: The following building prefix cases require agent-level
# handling (stripping verbose prefixes before calling verify_resident_identity)
# and cannot be tested at the tool level:
#   - 0304 vs "Building 18, Apt 304"  — agent must parse verbose format
#   - 7121 vs "7121 Sonoma Way"       — agent must strip street address
#   - 1-817 vs "A17"                  — likely STT error, not building prefix


class TestVerificationNumericAddressPrefix:
    """Numeric address prefix false negatives (KNCK-38919).

    PMS concatenates building/address number with unit letter (e.g., "7421C").
    Resident says just the letter suffix ("C" or "Apartment C").
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "on_file, heard",
        [
            pytest.param("7421C", "C", id="address-7421-unit-C"),
            pytest.param("7421C", "Apartment C", id="address-7421-apt-C"),
            pytest.param("100B", "B", id="address-100-unit-B"),
            pytest.param("2A", "A", id="address-2-unit-A"),
        ],
    )
    async def test_letter_suffix_matches_stored_address_unit(self, on_file, heard):
        """Resident provides just the letter suffix of an address+unit combo."""
        wrapper = _make_voice_context(on_file)
        result = await _verify_resident_identity_impl(wrapper, unit_number=heard)
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_building_strip_numeric_building_alpha_remainder(self):
        """strip_building_from_input handles numeric building + alpha remainder."""
        wrapper = _make_voice_context("C", ab_building_number="7421")
        # User says "7421C", stored is "C", building is "7421"
        result = await _verify_resident_identity_impl(wrapper, unit_number="7421C")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_reverse_strip_with_building_number(self):
        """Stored is '7421C', user says 'C', building is '7421' — bidirectional strip."""
        wrapper = _make_voice_context("7421C", ab_building_number="7421")
        result = await _verify_resident_identity_impl(wrapper, unit_number="C")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_wrong_letter_does_not_match(self):
        """User says 'D' but stored is '7421C' — should fail."""
        wrapper = _make_voice_context("7421C")
        result = await _verify_resident_identity_impl(wrapper, unit_number="D")
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]

    @pytest.mark.asyncio
    async def test_purely_numeric_stored_not_affected(self):
        """Purely numeric stored unit '7421' doesn't trigger address prefix strip."""
        wrapper = _make_voice_context("7421")
        result = await _verify_resident_identity_impl(wrapper, unit_number="C")
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]

    @pytest.mark.asyncio
    async def test_full_unit_still_matches(self):
        """Full unit '7421C' still matches directly."""
        wrapper = _make_voice_context("7421C")
        result = await _verify_resident_identity_impl(wrapper, unit_number="7421C")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_bare_numeric_does_not_match_full_address_style_unit(self):
        """Do not treat bare address number '7421' as equivalent to stored '7421C'."""
        wrapper = _make_voice_context("7421C")
        result = await _verify_resident_identity_impl(wrapper, unit_number="7421")
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]

    @pytest.mark.asyncio
    async def test_hyphenated_building_prefix_in_stored_unit(self):
        """Stored '2-622', caller says '622', building on record is '0' (unreliable)."""
        wrapper = _make_voice_context("2-622", ab_building_number="0")
        result = await _verify_resident_identity_impl(wrapper, unit_number="622")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_hyphenated_building_prefix_no_building_on_record(self):
        """Stored '3-100', caller says '100', no building on record."""
        wrapper = _make_voice_context("3-100")
        result = await _verify_resident_identity_impl(wrapper, unit_number="100")
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_hyphenated_prefix_wrong_unit_still_fails(self):
        """Stored '2-622', caller says '623' — should fail."""
        wrapper = _make_voice_context("2-622", ab_building_number="0")
        result = await _verify_resident_identity_impl(wrapper, unit_number="623")
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]


class TestVerificationHyphenatedBuildingPrefix:
    """Hyphen-separated building prefix in stored unit (KNCK-39312).

    PMS stores units as "building-unit" (e.g., "3-0505"). The on-record
    ab_building_number may be wrong (e.g., "0"). The fallback strips the
    numeric prefix before the hyphen and compares the remainder.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "on_file, heard, building",
        [
            pytest.param("3-0505", "505", "0", id="wrong-building-on-file"),
            pytest.param("2-622", "622", "0", id="building-2-wrong-record"),
            pytest.param("3-0505", "505", None, id="no-building-on-file"),
        ],
    )
    async def test_caller_says_unit_without_building_prefix(self, on_file, heard, building):
        """Resident says just the unit portion of a 'building-unit' stored value."""
        wrapper = _make_voice_context(on_file, ab_building_number=building)
        result = await _verify_resident_identity_impl(wrapper, unit_number=heard)
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_wrong_unit_still_fails(self):
        """Wrong unit number should still fail even with hyphen-separated stored value."""
        wrapper = _make_voice_context("3-0505", ab_building_number="0")
        result = await _verify_resident_identity_impl(wrapper, unit_number="999")
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]


class TestVerificationLetterBuildingPrefixInStoredUnit:
    """Issue #1572: stored unit has a letter building/section code prefix.

    Stored "MC 141" (no `ab_building_number`, `ab_unit_id` null). Caller says
    just the numeric tail "141". Symmetric to TestVerificationNumericAddressPrefix
    (which handles stored "7421C" + caller "C") but in the opposite direction:
    stored is letters+digits, caller provides digits.
    """

    @pytest.mark.asyncio
    async def test_letter_prefix_stored_caller_says_numeric_tail(self):
        """Verbatim case from prod trace 019e05b3: stored 'MC 141', caller '141'."""
        wrapper = _make_voice_context("MC 141")
        result = await _verify_resident_identity_impl(wrapper, unit_number="141")
        assert result["verified"] is True


class TestVerificationNumericBuildingBypass:
    """KNCK-39429: Numeric building prefix should not cause false-positive verification.

    When building is "1" and stored unit is "03" (normalized "3"), a user
    providing unit "13" should NOT be verified — "13" is not "building 1 + unit 3".
    """

    @pytest.mark.asyncio
    async def test_knck_39429_unit_13_with_building_1_stored_03(self):
        """Exact scenario from KNCK-39429: unit '13', stored '03', building '1'."""
        wrapper = _make_run_context("RESIDENT_ONE_EMAIL", "03", "09/09/1960", ab_building_number="1")
        result = await _verify_resident_identity_impl(wrapper, unit_number="13")
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]

    @pytest.mark.asyncio
    async def test_numeric_building_does_not_strip_from_wrong_unit(self):
        """Building '2', stored '05', user says '25' — should fail."""
        wrapper = _make_voice_context("05", ab_building_number="2")
        result = await _verify_resident_identity_impl(wrapper, unit_number="25")
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]

    @pytest.mark.asyncio
    async def test_numeric_building_with_separator_still_works(self):
        """Building '1', stored '03', user says '1-03' — separator format still works."""
        wrapper = _make_run_context("RESIDENT_ONE_EMAIL", "03", "09/09/1960", ab_building_number="1")
        result = await _verify_resident_identity_impl(wrapper, unit_number="1-03")
        assert result["verified"] is True


class TestVerificationSTTErrors:
    """STT transcription errors from prod voice.

    Speech-to-text misheard the unit entirely. These are NOT fixable via
    normalization and should correctly fail verification (true negatives).
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "on_file, heard",
        [
            pytest.param("2201", "TT01", id="stt-2201-as-TT01"),
            pytest.param("0915", "9612", id="stt-0915-as-9612"),
            pytest.param("0915", "10", id="stt-0915-as-10"),
            pytest.param("1082", "1092", id="stt-1082-as-1092"),
        ],
    )
    async def test_should_not_verify(self, on_file, heard):
        """STT errors should correctly fail verification."""
        wrapper = _make_voice_context(on_file)
        result = await _verify_resident_identity_impl(wrapper, unit_number=heard)
        assert result["verified"] is False
        assert "unit_number" in result["mismatched_fields"]


class TestVerificationRetry:
    """Tests for verification retry logic (KNCK-38687)."""

    # A. Settings

    def test_settings_has_max_identity_verification_attempts(self):
        """Setting exists with default of 2."""
        assert hasattr(settings, "max_identity_verification_attempts")
        assert settings.max_identity_verification_attempts == 2

    # B. Context — tested via fixture: verification_attempts starts at 0

    def test_verification_attempts_defaults_to_zero(self, run_context_sms):
        """New context starts with verification_attempts=0."""
        assert run_context_sms.context.get_verification_attempts("SMS") == 0

    # C. Counter increment

    @pytest.mark.asyncio
    async def test_failure_increments_verification_attempts(self, run_context_sms):
        """Failed verification increments attempts from 0 to 1."""
        await _verify_resident_identity_impl(run_context_sms, unit_number="99")
        assert run_context_sms.context.get_verification_attempts("SMS") == 1

    @pytest.mark.asyncio
    async def test_success_does_not_increment_verification_attempts(self, run_context_sms):
        """Successful verification leaves attempts at 0."""
        await _verify_resident_identity_impl(run_context_sms, unit_number="64")
        assert run_context_sms.context.get_verification_attempts("SMS") == 0

    @pytest.mark.asyncio
    async def test_success_after_previous_failure(self, run_context_sms):
        """First call fails (attempts=1), second succeeds (attempts stays 1, verified=true)."""
        result1 = await _verify_resident_identity_impl(run_context_sms, unit_number="99")
        assert result1["verified"] is False
        assert run_context_sms.context.get_verification_attempts("SMS") == 1

        result2 = await _verify_resident_identity_impl(run_context_sms, unit_number="64")
        assert result2["verified"] is True
        assert run_context_sms.context.get_verification_attempts("SMS") == 1

    # D. Action field in response

    @pytest.mark.asyncio
    async def test_failure_first_attempt_returns_retry_action(self, run_context_sms):
        """First failure with max_attempts=2 returns RETRY action."""
        result = await _verify_resident_identity_impl(run_context_sms, unit_number="99")
        assert result["action"] == ACTION_RETRY.format(attempts=1)

    @pytest.mark.asyncio
    async def test_failure_second_attempt_returns_failed_action(self, run_context_sms):
        """Second failure with max_attempts=2 returns FAILED action."""
        await _verify_resident_identity_impl(run_context_sms, unit_number="99")
        result = await _verify_resident_identity_impl(run_context_sms, unit_number="99")
        assert result["action"] == ACTION_FAILED.format(attempts=2)

    @pytest.mark.asyncio
    async def test_success_returns_verified_action(self, run_context_sms):
        """Successful verification returns VERIFIED action."""
        result = await _verify_resident_identity_impl(run_context_sms, unit_number="64")
        assert result["action"] == ACTION_VERIFIED

    @pytest.mark.asyncio
    async def test_max_attempts_one_returns_failed_action(self, run_context_sms, monkeypatch):
        """With max_attempts=1, first failure returns FAILED action (kill switch)."""
        monkeypatch.setattr(settings, "max_identity_verification_attempts", 1)
        result = await _verify_resident_identity_impl(run_context_sms, unit_number="99")
        assert result["action"] == ACTION_FAILED.format(attempts=1)

    @pytest.mark.asyncio
    async def test_response_has_standard_shape(self, run_context_sms):
        """Every response has exactly {verified, mismatched_fields, action}."""
        success = await _verify_resident_identity_impl(run_context_sms, unit_number="64")
        assert set(success.keys()) == {"verified", "mismatched_fields", "action"}

        run_context_sms.context.verification_attempts = {}
        failure = await _verify_resident_identity_impl(run_context_sms, unit_number="99")
        assert set(failure.keys()) == {"verified", "mismatched_fields", "action"}

    # E. Edge case

    @pytest.mark.asyncio
    async def test_birth_year_failure_also_increments_attempts(self, run_context_sms):
        """Birth year mismatch (unit correct) still counts as a failed attempt."""
        result = await _verify_resident_identity_impl(run_context_sms, unit_number="64", birth_year="1970")
        assert result["verified"] is False
        assert run_context_sms.context.get_verification_attempts("SMS") == 1
        assert result["action"] == ACTION_RETRY.format(attempts=1)


class TestMissingPayloadData:
    """Tests for KNCK-39409: when verification data is missing from the payload,
    the tool should return MISSING_DATA immediately instead of failing after retries."""

    @pytest.mark.asyncio
    async def test_missing_unit_number_empty_string(self):
        """Empty ab_unit_number returns MISSING_DATA immediately."""
        ctx = _make_run_context("resident_one_sms", ab_unit_number="", date_of_birth="01/15/1990")
        result = await _verify_resident_identity_impl(ctx, unit_number="64")
        assert result["verified"] is False
        assert result["mismatched_fields"] == ["unit_number"]
        assert result["action"] == ACTION_MISSING_DATA

    @pytest.mark.asyncio
    async def test_missing_unit_number_none(self):
        """None ab_unit_number returns MISSING_DATA immediately."""
        ctx = _make_run_context("resident_one_sms", ab_unit_number=None, date_of_birth="01/15/1990")
        result = await _verify_resident_identity_impl(ctx, unit_number="64")
        assert result["verified"] is False
        assert result["mismatched_fields"] == ["unit_number"]
        assert result["action"] == ACTION_MISSING_DATA

    @pytest.mark.asyncio
    async def test_missing_dob_with_birth_year_returns_missing_data(self):
        """Empty date_of_birth returns MISSING_DATA when birth_year is provided."""
        ctx = _make_run_context("resident_one_sms", ab_unit_number="1509", date_of_birth="")
        result = await _verify_resident_identity_impl(ctx, unit_number="1509", birth_year="1982")
        assert result["verified"] is False
        assert result["mismatched_fields"] == ["birth_year"]
        assert result["action"] == ACTION_MISSING_DATA

    @pytest.mark.asyncio
    async def test_missing_dob_none_with_birth_year_returns_missing_data(self):
        """None date_of_birth returns MISSING_DATA when birth_year is provided."""
        ctx = _make_run_context("resident_one_sms", ab_unit_number="1509", date_of_birth=None)
        result = await _verify_resident_identity_impl(ctx, unit_number="1509", birth_year="1982")
        assert result["verified"] is False
        assert result["mismatched_fields"] == ["birth_year"]
        assert result["action"] == ACTION_MISSING_DATA

    @pytest.mark.asyncio
    async def test_missing_dob_without_birth_year_still_verifies_unit(self):
        """Empty DOB doesn't matter when birth_year is not provided — unit-only verification succeeds."""
        ctx = _make_run_context("resident_one_sms", ab_unit_number="1509", date_of_birth="")
        result = await _verify_resident_identity_impl(ctx, unit_number="1509")
        assert result["verified"] is True
        assert result["mismatched_fields"] == []
        assert result["action"] == ACTION_VERIFIED

    @pytest.mark.asyncio
    async def test_missing_unit_does_not_increment_attempts(self):
        """MISSING_DATA for unit does not count as a verification attempt."""
        ctx = _make_run_context("resident_one_sms", ab_unit_number="", date_of_birth="01/15/1990")
        await _verify_resident_identity_impl(ctx, unit_number="64")
        assert ctx.context.get_verification_attempts("SMS") == 0

    @pytest.mark.asyncio
    async def test_missing_dob_does_not_increment_attempts(self):
        """MISSING_DATA for birth year does not count as a verification attempt."""
        ctx = _make_run_context("resident_one_sms", ab_unit_number="1509", date_of_birth="")
        await _verify_resident_identity_impl(ctx, unit_number="1509", birth_year="1982")
        assert ctx.context.get_verification_attempts("SMS") == 0
