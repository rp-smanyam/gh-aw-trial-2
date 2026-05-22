"""
Pre-flight verification check for tools that require identity verification.
"""

import structlog

from agent_leasing.agent.util import get_channel_from_context
from agent_leasing.models.context import SessionScope
from agent_leasing.settings import settings

logger = structlog.get_logger(__name__)

# Tool verification requirements: tool_name -> requires_birth_year
PROTECTED_TOOLS = {
    "call_facilities_thinker_via_api": False,  # Unit only
    "create_service_request": False,  # Unit only
    "get_active_service_requests": False,  # Unit only
    "issue_guest_parking_pass": False,  # Unit only
    "get_rent_information": True,  # Unit AND birth year
    "get_fas_account_statement": True,  # Unit AND birth year
    "get_resident_autopay_and_transactions": True,  # Unit AND birth year
    "get_property_details": True,  # Unit AND birth year
    "get_custom_reminders": True,  # Unit AND birth year
    "manage_custom_reminders": True,  # Unit AND birth year
}


def check_verification_status(context: SessionScope, tool_name: str) -> tuple[bool, str | None]:
    """Check if verification requirements are met for a tool.

    Verification state is tracked per-channel so that, e.g., an SMS-verified
    session does not automatically grant access over email.

    Returns: (is_verified, error_message)
    """
    # Verification globally disabled
    if not settings.identity_verification_enabled:
        return True, None

    channel = get_channel_from_context(context)

    # Chat users are pre-authenticated
    if channel == "CHAT":
        return True, None

    # Check if tool requires verification
    if tool_name not in PROTECTED_TOOLS:
        return True, None

    requires_birth_year = PROTECTED_TOOLS[tool_name]

    # Check verification status for the current channel
    if not context.is_identity_verified(channel):
        return False, "VERIFICATION_REQUIRED: Call verify_resident_identity first."

    if requires_birth_year and not context.is_identity_verified_with_birth_year(channel):
        return False, "VERIFICATION_REQUIRED: Call verify_resident_identity with birth_year first."

    return True, None
