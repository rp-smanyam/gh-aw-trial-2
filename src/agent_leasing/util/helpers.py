import base64
import hashlib
import json
import re
from datetime import datetime
from datetime import time as dt_time
from zoneinfo import ZoneInfo

import structlog
from num2words import num2words

from agent_leasing.api.model import OfficeHour
from agent_leasing.settings import settings

logger = structlog.getLogger()


def encode_object(obj: dict) -> str:
    return base64.b64encode(json.dumps(obj).encode()).decode()


def decode_object(encoded: str) -> dict:
    return json.loads(base64.b64decode(encoded).decode())


def get_token_hash() -> str:
    return hashlib.sha256(settings.identity_secret_token.encode()).hexdigest()


CURRENCY_PATTERN = r"(?<!\d)(?:\$|USD\s*)([\d,]+(?:\.\d{1,2})?)(?!\d)"
NUMBER_PATTERN = r"(?<!\d)(\d+(?:,\d{3})*(?:\.\d+)?)(?!\d)"


_GREETING_PUNCT_ORPHAN = re.compile(r"\s+([,.!?;:])")
_GREETING_WHITESPACE = re.compile(r"\s{2,}")


def resolve_greeting_placeholders(
    greeting: str | None,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    property_name: str | None = None,
) -> str | None:
    """Substitute [first_name]/[last_name]/[property_name] tokens used in KB custom greetings.

    The realtime model is told to say the custom greeting verbatim, so the placeholders
    have to be resolved before the string reaches the prompt. Missing values collapse to
    an empty string; residual orphan punctuation ("Hello , welcome") and double-spaces
    are cleaned so TTS doesn't stumble on them.
    """
    if not greeting:
        return greeting
    resolved = (
        greeting.replace("[first_name]", first_name or "")
        .replace("[last_name]", last_name or "")
        .replace("[property_name]", property_name or "")
    )
    resolved = _GREETING_PUNCT_ORPHAN.sub(r"\1", resolved)
    resolved = _GREETING_WHITESPACE.sub(" ", resolved)
    return resolved.strip()


def humanize_numbers(text):
    def replacement(match):
        number_str = match.group(1)
        number_str_clean = number_str.replace(",", "")

        try:
            number = float(number_str_clean)
        except ValueError:
            return match.group(0)

        if match.group(0).startswith("$") or "USD" in match.group(0):
            # TODO: add language detection based on the language code
            # TODO: add currency detection based on the currency in the response
            return num2words(number, to="currency", lang="en", currency="USD")
        else:
            return num2words(number)

    text = re.sub(CURRENCY_PATTERN, replacement, text)
    text = re.sub(NUMBER_PATTERN, replacement, text)
    return text


def is_office_currently_open(
    office_hours: dict[str, OfficeHour] | None,
    property_timezone: str | None,
    now: datetime | None = None,
) -> bool | None:
    """Check if the property office is currently open based on office hours and timezone.

    Returns True (open), False (closed), or None (insufficient data — fail open).
    """
    if not office_hours or not property_timezone:
        return None

    try:
        tz = ZoneInfo(property_timezone)
    except Exception:
        logger.warning("Invalid property_timezone for office hours check", timezone=property_timezone)
        return None

    try:
        if now is None:
            now_local = datetime.now(tz)
        elif now.tzinfo is not None:
            now_local = now.astimezone(tz)
        else:
            # Naive datetime (e.g. from SessionScope.current_time) — treat as local to property timezone
            now_local = now.replace(tzinfo=tz)
        day_key = str(now_local.isoweekday())

        day_hours = office_hours.get(day_key)
        if day_hours is None:
            return None

        if not day_hours.is_active:
            return False

        if not day_hours.start_time or not day_hours.end_time:
            return None

        start = dt_time.fromisoformat(day_hours.start_time)
        end = dt_time.fromisoformat(day_hours.end_time)
        return start <= now_local.time() < end
    except Exception:
        logger.warning("Error checking office hours", exc_info=True)
        return None
