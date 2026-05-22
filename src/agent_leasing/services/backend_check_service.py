import asyncio
import json
import pathlib
import statistics
import time

from agent_leasing.api.auth.auth_helper import (
    get_facilities_mcp_auth_token,
    get_knock_mcp_auth_token,
    get_loft_mcp_auth_token,
    get_onsite_mcp_auth_token,
)
from agent_leasing.api.model import Persona
from agent_leasing.api.util import perform_api_call
from agent_leasing.clients.mcp import CachingMCPServer
from agent_leasing.settings import settings


def _format_exception_reason(exc: Exception, fallback: str) -> str:
    """Return a non-empty reason string for an exception."""
    message = str(exc).strip()
    if message:
        return message
    return f"{fallback}: {type(exc).__name__}"


def _load_healthcheck_ids() -> dict:
    """Load healthcheck IDs from the environment-specific example request file."""
    example_dir = pathlib.Path(__file__).parents[1] / "api" / "example_data" / "resident" / "chat"

    env = settings.environment
    env_file_map = {
        "alpha": "example_ask_request_ll.alpha.json",
        "beta": "example_ask_request_ll.beta.json",
    }
    filename = env_file_map.get(env, "example_ask_request_ll.json")
    example_file = example_dir / filename

    with open(example_file, encoding="utf-8") as f:
        data = json.load(f)

    product_info = data["product_info"]
    return {
        "knock_property_id": int(product_info["knock_property_id"]),
        "knock_resident_id": int(product_info["knock_resident_id"]),
        "uc_company_id": int(product_info["uc_company_id"]["id"]),
        "uc_property_id": int(product_info["uc_property_id"]["id"]),
        "uc_resident_household_id": int(product_info["uc_resident_household_id"]["id"]),
        "uc_resident_member_id": int(product_info["uc_resident_member_id"]["id"]),
        "ab_resident_id": int(product_info["ab_resident_id"]["id"]),
        "uc_community_id": int(product_info["uc_community_id"]["id"]),
        "ab_unit_id": int(product_info["ab_unit_id"]["id"]),
    }


MCP_HEALTHCHECK_IDS = None
MCP_HEALTHCHECK_IDS_ERROR = None


def _get_mcp_healthcheck_ids():
    """Lazily load and cache MCP healthcheck IDs, capturing any loading errors."""
    global MCP_HEALTHCHECK_IDS, MCP_HEALTHCHECK_IDS_ERROR
    if MCP_HEALTHCHECK_IDS is not None:
        return MCP_HEALTHCHECK_IDS, None
    if MCP_HEALTHCHECK_IDS_ERROR is not None:
        return None, MCP_HEALTHCHECK_IDS_ERROR
    try:
        MCP_HEALTHCHECK_IDS = _load_healthcheck_ids()
        return MCP_HEALTHCHECK_IDS, None
    except Exception as exc:
        MCP_HEALTHCHECK_IDS_ERROR = f"Failed to load MCP healthcheck IDs: {exc}"
        return None, MCP_HEALTHCHECK_IDS_ERROR


