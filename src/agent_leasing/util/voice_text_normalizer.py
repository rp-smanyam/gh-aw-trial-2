"""Voice text normalization for TTS output.

Provides per-type normalizers that convert structured data values into
spoken-form text suitable for text-to-speech engines. Used by MCP
post-processors (for tool outputs) and as a safety-net on thinker responses.
"""

import re
from typing import Any

import phonenumbers
from num2words import num2words

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Currency: $1,234.56, USD 1234.56, ($1,234.56), -$1,234.56
# Group 1: opening parenthesis (empty unless accounting-style negative)
# Group 2: minus sign (empty unless explicit negative)
# Group 3: numeric amount
# Group 4: closing parenthesis (empty unless accounting-style negative)
_CURRENCY_RE = re.compile(r"(\()?(-)?(?:\$|USD\s*)([\d,]+(?:\.\d{1,2})?)(\))?(?!\d)")

# ISO date: 2025-10-13 (optionally with time component)
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:T[\d:.Z+-]+)?\b")

# US date: 05/09/2025
_US_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")

# Service request / reference IDs: SR 1234-5, REF 9876, etc.
_ID_PREFIX_RE = re.compile(r"\b(SR|REF|WO|ID|PKG|TRK)[\s#-]*[\dA-Za-z][\dA-Za-z\-]+", re.IGNORECASE)

# Phone in E.164: +15103810111
_E164_RE = re.compile(r"\+\d{10,15}")

# Standalone digit-dash patterns that look like IDs: 1234-5, 12345-678
_DIGIT_DASH_RE = re.compile(r"\b\d+(?:-\d+)+\b")

# General numbers: 1234, 1,234, 12.5, 123456 — but NOT inside dates or phone numbers
_NUMBER_RE = re.compile(r"(?<!\d)(\d+(?:,\d{3})*(?:\.\d+)?)(?!\d)")

# Month names for date conversion
_MONTHS = [
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

# Ordinal suffixes for days
_ORDINALS = {
    1: "first",
    2: "second",
    3: "third",
    4: "fourth",
    5: "fifth",
    6: "sixth",
    7: "seventh",
    8: "eighth",
    9: "ninth",
    10: "tenth",
    11: "eleventh",
    12: "twelfth",
    13: "thirteenth",
    14: "fourteenth",
    15: "fifteenth",
    16: "sixteenth",
    17: "seventeenth",
    18: "eighteenth",
    19: "nineteenth",
    20: "twentieth",
    21: "twenty first",
    22: "twenty second",
    23: "twenty third",
    24: "twenty fourth",
    25: "twenty fifth",
    26: "twenty sixth",
    27: "twenty seventh",
    28: "twenty eighth",
    29: "twenty ninth",
    30: "thirtieth",
    31: "thirty first",
}

# Digit word map
_DIGIT_WORDS = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
}

# Explicit per-field normalization mapping.
# Only fields listed here are normalized — no heuristic guessing.
# These map to actual fields from MCP tool responses (see tests/stubbed_mcp.py).
FIELD_NORMALIZATIONS: dict[str, str] = {
    # get_rent_information (new format: onesite_new_rent_format=True)
    "current_balance": "currency",
    "past_due_balance": "currency",
    # get_rent_information (old format: onesite_new_rent_format=False)
    "balance": "currency",
    "pending_balance": "currency",
    "total_balance_due": "currency",  # computed by modify_get_rent_information
    # get_rent_information (both formats)
    "rent": "currency",
    "rent_due_date": "date",
    # fetch_community_events / fetch_user_signed_up_community_events
    "startDate": "date",
    "endDate": "date",
    "price": "currency",
    # get_lease_term_information
    "lease_start": "date",
    "lease_end": "date",
    # get_active_service_requests / create_service_request
    "sr_id": "id",
    # get_residents_packages
    "trackingNumber": "id",
    # issue_guest_parking_pass
    "dateInserted": "date",
    "validFrom": "date",
    "validTo": "date",
}

