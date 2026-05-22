"""
Shared date helper functions for test files.

These helpers generate dates relative to a fixed base date, ensuring consistency
between test expected outputs, MCP stub responses, and the agent's current_time
fixture. Use month offsets for longer periods (lease terms), day offsets for
short-term events.

IMPORTANT: TEST_BASE_DATE must match the `current_time` fixture in conftest.py.
"""

from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta

# Fixed base date for all test date calculations.
# Must stay in sync with the `current_time` fixture in tests/conftest.py.
TEST_BASE_DATE = datetime(2025, 9, 2, 11, 0)


def _get_offset_date(months: int = 0, days: int = 0) -> datetime:
    """Get a date offset from the fixed test base date by months and/or days."""
    base = TEST_BASE_DATE
    if months:
        base = base + relativedelta(months=months)
    if days:
        base = base + timedelta(days=days)
    return base


def generate_datetime_string(
    days: int = 0,
    hour: int = 0,
    minute: int = 0,
    tz_offset: str = "+00:00",
    months: int = 0,
) -> str:
    """
    Generate an ISO 8601 datetime string relative to today.

    Args:
        days: Number of days to offset
        hour: Hour of day (0-23)
        minute: Minute (0-59)
        tz_offset: Timezone offset string (e.g., "+00:00", "-05:00", "-07:00")
        months: Number of months to offset (for longer periods)

    Returns:
        ISO 8601 datetime string like "2025-06-15T14:30:00-05:00"
    """
    base_date = _get_offset_date(months, days)
    return base_date.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat() + tz_offset


def generate_date_iso(months: int = 0, days: int = 0) -> str:
    """
    Generate an ISO date string (YYYY-MM-DD) relative to today.

    Args:
        months: Number of months to offset
        days: Number of days to offset

    Returns:
        Date string like "2025-06-15"
    """
    return _get_offset_date(months, days).strftime("%Y-%m-%d")


def generate_date_mmddyyyy(days: int = 0, months: int = 0) -> str:
    """
    Generate a date string in MM/DD/YYYY format relative to today.

    Args:
        days: Number of days to offset
        months: Number of months to offset

    Returns:
        Date string like "06/15/2025"
    """
    return _get_offset_date(months, days).strftime("%m/%d/%Y")


def generate_human_date(days: int = 0, months: int = 0, include_year: bool = True) -> str:
    """
    Generate a human-readable date string relative to today.

    Args:
        days: Number of days to offset
        months: Number of months to offset
        include_year: Whether to include the year in the output

    Returns:
        Date string like "June 15, 2025" or "June 15"
    """
    base_date = _get_offset_date(months, days)
    if include_year:
        return base_date.strftime("%B %d, %Y")
    else:
        return base_date.strftime("%B %d")


def format_event_date(
    days: int = 0,
    start_hour: int = 0,
    start_minute: int = 0,
    end_hour: int = 0,
    end_minute: int = 0,
    months: int = 0,
) -> str:
    """
    Format a human-readable event date/time string with start and end times.

    Args:
        days: Number of days to offset
        start_hour: Start hour (0-23)
        start_minute: Start minute (0-59)
        end_hour: End hour (0-23)
        end_minute: End minute (0-59)
        months: Number of months to offset

    Returns:
        String like "Jun 15, 2025 2:00 PM -  4:00 PM"
    """
    base_date = _get_offset_date(months, days)
    month_abbr = base_date.strftime("%b")
    day = base_date.day
    year = base_date.year
    start_time = base_date.replace(hour=start_hour, minute=start_minute).strftime("%-I:%M %p")
    end_time = base_date.replace(hour=end_hour, minute=end_minute).strftime("%-I:%M %p")
    return f"{month_abbr} {day}, {year} {start_time} -  {end_time}"
