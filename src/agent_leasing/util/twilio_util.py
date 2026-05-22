"""Shared Twilio utilities."""

import structlog
from fastapi import HTTPException
from fastapi.datastructures import FormData
from twilio.request_validator import RequestValidator

from agent_leasing.settings import settings

logger = structlog.getLogger()


async def validate_twilio_request(url: str, form: FormData, signature: str):
    """Validate a Twilio request using Twilio's RequestValidator.

    Args:
        url: The full URL of the request
        form: The form data from the request
        signature: The X-Twilio-Signature header value

    Raises:
        HTTPException: If the signature validation fails
    """
    if settings.environment in ["local", "dev"]:
        return
    validator = RequestValidator(settings.twilio_auth_token)
    valid = validator.validate(url, form, signature)
    if not valid:
        logger.error(f"Twilio request validation failed for {url}")
        raise HTTPException(status_code=403, detail="Invalid Twilio Signature")


def get_twilio_credentials() -> tuple[str, str, str]:
    """
    Validate that the Twilio credentials exist.

    Returns:
        tuple[str, str, str]: (api_key, api_secret, account_sid)

    Raises:
        ValueError: If any Twilio credentials are not configured
    """
    # Get Twilio credentials from settings
    account_sid = settings.knock_twilio_account_sid
    api_key = settings.knock_twilio_api_key
    api_secret = settings.knock_twilio_api_secret

    if not all([api_key, api_secret, account_sid]):
        raise ValueError(
            f"Twilio credentials are not configured: "
            f"api_key='{api_key}', "
            f"api_secret='{api_secret}', "
            f"account_sid='{account_sid}'"
        )

    return api_key, api_secret, account_sid