_NORMALIZERS: dict[str, callable] = {
    "currency": lambda v: normalize_currency(v),
    "date": lambda v: normalize_date(v),
    "phone": lambda v: normalize_phone(v),
    "id": lambda v: normalize_id(v),
}


# ---------------------------------------------------------------------------
# Individual normalizers
# ---------------------------------------------------------------------------


def normalize_currency(value: str) -> str:
    """Convert currency strings to spoken form.

    "$123.45" -> "one hundred twenty three dollars and forty five cents"
    "($79.11)" -> "negative seventy nine dollars and eleven cents"
    "-$79.11" -> "negative seventy nine dollars and eleven cents"
    """

    def _replace(match: re.Match) -> str:
        open_paren, minus, number_str, close_paren = match.groups()
        is_negative = bool(open_paren and close_paren) or bool(minus)
        number_str = number_str.replace(",", "")
        try:
            number = float(number_str)
            spoken = num2words(number, to="currency", lang="en", currency="USD")
            return f"negative {spoken}" if is_negative else spoken
        except (ValueError, OverflowError):
            return match.group(0)

    return _CURRENCY_RE.sub(_replace, value)


def normalize_phone(value: str) -> str:
    """Convert phone numbers to digit-by-digit spoken form.

    "+15103810111" -> "plus one five one zero three eight one zero one one one"
    Special case: "911" -> "nine one one"
    """
    # Special case for 911
    if value.strip() == "911":
        return "nine one one"

    # Try to detect phone numbers using phonenumbers library
    try:
        parsed = phonenumbers.parse(value, "US")
        if phonenumbers.is_valid_number(parsed):
            # Format as E.164 and convert digit-by-digit
            e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            return _digits_to_words(e164)
    except phonenumbers.NumberParseException:
        pass

    # Fallback: if it matches E.164 pattern, convert digit-by-digit
    if _E164_RE.fullmatch(value.strip()):
        return _digits_to_words(value.strip())

    return value


def normalize_date(value: str) -> str:
    """Convert date strings to spoken form.

    "2025-10-13" -> "October thirteenth, twenty twenty five"
    "05/09/2025" -> "May ninth, twenty twenty five"
    """
    # Try ISO format first
    iso_match = _ISO_DATE_RE.search(value)
    if iso_match:
        year, month, day = int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))
        return _ISO_DATE_RE.sub(lambda m: _format_date(year, month, day), value, count=1)

    # Try US format
    us_match = _US_DATE_RE.search(value)
    if us_match:
        month, day, year = int(us_match.group(1)), int(us_match.group(2)), int(us_match.group(3))
        return _US_DATE_RE.sub(lambda m: _format_date(year, month, day), value, count=1)

    return value


def normalize_id(value: str) -> str:
    """Convert ID-like strings to digit-by-digit spoken form.

    "SR 1234-5" -> "S R one two three four dash five"
    """
    result = []
    for char in value:
        if char.isdigit():
            result.append(_DIGIT_WORDS[char])
        elif char == "-":
            result.append("dash")
        elif char == "#":
            result.append("number")
        elif char == " ":
            # Only add space separator, don't append a word
            pass
        elif char.isalpha():
            result.append(char.upper())
        else:
            result.append(char)
    return " ".join(result).strip()


def normalize_number(value: str) -> str:
    """Context-aware number normalization.

    Small numbers (< 10000) get word form: "123" -> "one hundred and twenty-three"
    Large numbers get digit-by-digit: "1234567" -> "one two three four five six seven"
    """

    def _replace(match: re.Match) -> str:
        number_str = match.group(1).replace(",", "")
        try:
            number = float(number_str)
            # Large numbers (likely IDs) get digit-by-digit
            if number >= 10000 and "." not in number_str:
                return _digits_to_words(number_str)
            return num2words(number)
        except (ValueError, OverflowError):
            return match.group(0)

    return _NUMBER_RE.sub(_replace, value)


