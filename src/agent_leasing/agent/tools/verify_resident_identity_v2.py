"""
Candidate-generation implementation of verify_resident_identity (#1491).

Pure implementation — the public tool entry point lives in
verify_resident_identity.py and dispatches here when
settings.use_candidate_generation_verifier is True.

Replaces the chain of fallback conditionals with a single match step: each
side generates the set of canonical forms it may legitimately match
against, and verification succeeds iff the sets intersect.

A few rules are deliberately one-sided because the same canonical pattern
can mean different things depending on which side it came from. Example:
stored "B-2" (building B, unit 2) and stored "2B" (unit 2B) both normalize
to "2B"; we look at the raw stored value to decide.
"""

import re
from typing import Any

import structlog
from agents import RunContextWrapper
from langsmith import traceable

from agent_leasing.agent.tools.verify_resident_identity import (
    ACTION_FAILED,
    ACTION_MISSING_DATA,
    ACTION_RETRY,
    ACTION_VERIFIED,
    extract_birth_year,
    normalize_unit_number,
    strip_building_from_input,
)
from agent_leasing.agent.util import get_channel_from_context
from agent_leasing.settings import settings

logger = structlog.get_logger(__name__)

_UNIT_PREFIX_RE = re.compile(r"^(?:UNIT|APT\.?|APARTMENT|#)\s*", re.IGNORECASE)


def _strip_unit_prefix(value: str) -> str:
    """Upper-case, trim, and remove a leading Unit/Apt/Apartment/# prefix."""
    return _UNIT_PREFIX_RE.sub("", value.upper().strip())


def _generate_variants(
    value: str | None,
    *,
    ab_building_number: str | None = None,
    caller_building: str | None = None,
    side: str,
) -> set[str]:
    """Return the canonical forms `value` may legitimately match against.

    All returned strings are in canonical form (see normalize_unit_number),
    so comparison is a set intersection: `expected_variants & provided_variants`.

    `side` is "stored" or "input". Some rules apply only to one side because
    the asymmetry is real (see module docstring).
    """
    if not value:
        return set()

    base = str(value).strip()
    canon = normalize_unit_number(base)
    variants = {canon}
    stripped = _strip_unit_prefix(base)

    if side == "stored":
        # Hyphen-separated numeric building prefix: "1561-2402" -> "2402",
        # "3-0505" -> "505". PMS stores building+unit; caller says unit.
        if m := re.match(r"^\d+-(.+)$", base):
            variants.add(normalize_unit_number(m.group(1)))

        # Multi-letter section/building prefix: "MC 141" -> "141".
        if m := re.match(r"^[A-Za-z]{2,}[\s.\-]*(\d+[A-Za-z]?)$", base):
            variants.add(normalize_unit_number(m.group(1)))

        # PMS-suffix letter on bare unit: "182U" -> "182", "103U" -> "103".
        #
        # Gated to 2–3 digits. Rationale at each boundary:
        #   - <2 digits: stored "2A" + caller "2" must fail — "2A" could
        #     just as easily be parsed as "address 2, unit A" (same logic
        #     that rejects "7421C" + "7421"). v1 fails this case; preserve.
        #   - >3 digits: stored "7421C" + caller "7421" must fail — "7421"
        #     reads as an address. Existing test
        #     `test_bare_numeric_does_not_match_full_address_style_unit`.
        #
        # The threshold is a tunable design choice — alternatives:
        #   (a) drop the gate entirely (full symmetry): accept the boundary
        #       cases too. Simpler, but accepts the address-as-unit risk.
        #   (b) gate on something other than digit length, e.g. require a
        #       building token to be present. Less brittle but the prod
        #       data we have (sub-gap B traces) doesn't consistently show
        #       a building token, so this would underfit.
        # The 2–3 digit window matches both the sub-gap B prod cases
        # (103U / 182U) and v1 behavior at the boundaries. Revise if prod
        # data shifts the range.
        if m := re.fullmatch(r"(\d{2,3})([A-Z])", stripped):
            variants.add(m.group(1))

    if side == "input":
        # Caller appended a suffix letter to the bare unit they live in:
        # "19" stored, caller says "19C" -> match. No digit-length gate here
        # because input-side stripping preserves existing behavior unchanged.
        if m := re.fullmatch(r"(\d+)([A-Z])", stripped):
            variants.add(m.group(1))

    # Symmetric: address-style stored "7421C" with caller saying just "C".
    # Caller may say just the letter, OR (rarely) stored may be just the letter
    # and caller may give the full form; either way the letter tail is a variant.
    if m := re.fullmatch(r"(\d+)([A-Z]+)", canon):
        variants.add(m.group(2))

    # Symmetric: building-token strip with either the on-file building or the
    # caller-provided building.
    for token in (ab_building_number, caller_building):
        if token and (s := strip_building_from_input(base, token)) is not None:
            variants.add(normalize_unit_number(s))

    variants.discard("")
    return variants


@traceable(run_type="tool", name="verify_resident_identity")
async def _verify_resident_identity_impl_v2(
    ctx: RunContextWrapper[Any],
    unit_number: str,
    birth_year: str | None = None,
    alternate_unit_number: str | None = None,
    caller_building: str | None = None,
) -> dict:
    channel = get_channel_from_context(ctx.context)
    product_info = ctx.context.ask_request.product_info

    if channel == "CHAT":
        logger.info("Skipping verification for CHAT channel (pre-authenticated)")
        return {"verified": True, "mismatched_fields": [], "action": ACTION_VERIFIED}

    ab_building = getattr(product_info, "ab_building_number", None)

    expected_variants = _generate_variants(
        product_info.ab_unit_number,
        ab_building_number=ab_building,
        caller_building=caller_building,
        side="stored",
    )
    if not expected_variants:
        logger.warning("Unit number missing from product_info — cannot verify", channel=channel)
        return {"verified": False, "mismatched_fields": ["unit_number"], "action": ACTION_MISSING_DATA}

    mismatched_fields: list[str] = []

    provided_variants: set[str] = set()
    for raw in (unit_number, alternate_unit_number):
        if raw:
            provided_variants |= _generate_variants(
                raw,
                ab_building_number=ab_building,
                caller_building=caller_building,
                side="input",
            )

    logger.info(
        "Verifying unit number",
        channel=channel,
        provided_raw=unit_number,
        alternate_raw=alternate_unit_number,
        provided_variants=sorted(provided_variants),
        expected_variants=sorted(expected_variants),
    )

    if not (expected_variants & provided_variants):
        mismatched_fields.append("unit_number")
        logger.info("Unit number verification failed", channel=channel)

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
