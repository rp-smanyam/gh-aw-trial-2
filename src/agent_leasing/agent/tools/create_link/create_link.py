import os
from typing import Any, Literal
from urllib import parse

import structlog
from agents import RunContextWrapper, function_tool
from langsmith import traceable

from agent_leasing.api.model import Channel, StaticPaths
from agent_leasing.settings import settings

logger = structlog.get_logger(__name__)

# Import the description from the CREATE_LINK_DESCRIPTION file
description_path = os.path.join(os.path.dirname(__file__), "CREATE_LINK_DESCRIPTION.md")
with open(description_path, encoding="utf-8") as f:
    CREATE_LINK_DESCRIPTION = f.read().strip()


# Helper functions for each link type
def _create_payment_and_ledger_link(base_url: str, static_paths: StaticPaths) -> str:
    """Create link to payment and ledger page."""
    return parse.urljoin(base_url, static_paths.payment_and_ledger)


def _create_amenities_link(base_url: str, static_paths: StaticPaths) -> str:
    """Create link to amenities page."""
    return parse.urljoin(base_url, static_paths.amenities)


def _create_reservations_link(base_url: str, static_paths: StaticPaths) -> str:
    """Create link to reservations page."""
    return parse.urljoin(base_url, static_paths.reservations)


def _create_parking_link(base_url: str, static_paths: StaticPaths) -> str:
    """Create link to parking page."""
    return parse.urljoin(base_url, static_paths.parking)


def _create_package_link(base_url: str, static_paths: StaticPaths) -> str:
    """Create link to package page."""
    return parse.urljoin(base_url, static_paths.package)


def _create_community_events_link(base_url: str, static_paths: StaticPaths) -> str:
    """Create link to community events page."""
    return parse.urljoin(base_url, static_paths.community_events)


def _create_human_hand_off_link(base_url: str, static_paths: StaticPaths) -> str:
    """Create link to human hand-off/messenger page."""
    return parse.urljoin(base_url, static_paths.human_hand_off)


def _create_service_request_link(base_url: str, static_paths: StaticPaths) -> str:
    """Create link to service request page."""
    return parse.urljoin(base_url, static_paths.service_request)


def _create_front_desk_instructions_link(base_url: str, static_paths: StaticPaths) -> str:
    """Create link to front desk instructions page."""
    return parse.urljoin(base_url, static_paths.front_desk_instructions)


def _create_resident_checklist_link(base_url: str, static_paths: StaticPaths) -> str:
    """Create link to resident checklist page."""
    return parse.urljoin(base_url, static_paths.resident_checklist)


def _create_parking_passes_link(base_url: str, static_paths: StaticPaths) -> str:
    """Create link to parking passes page."""
    return parse.urljoin(base_url, static_paths.parking_passes)


def _create_community_wall_link(base_url: str, static_paths: StaticPaths) -> str:
    """Create link to community wall page."""
    return parse.urljoin(base_url, static_paths.community_wall)


def _create_single_service_request_link(base_url: str, static_paths: StaticPaths, mr_id: str | None = None) -> str:
    """Create link to single service request detail page."""
    if mr_id:
        # mrId is a literal route segment; the numeric sr_id is appended after it
        path = f"{static_paths.single_service_request.rstrip('/')}/{mr_id}"
    else:
        # No usable ID — fall back to generic SR portal page
        path = static_paths.service_request
    return parse.urljoin(base_url, path)


def _create_all_open_service_request_link(base_url: str, static_paths: StaticPaths) -> str:
    """Create link to all open service requests page."""
    return parse.urljoin(base_url, static_paths.all_open_service_request)


def _create_leasing_link(base_url: str, static_paths: StaticPaths) -> str:
    """Create link to leasing page."""
    return parse.urljoin(base_url, static_paths.leasing)


# Map link types to their corresponding helper functions
LINK_HANDLERS = {
    "payment_and_ledger": _create_payment_and_ledger_link,
    "amenities": _create_amenities_link,
    "reservations": _create_reservations_link,
    "parking": _create_parking_link,
    "package": _create_package_link,
    "community_events": _create_community_events_link,
    "human_hand_off": _create_human_hand_off_link,
    "service_request": _create_service_request_link,
    "front_desk_instructions": _create_front_desk_instructions_link,
    "parking_passes": _create_parking_passes_link,
    "community_wall": _create_community_wall_link,
    "single_service_request": _create_single_service_request_link,
    "all_open_service_request": _create_all_open_service_request_link,
    "leasing": _create_leasing_link,
}
LINK_HANDLERS_KEYS = list(LINK_HANDLERS.keys())


@traceable(run_type="tool", name="create_link")
async def _create_link_impl(
    ctx: RunContextWrapper[Any], link_type: Literal[*LINK_HANDLERS_KEYS], mr_id: str | None = None
):
    result: str
    try:
        base_url = ctx.context.ask_request.product_info.uc_portal_base_url
        static_paths = ctx.context.ask_request.product_info.static_paths

        if not base_url:
            logger.warning("Portal base URL not configured")
            result = f"Error building {link_type} link: Portal base URL not configured"

        elif not static_paths:
            logger.warning("Static paths not configured")
            result = f"Error building {link_type} link: Static paths not configured"

        else:
            handler = LINK_HANDLERS.get(link_type)
            if handler:
                # Default fallback: if the path for this link type isn't configured,
                # return the portal homepage instead of erroring
                path_attr = getattr(static_paths, link_type, None)
                if path_attr is None:
                    result = base_url
                elif link_type == "single_service_request":
                    # Only the thinker API on chat returns a numeric sr_id usable in the portal URL;
                    # MCP and email return a display SR# (e.g. "3550-1") that doesn't match
                    channel = ctx.context.ask_request.conversation_type
                    usable_mr_id = (
                        mr_id if (settings.facilities_thinker_api_enabled and channel == Channel.CHAT) else None
                    )
                    result = handler(base_url, static_paths, mr_id=usable_mr_id)
                else:
                    result = handler(base_url, static_paths)
            else:
                result = f"Error building {link_type} link: Unknown link type {link_type}"
    except Exception as e:
        logger.exception("Error building link", link_type=link_type, error=str(e))
        result = f"Error building {link_type} link: {str(e)}"

    return result


@function_tool(
    description_override=CREATE_LINK_DESCRIPTION,
)
async def create_link(
    ctx: RunContextWrapper[Any], link_type: Literal[*LINK_HANDLERS_KEYS], mr_id: str | None = None
) -> str:
    """Create a link to a specific portal page based on the link type.

    Args:
        ctx: The run context wrapper containing request information
        link_type: The type of link to create
        mr_id: Optional service request ID to substitute into single_service_request URLs

    Returns:
        The complete URL for the requested portal page
    """

    return await _create_link_impl(ctx, link_type, mr_id=mr_id)
