import asyncio

import structlog
from agents import RunContextWrapper
from langsmith import traceable

from agent_leasing.agent.tools.api_call.api_call import prefetch_active_service_requests
from agent_leasing.agent.util import call_and_save_tool, is_enabled
from agent_leasing.clients.mcp import CachingMCPServer
from agent_leasing.settings import settings

logger = structlog.getLogger()


@traceable
async def prefetch_property_overview_and_insights(
    mcp_servers: dict,
    context,
    resident_summary: str | None = None,
    prefetch_facilities_server: CachingMCPServer | None = None,
) -> list:
    """
    Prefetch insight data for the first turn.

    When settings.property_marketing_info_tool_enabled is False, sets property_data
    from the LDP resident_summary (already fetched during module loading).
    Then collects insight tools in parallel using `call_and_save_tool`. Tool outputs are saved
    in the context so they can be accessed in the prompt.

    Args:
        mcp_servers: Dictionary of MCP server instances that must contain knock_mcp_server, facilities_mcp_server,
          and loft_mcp_server
        context: Session context containing request information
        resident_summary: Resident summary from LDP. When the tool is disabled, sets context.property_data.
        prefetch_facilities_server: Pre-connected temporary facilities MCP server for SR prefetch.
          Created and connected by the caller when sr_prefetch_via_mcp=True.
    """

    if not settings.property_marketing_info_tool_enabled and resident_summary and not context.property_data:
        context.property_data = resident_summary
        logger.info("Set property_data from LDP resident_summary")

    prefetch_tools = []

    # Only fetches insights if this is the first turn (no prior OpenAI server history)
    # and the welcome is configured to include the insights section.
    if "insights" in settings.welcome_message_sections and not context.has_openai_server_history:
        if is_enabled("MR", context.disabled_modules):
            # Insight: Active Service Requests
            if settings.facilities_thinker_api_enabled is True and not settings.sr_prefetch_via_mcp:
                ctx_wrapper = RunContextWrapper(context)
                prefetch_tools.append(prefetch_active_service_requests(ctx_wrapper))
            else:
                # Use MCP — either facilities_thinker_api_enabled is False (server already
                # in mcp_servers) or sr_prefetch_via_mcp is True (pre-connected server
                # passed in via prefetch_facilities_server)
                facilities_server = mcp_servers.get("facilities_mcp_server") or prefetch_facilities_server
                if facilities_server:
                    prefetch_tools.append(
                        call_and_save_tool(
                            facilities_server,
                            "get_active_service_requests",
                            {
                                "pmc_id": int(context.ask_request.product_info.uc_company_id.id),
                                "resident_household_id": int(
                                    context.ask_request.product_info.uc_resident_household_id.id
                                ),
                                "site_id": int(context.ask_request.product_info.uc_property_id.id),
                                "resident_member_id": int(context.ask_request.product_info.uc_resident_member_id.id),
                            },
                            context,
                            "service_requests",
                            skip_pre_processors=True,
                            skip_post_processors=True,
                        )
                    )

        if is_enabled("PACKAGES", context.disabled_modules) and "loft_mcp_server" in mcp_servers:
            # Insight: Packages — prefetch is side-effect-free; suppress
            # post-processors so the task-activity emitter doesn't fire a
            # spurious "Package Questions Asked" event the resident never
            # asked about.
            prefetch_tools.append(
                call_and_save_tool(
                    mcp_servers["loft_mcp_server"],
                    "get_residents_packages",
                    {
                        "resident_id": str(context.ask_request.product_info.ab_resident_id.id),
                    },
                    context,
                    "packages",
                    skip_post_processors=True,
                )
            )

        if is_enabled("EVENTS", context.disabled_modules) and "loft_mcp_server" in mcp_servers:
            # Insight: Community Events
            prefetch_tools.append(
                call_and_save_tool(
                    mcp_servers["loft_mcp_server"],
                    "fetch_user_signed_up_community_events",
                    {
                        "resident_id": str(context.ask_request.product_info.ab_resident_id.id),
                    },
                    context,
                    "signed_up_community_events",
                    "events",
                    skip_post_processors=True,
                )
            )

    results = await asyncio.gather(*prefetch_tools, return_exceptions=True)

    for e in [r for r in results if isinstance(r, Exception)]:
        logger.error("Unable to prefetch MCP data", error=e)

    return results