async def build_mcp_dependency_status() -> dict:
    """Build the health status of all backend dependencies (MCP servers and REST APIs)."""
    ids, load_error = _get_mcp_healthcheck_ids()
    if load_error is not None:
        return {
            "status": "degraded",
            "dependencies": [],
            "details": {
                "reason": "Unable to load MCP healthcheck IDs from example data file.",
                "error": load_error,
            },
        }

    mcp_definitions = [
        {
            "key": "mcp-facilities",
            "server_url": settings.facilities_mcp_server,
            "auth_function": get_facilities_mcp_auth_token if settings.facilities_mcp_auth_enabled else None,
            "tools": [
                (
                    "get_active_service_requests",
                    {
                        "pmc_id": ids["uc_company_id"],
                        "resident_household_id": ids["uc_resident_household_id"],
                        "site_id": ids["uc_property_id"],
                        "resident_member_id": ids["uc_resident_member_id"],
                    },
                )
            ],
        },
        {
            "key": "mcp-knock",
            "server_url": settings.knock_mcp_server,
            "auth_function": get_knock_mcp_auth_token if settings.knock_mcp_auth_enabled else None,
            "tools": [
                ("check_resident_sms_opt_in_status", {"resident_id": ids["knock_resident_id"]}),
                (
                    "get_property_overview",
                    {"property_id": ids["knock_property_id"], "renter_type": Persona.RESIDENT.value},
                ),
            ],
        },
        {
            "key": "mcp-loft",
            "server_url": settings.loft_mcp_server,
            "auth_function": get_loft_mcp_auth_token if settings.loft_mcp_auth_enabled else None,
            "tools": [
                (
                    "fetch_community_events",
                    {
                        "resident_id": str(ids["ab_resident_id"]),
                        "community_id": str(ids["uc_community_id"]),
                    },
                ),
                ("fetch_user_signed_up_community_events", {"resident_id": str(ids["ab_resident_id"])}),
                ("get_residents_packages", {"resident_id": str(ids["ab_resident_id"])}),
            ],
        },
        {
            "key": "mcp-onsite",
            "server_url": settings.onesite_mcp_server,
            "auth_function": get_onsite_mcp_auth_token if settings.onesite_mcp_auth_enabled else None,
            "tools": [
                (
                    "get_lease_term_information",
                    {
                        "company_id": ids["uc_company_id"],
                        "property_id": ids["uc_property_id"],
                        "resident_household_id": ids["uc_resident_household_id"],
                    },
                ),
                (
                    "get_rent_information",
                    {
                        "company_id": ids["uc_company_id"],
                        "property_id": ids["uc_property_id"],
                        "resident_household_id": ids["uc_resident_household_id"],
                        "resident_member_id": ids["uc_resident_member_id"],
                    },
                ),
            ],
        },
    ]

    # Run all MCP checks and REST API checks concurrently
    mcp_tasks = [
        _check_mcp_server(
            definition["server_url"],
            definition["auth_function"],
            definition["tools"],
        )
        for definition in mcp_definitions
    ]

    facilities_api_task = _check_rest_api(
        "facilities-api",
        host=settings.facilities_thinker_api_host,
        endpoint="/facilities-resident-thinker/v2/thinker",
        method="POST",
        auth_server="facilities",
        payload={
            "resident_identifiers": {
                "pmc_id": ids["uc_company_id"],
                "site_id": ids["uc_property_id"],
                "resident_household_id": ids["uc_resident_household_id"],
                "resident_member_id": ids["uc_resident_member_id"],
                "ab_community_id": ids["uc_community_id"],
                "ab_resident_id": ids["ab_resident_id"],
            },
            "relevant_context_from_last_user_message": "What are my open service requests?",
            "channel": "sms",
        },
    )

    all_results = await asyncio.gather(*mcp_tasks, facilities_api_task)

    # Unpack results: first N are MCP servers, last one is the REST API
    mcp_results = all_results[: len(mcp_definitions)]
    facilities_api_status = all_results[-1]

    mcp_tools: dict[str, dict] = {}
    degraded_details: list[dict[str, str]] = []
    overall_degraded = False

    for definition, server_status in zip(mcp_definitions, mcp_results):
        mcp_tools[definition["key"]] = server_status
        if server_status["status"] != "healthy":
            overall_degraded = True
            if reason := server_status.get("reason"):
                degraded_details.append({"name": definition["key"], "reason": reason})

    apis = [{"facilities-api": facilities_api_status}]
    if facilities_api_status["status"] != "healthy":
        overall_degraded = True
        if reason := facilities_api_status.get("reason"):
            degraded_details.append({"name": "facilities-api", "reason": reason})

    details: dict[str, object] = {}
    if degraded_details:
        details["degraded"] = degraded_details
    elif not overall_degraded:
        details["message"] = "MCP and API checks are healthy"

    return {
        "status": "degraded" if overall_degraded else "healthy",
        "mcp_tools": mcp_tools,
        "apis": apis,
        "details": details,
    }