def normalize_field_value(value: str, field_name: str | None = None) -> str:
    """Apply the normalizer mapped to *field_name*, if any.

    Only fields explicitly listed in ``FIELD_NORMALIZATIONS`` are transformed.
    Unknown or unmapped fields are returned unchanged — no heuristic guessing.
    """
    if not isinstance(value, str) or not value.strip():
        return value

    if not field_name:
        return value

    norm_type = FIELD_NORMALIZATIONS.get(field_name) or FIELD_NORMALIZATIONS.get(field_name.lower())
    if norm_type is None:
        return value

    normalizer = _NORMALIZERS.get(norm_type)
    if normalizer is None:
        return value

    return normalizer(value)


# ---------------------------------------------------------------------------
# Bulk normalization for JSON structures and free text
# ---------------------------------------------------------------------------


def normalize_json_values(data: Any, parent_key: str | None = None) -> Any:
    """Recursively walk a JSON structure and normalize string values."""
    if isinstance(data, dict):
        return {k: normalize_json_values(v, parent_key=k) for k, v in data.items()}
    if isinstance(data, list):
        return [normalize_json_values(item, parent_key=parent_key) for item in data]
    if isinstance(data, str):
        return normalize_field_value(data, field_name=parent_key)
    return data


def voice_text_normalize(text: str) -> str:
    """Normalize free-form text for voice TTS output.

    This is the safety-net normalizer that runs on thinker responses.
    It applies all normalizations in sequence to free-form text.
    """
    if not text:
        return text

    # 1. Currency (must be before general numbers)
    text = normalize_currency(text)

    # 2. Dates (ISO then US)
    text = _normalize_dates_in_text(text)

    # 3. Phone numbers in text (E.164 format)
    text = _normalize_phones_in_text(text)

    # 4. ID patterns in text
    text = _normalize_ids_in_text(text)

    # 5. Remaining numbers
    text = normalize_number(text)

    return text


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _digits_to_words(text: str) -> str:
    """Convert a string with digits to word-per-digit form, preserving non-digit chars."""
    result = []
    for char in text:
        if char.isdigit():
            result.append(_DIGIT_WORDS[char])
        elif char == "+":
            result.append("plus")
        elif char == "-":
            result.append("dash")
        else:
            result.append(char)
    return " ".join(result)


def _format_date(year: int, month: int, day: int) -> str:
    """Format a date into spoken form: 'October thirteenth, twenty twenty five'."""
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return f"{month}/{day}/{year}"

    month_name = _MONTHS[month]
    day_ordinal = _ORDINALS.get(day, str(day))

    # Convert year to spoken form
    if 2000 <= year <= 2099:
        century = year // 100  # 20
        remainder = year % 100
        if remainder == 0:
            year_spoken = num2words(year)
        elif remainder < 10:
            # 2005 -> "twenty oh five"
            year_spoken = f"{num2words(century)} oh {num2words(remainder)}"
        else:
            # 2025 -> "twenty twenty five"
            year_spoken = f"{num2words(century)} {num2words(remainder)}"
    else:
        year_spoken = num2words(year)

    return f"{month_name} {day_ordinal}, {year_spoken}"


def _normalize_dates_in_text(text: str) -> str:
    """Replace date patterns in free text."""

    def _iso_replace(match: re.Match) -> str:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        return _format_date(year, month, day)

    def _us_replace(match: re.Match) -> str:
        month, day, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        return _format_date(year, month, day)

    text = _ISO_DATE_RE.sub(_iso_replace, text)
    text = _US_DATE_RE.sub(_us_replace, text)
    return text


def _normalize_phones_in_text(text: str) -> str:
    """Replace E.164 phone numbers in free text."""

    def _replace(match: re.Match) -> str:
        return _digits_to_words(match.group(0))

    return _E164_RE.sub(_replace, text)


def _normalize_ids_in_text(text: str) -> str:
    """Replace ID patterns in free text."""

    def _replace(match: re.Match) -> str:
        return normalize_id(match.group(0))

    return _ID_PREFIX_RE.sub(_replace, text)
