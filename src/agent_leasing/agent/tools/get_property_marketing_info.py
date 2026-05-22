from typing import Any

import structlog
from agents import RunContextWrapper, function_tool
from langsmith import traceable

from agent_leasing.clients.ldp import LDPError, fetch_ldp_property_data

logger = structlog.get_logger(__name__)

DESCRIPTION = """Get the property's marketing and descriptive information from the LDP cache.

Call this tool the first time you trigger the Property Q&A Workflow in this conversation for questions about:
- Property amenities, features, or community facilities (e.g., "Do you have a pool?", "Is there a gym?", "What amenities are included?", "Tell me about the property")
- Property positioning, neighborhood, or community highlights
- Property-specific policies documented in marketing materials (pet policy, parking policy, guest policy)
- Lease-related terms described in the property overview (lease break fees, notice-to-vacate, security deposit amounts, early termination)
- Fees described in property marketing materials (late fees, move-in fees, pet fees, parking fees)
- Office hours, contact information, or property management details
- Move-out procedures or security deposit return terms
- Affordable housing or income qualification requirements
- Reservable community spaces or amenity booking details

If the tool result is already in the conversation history, use it directly — do NOT call the tool
again. Do NOT answer property questions from memory or assumptions — always use the tool result.

Returns a narrative summary of property features, amenities, community positioning, and policies
as published by the property management company. Use this only for marketing or descriptive
property information — not for real-time data such as current balances, active service requests,
or calendar events.

Returns "No marketing information available." if the data cannot be retrieved — do not retry
or fabricate information in that case."""


@traceable(run_type="tool", name="get_property_marketing_info")
async def _get_property_marketing_info_impl(ctx: RunContextWrapper[Any]) -> str:
    """Fetch property marketing info from LDP. Returns a fallback message if unavailable."""
    try:
        data = await fetch_ldp_property_data(ctx.context.property_id)
        return data.get("resident_summary") or "No marketing information available."
    except LDPError:
        return "No marketing information available."


@function_tool(description_override=DESCRIPTION)
async def get_property_marketing_info(ctx: RunContextWrapper[Any]) -> str:
    """Returns the property's marketing and descriptive information from the LDP cache."""
    return await _get_property_marketing_info_impl(ctx)