async def _check_mcp_server(
    server_url: str,
    auth_function,
    tools: list[tuple[str, dict]],
) -> dict:
    """Check the health of an MCP server by calling each configured tool."""
    tool_results = []
    degraded_reasons: list[str] = []
    num_samples = 1

    try:
        async with CachingMCPServer(
            name="MCP Server",
            params={"url": server_url},
            auth_function=auth_function,
            cache_tools_list=False,
            client_session_timeout_seconds=20,
        ) as mcp_server:
            for tool_name, arguments in tools:
                latencies = []
                status = "healthy"
                reason = None

                for _ in range(num_samples):
                    start = time.perf_counter()
                    try:
                        result = await mcp_server.call_tool(tool_name, arguments)
                        if result is None or result.isError:
                            status = "degraded"
                            reason = result.content[0].text if result and result.content else "Tool returned an error"
                        latencies.append(int((time.perf_counter() - start) * 1000))
                    except Exception as exc:
                        status = "degraded"
                        reason = _format_exception_reason(exc, "MCP tool error")
                        latencies.append(int((time.perf_counter() - start) * 1000))

                p50 = int(statistics.median(latencies)) if latencies else None
                p99 = (
                    int(sorted(latencies)[int(len(latencies) * 0.99)] if len(latencies) > 1 else latencies[0])
                    if latencies
                    else None
                )

                if status != "healthy":
                    degraded_reasons.append(f"{tool_name}: {reason}")

                tool_results.append(
                    {
                        "name": tool_name,
                        "status": status,
                        "latency_ms": {"p50": p50, "p99": p99},
                    }
                )
    except Exception as exc:
        reason = _format_exception_reason(exc, "Failed to connect to MCP server")
        return {
            "status": "degraded",
            "reason": reason,
            "tools": [
                {
                    "name": tool_name,
                    "status": "degraded",
                    "reason": reason,
                    "latency_ms": {"p50": None, "p99": None},
                }
                for tool_name, _ in tools
            ],
        }

    return {
        "status": "degraded" if degraded_reasons else "healthy",
        "reason": "; ".join(degraded_reasons) if degraded_reasons else "ok",
        "tools": tool_results,
    }


async def _check_rest_api(
    api_name: str,
    host: str,
    endpoint: str,
    method: str,
    auth_server: str,
    payload: dict | None = None,
) -> dict:
    """Check the health of a REST API by making an authenticated HTTP request."""
    latencies = []
    num_samples = 1
    status = "healthy"
    reason = "ok"

    try:
        for _ in range(num_samples):
            api_start = time.perf_counter()
            try:
                response = await perform_api_call(
                    host=host,
                    endpoint=endpoint,
                    method=method,
                    auth_server=auth_server,
                    payload=payload,
                )
                latencies.append(int((time.perf_counter() - api_start) * 1000))
                if response is None:
                    status = "degraded"
                    reason = "No response from API"
            except Exception as exc:
                latencies.append(int((time.perf_counter() - api_start) * 1000))
                status = "degraded"
                reason = _format_exception_reason(exc, "REST API error")

        p50 = int(statistics.median(latencies)) if latencies else None
        p99 = (
            int(sorted(latencies)[int(len(latencies) * 0.99)] if len(latencies) > 1 else latencies[0])
            if latencies
            else None
        )

        return {
            "status": status,
            "reason": reason,
            "latency_ms": {"p50": p50, "p99": p99},
        }
    except Exception as exc:
        return {
            "status": "degraded",
            "reason": _format_exception_reason(exc, "REST API error"),
            "latency_ms": {"p50": None, "p99": None},
        }
