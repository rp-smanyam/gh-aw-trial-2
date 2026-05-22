"""
Tool for verifying resident identity by comparing user-provided values
against stored values (unit number and optionally birth year).
"""

import re
from typing import Annotated, Any

import structlog
from agents import RunContextWrapper, function_tool
from langsmith import traceable

from agent_leasing.agent.util import get_channel_from_context
from agent_leasing.settings import settings

logger = structlog.get_logger(__name__)

ACTION_VERIFIED = "VERIFIED: Resident identity confirmed. Proceed with the requested action."
ACTION_RETRY = "RETRY: Could not verify the resident on attempt {attempts}. Ask the resident to re-confirm and call verify_resident_identity again."
ACTION_FAILED = (
    "FAILED: Could not verify the resident on attempt {attempts}. Transfer to staff to resolve their request."
)
ACTION_MISSING_DATA = "MISSING_DATA: Verification data is not on file. Transfer to staff to resolve their request."

DESCRIPTION = """Verify the resident's identity before calling protected tools.

REQUIRED before: call_facilities_thinker_via_api, issue_guest_parking_pass, get_rent_information

Parameters:
- unit_number: Always required
- birth_year: Only provide if instructed by the verification requirements
- alternate_unit_number: Only for VOICE — if the raw transcript differs from the interpreted unit number, pass the alternate here
- caller_building: If the caller mentions a building number/name, pass it here. Used only to help strip building info from the unit — never makes verification stricter.

Returns {verified: true/false, mismatched_fields: [], action: string}. The action field tells you exactly what to do next — follow it. Never reveals correct values."""


def normalize_unit_number(value: str | None) -> str:
    """Normalize unit number for comparison.

    - Converts to uppercase
    - Strips whitespace
    - Removes common prefixes like 'unit', 'apt', '#'
    - Collapses internal spaces (e.g., "12 A" -> "12A")
    - Strips hyphens (e.g., "0107-A" -> "0107A", "11-308" -> "11308")
    - Strips colons (voice STT artifact, e.g., "6:30" -> "630")
    - Strips leading zeros (e.g., "03" -> "3", "0812H" -> "812H")
    - Strips interior zeros after letter prefix (e.g., "R01" -> "R1")
    - Normalizes letter position: leading letters moved to end (e.g., "W208" -> "208W")
    """
    if value is None:
        return ""

    normalized = str(value).upper().strip()

    # Remove common prefixes
    prefixes = [r"^UNIT\s*", r"^APT\.?\s*", r"^APARTMENT\s*", r"^#\s*"]
    for prefix in prefixes:
        normalized = re.sub(prefix, "", normalized, flags=re.IGNORECASE)

    # Collapse internal spaces
    normalized = re.sub(r"\s+", "", normalized)

    # Strip hyphens (formatting-only, e.g., "0107-A" -> "0107A", "11-308" -> "11308")
    normalized = normalized.replace("-", "")

    # Strip colons — voice STT occasionally renders numbers like "6:30" for "630"
    normalized = normalized.replace(":", "")

    # Strip leading zeros from the numeric prefix
    # Handles purely numeric units ("03" -> "3") and alphanumeric units ("0812H" -> "812H")
    normalized = re.sub(r"^0+(?=\d)", "", normalized)

    # Strip leading zeros after a letter prefix (e.g., "R01" -> "R1")
    normalized = re.sub(r"([A-Z])0+(?=\d)", r"\1", normalized)

    # Normalize letter position: move leading letter block to end
    # so "W208" and "208W" both normalize to "208W"
    match = re.match(r"^([A-Z]+)(\d+)$", normalized)
    if match:
        normalized = match.group(2) + match.group(1)

    return normalized


def strip_building_from_input(value: str, building_number: str | None) -> str | None:
    """Try to strip a building identifier from a unit value.

    The agent prompt handles verbose forms like "Building B Unit 2", so this
    only catches the compact forms the agent is likely to pass through:
    - Separator: "B-2", "B.2", "B 2"
    - Concatenated: "B2"

    Returns the remainder after stripping, or None if no building pattern detected.
    """
    if not building_number or not value:
        return None

    val = value.strip()
    bldg = building_number.strip().upper()

    if not bldg or not val:
        return None

    val_upper = val.upper()

    # Separator format: "B-2", "B.2", "B 2"
    sep_match = re.match(
        re.escape(bldg) + r"[-.\s](.+)$",
        val_upper,
    )
    if sep_match:
        return val[sep_match.start(1) :]

    # Concatenated format: "B2" or "7421C"
    # Allowed when remainder starts with a digit, OR when building is purely
    # numeric and remainder is purely alphabetic (unambiguous split boundary).
    if val_upper.startswith(bldg) and len(val_upper) > len(bldg):
        remainder = val[len(bldg) :]
        if remainder[0].isdigit() and not bldg.isdigit():
            return remainder
        if bldg.isdigit() and remainder.isalpha():
            return remainder

    return None


