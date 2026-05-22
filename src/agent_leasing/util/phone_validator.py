"""Phone number validation utilities using Google's libphonenumber."""

from __future__ import annotations

import phonenumbers
from phonenumbers import NumberParseException


def validate_and_normalize_phone(phone: str, default_region: str = "US") -> str:
    """
    Validate and normalize a phone number to E.164 format.

    Note: this will fail validation on fake numbers: e.g., (555) 555-5555 or (123) 456-7890

    Args:
        phone: Phone number string to validate
        default_region: Default country code to use if not specified (default: "US")

    Returns:
        Phone number in E.164 format (e.g., "+12025551234")

    Raises:
        ValueError: If the phone number is empty, invalid, or cannot be parsed
    """
    if not phone or not phone.strip():
        raise ValueError("Phone number cannot be empty")

    phone = phone.strip()

    try:
        # Parse the phone number with the default region
        parsed = phonenumbers.parse(phone, default_region)

        # Validate that the number is actually valid
        if not phonenumbers.is_valid_number(parsed):
            raise ValueError(f"Invalid phone number: '{phone}'. Please provide a valid phone number.")

        # Return in E.164 format
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)

    except NumberParseException as e:
        raise ValueError(
            f"Could not parse phone number '{phone}': {e}. "
            f"Please provide a valid phone number in a recognizable format."
        )