def extract_birth_year(date_of_birth: str | None) -> str | None:
    """Extract the year from a date of birth string.

    Handles formats: MM/DD/YYYY, YYYY-MM-DD, or just YYYY
    """
    if date_of_birth is None:
        return None

    date_str = str(date_of_birth).strip()

    # Try MM/DD/YYYY format
    match = re.match(r"^\d{1,2}/\d{1,2}/(\d{4})$", date_str)
    if match:
        return match.group(1)

    # Try YYYY-MM-DD format
    match = re.match(r"^(\d{4})-\d{1,2}-\d{1,2}$", date_str)
    if match:
        return match.group(1)

    # Try just year
    match = re.match(r"^(\d{4})$", date_str)
    if match:
        return match.group(1)

    return None


def strip_trailing_letter_suffix_from_input(raw_input: str | None, expected_unit: str) -> str | None:
    """Tolerate a trailing unit letter when PMS stores only the numeric core."""
    if raw_input is None or not expected_unit.isdigit():
        return None

    candidate = str(raw_input).upper().strip()

    prefixes = [r"^UNIT\s*", r"^APT\.?\s*", r"^APARTMENT\s*", r"^#\s*"]
    for prefix in prefixes:
        candidate = re.sub(prefix, "", candidate, flags=re.IGNORECASE)

    candidate = re.sub(r"\s+", "", candidate)
    candidate = candidate.replace("-", "")
    candidate = re.sub(r"^0+(?=\d)", "", candidate)

    match = re.fullmatch(r"(\d+)([A-Z])", candidate)
    if not match or match.group(1) != expected_unit:
        return None

    return expected_unit


async def _verify_resident_identity_impl(
    ctx: RunContextWrapper[Any],
    unit_number: str,
    birth_year: str | None = None,
    alternate_unit_number: str | None = None,
    caller_building: str | None = None,
) -> dict:
    """Dispatcher — runs v1 (cascading fallbacks) or v2 (candidate generation)
    based on settings.use_candidate_generation_verifier (#1491)."""
    if settings.use_candidate_generation_verifier:
        # Late import to avoid the v1↔v2 cycle (v2 imports constants from v1).
        from agent_leasing.agent.tools.verify_resident_identity_v2 import (
            _verify_resident_identity_impl_v2,
        )

        return await _verify_resident_identity_impl_v2(
            ctx, unit_number, birth_year, alternate_unit_number, caller_building
        )
    return await _verify_resident_identity_impl_v1(
        ctx, unit_number, birth_year, alternate_unit_number, caller_building
    )


@traceable(run_type="tool", name="verify_resident_identity")
async def _verify_resident_identity_impl_v1(
    ctx: RunContextWrapper[Any],
    unit_number: str,
    birth_year: str | None = None,
    alternate_unit_number: str | None = None,
    caller_building: str | None = None,
) -> dict:
    """v1 implementation — cascading fallback conditionals."""
    channel = get_channel_from_context(ctx.context)
    product_info = ctx.context.ask_request.product_info

    # Chat users are pre-authenticated
    if channel == "CHAT":
        logger.info("Skipping verification for CHAT channel (pre-authenticated)")
        return {"verified": True, "mismatched_fields": [], "action": ACTION_VERIFIED}

    # If unit number is missing from payload, verification is impossible
    expected_unit = normalize_unit_number(product_info.ab_unit_number)
    if not expected_unit:
        logger.warning("Unit number missing from product_info — cannot verify", channel=channel)
        return {"verified": False, "mismatched_fields": ["unit_number"], "action": ACTION_MISSING_DATA}

    mismatched_fields = []

    # Verify unit number
    provided_unit = normalize_unit_number(unit_number)

    logger.info(
        "Verifying unit number",
        channel=channel,
        provided_raw=unit_number,
        provided_normalized=provided_unit,
        expected_normalized=expected_unit,
    )

    # Fallback: if mismatch and building number is available, try stripping building info
    # from either the user's input or the stored unit (bidirectional)
    if provided_unit != expected_unit:
        building = getattr(product_info, "ab_building_number", None)
        if building:
            # Try stripping building from user's input (e.g., user says "B-2", stored is "2")
            extracted = strip_building_from_input(unit_number, building)
            if extracted is not None:
                provided_unit = normalize_unit_number(extracted)
                logger.info(
                    "Unit matched after stripping building prefix from input",
                    building=building,
                    extracted=extracted,
                )
            # Try stripping building from stored unit (e.g., user says "2", stored is "B-2")
            if provided_unit != expected_unit:
                extracted_expected = strip_building_from_input(product_info.ab_unit_number, building)
                if extracted_expected is not None:
                    expected_unit = normalize_unit_number(extracted_expected)
                    logger.info(
                        "Unit matched after stripping building prefix from stored value",
                        building=building,
                        extracted=extracted_expected,
                    )

    # Fallback: try stripping caller-provided building from input
    if provided_unit != expected_unit and caller_building:
        extracted = strip_building_from_input(unit_number, caller_building)
        if extracted is not None:
            caller_stripped = normalize_unit_number(extracted)
            if caller_stripped == expected_unit:
                provided_unit = caller_stripped
                logger.info(
                    "Unit matched after stripping caller-provided building from input",
                    caller_building=caller_building,
                    extracted=extracted,
                )
        # Try stripping caller-provided building from stored unit (bidirectional)
        if provided_unit != expected_unit:
            extracted_expected = strip_building_from_input(product_info.ab_unit_number, caller_building)
            if extracted_expected is not None:
                caller_stripped_expected = normalize_unit_number(extracted_expected)
                if provided_unit == caller_stripped_expected:
                    expected_unit = caller_stripped_expected
                    logger.info(
                        "Unit matched after stripping caller-provided building from stored value",
                        caller_building=caller_building,
                        extracted=extracted_expected,
                    )

    # Voice transcription fallback: try alternate unit number
    if provided_unit != expected_unit and alternate_unit_number:
        alternate_normalized = normalize_unit_number(alternate_unit_number)
        if alternate_normalized == expected_unit:
            provided_unit = alternate_normalized
            logger.info(
                "Unit matched using alternate unit number",
                alternate_raw=alternate_unit_number,
                alternate_normalized=alternate_normalized,
            )
        else:
            # Also try building-stripping on the alternate
            building = getattr(product_info, "ab_building_number", None)
            if building:
                extracted = strip_building_from_input(alternate_unit_number, building)
                if extracted is not None:
                    alternate_normalized = normalize_unit_number(extracted)
                    if alternate_normalized == expected_unit:
                        provided_unit = alternate_normalized
                        logger.info(
                            "Unit matched using alternate unit number after building strip",
                            alternate_raw=alternate_unit_number,
                            alternate_normalized=alternate_normalized,
                        )
            if provided_unit != expected_unit:
                stripped_alternate = strip_trailing_letter_suffix_from_input(alternate_unit_number, expected_unit)
                if stripped_alternate is not None:
                    provided_unit = stripped_alternate
                    logger.info(
                        "Unit matched after stripping trailing letter suffix from alternate input",
                        channel=channel,
                        expected_normalized=expected_unit,
                    )

    # Some properties store only the numeric core ("19") while the caller
    # includes a trailing letter suffix ("19C"). Trust the numeric core only
    # when the stored value is already bare numeric.
    if provided_unit != expected_unit:
        stripped_suffix = strip_trailing_letter_suffix_from_input(unit_number, expected_unit)
        if stripped_suffix is not None:
            logger.info(
                "Unit matched after stripping trailing letter suffix from input",
                channel=channel,
                provided_normalized=provided_unit,
                expected_normalized=expected_unit,
            )
            provided_unit = stripped_suffix

    # Fallback: numeric address prefix — stored unit is "7421C" (address + letter),
    # resident says "C". Strip numeric prefix from stored unit and compare.
    if provided_unit != expected_unit:
        match = re.match(r"^(\d+)([A-Z]+)$", expected_unit)
        if match and provided_unit == match.group(2):
            expected_unit = match.group(2)
            logger.info(
                "Unit matched after stripping numeric address prefix from stored value",
                stripped_prefix=match.group(1),
            )

    # Fallback: letter building/section prefix on stored unit — stored "MC 141"
    # (building/section code + numeric unit), resident says just the unit "141".
    # Direction must come from the raw stored value: after letter-position
    # normalization, "MC 141" and "7421C" both look like "141MC"/"7421C", so
    # the normalized form alone can't tell us which side is the unit. Require
    # a 2+ letter prefix to avoid misreading single-letter unit IDs like "R01".
    if provided_unit != expected_unit:
        raw_stored_upper = str(product_info.ab_unit_number or "").upper().strip()
        raw_stored_upper = re.sub(r"^(?:UNIT|APT\.?|APARTMENT|#)\s*", "", raw_stored_upper, flags=re.IGNORECASE)
        prefix_match = re.match(r"^([A-Z]{2,})[\s.\-]*(\d+)$", raw_stored_upper)
        if prefix_match:
            unit_only = normalize_unit_number(prefix_match.group(2))
            if provided_unit == unit_only:
                expected_unit = unit_only
                logger.info(
                    "Unit matched after stripping letter building prefix from stored value",
                    stripped_prefix=prefix_match.group(1),
                )

    # Fallback: hyphen-separated building prefix in stored unit — stored "2-622",
    # caller says "622". The building on record may be wrong (e.g., "0"), so we
    # cannot rely on it. Instead, if the raw stored unit has a "X-YYY" pattern
    # where X is digits, try matching against just YYY.
    if provided_unit != expected_unit:
        raw_stored = (product_info.ab_unit_number or "").strip()
        sep_match = re.match(r"^(\d+)-(.+)$", raw_stored)
        if sep_match:
            after_sep = normalize_unit_number(sep_match.group(2))
            if provided_unit == after_sep:
                expected_unit = after_sep
                logger.info(
                    "Unit matched after stripping hyphenated building prefix from stored value",
                    stripped_prefix=sep_match.group(1),
                )

    if not expected_unit or provided_unit != expected_unit:
        mismatched_fields.append("unit_number")
        logger.info("Unit number verification failed", channel=channel)

    # Verify birth year if provided
    if birth_year is not None:
        expected_year = extract_birth_year(product_info.date_of_birth)
        if not expected_year:
            logger.warning("Date of birth missing from product_info — cannot verify birth year", channel=channel)
            return {"verified": False, "mismatched_fields": ["birth_year"], "action": ACTION_MISSING_DATA}
        provided_year = str(birth_year).strip()
        if provided_year != expected_year:
            mismatched_fields.append("birth_year")
            logger.info("Birth year verification failed")

    verified = len(mismatched_fields) == 0

    if not verified:
        ctx.context.increment_verification_attempts(channel)

    # Update context verification status (per-channel)
    if verified:
        ctx.context.set_identity_verified(channel)
        if birth_year is not None:
            ctx.context.set_identity_verified_with_birth_year(channel)
        logger.info("Resident identity verified", with_birth_year=birth_year is not None)

    if verified:
        action = ACTION_VERIFIED
    else:
        attempts = ctx.context.get_verification_attempts(channel)
        can_retry = attempts < settings.max_identity_verification_attempts
        action = ACTION_RETRY.format(attempts=attempts) if can_retry else ACTION_FAILED.format(attempts=attempts)

    return {"verified": verified, "mismatched_fields": mismatched_fields, "action": action}


@function_tool(description_override=DESCRIPTION)
async def verify_resident_identity(
    ctx: RunContextWrapper[Any],
    unit_number: Annotated[str, "The unit number provided by the resident"],
    birth_year: Annotated[
        str | None, "Birth year (YYYY). Only provide if instructed by the verification requirements."
    ] = None,
    alternate_unit_number: Annotated[
        str | None,
        "Alternate unit number from voice transcript. Only provide for VOICE when the "
        "raw transcript differs from the interpreted value.",
    ] = None,
    caller_building: Annotated[
        str | None,
        "Building number/name mentioned by the caller. Used only to help match the unit — "
        "never makes verification stricter.",
    ] = None,
) -> dict:
    """Function tool wrapper that delegates to the implementation for easier testing."""
    return await _verify_resident_identity_impl(ctx, unit_number, birth_year, alternate_unit_number, caller_building)
