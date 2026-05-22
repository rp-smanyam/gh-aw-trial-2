import os
from typing import Annotated, Any

import structlog
from agents import RunContextWrapper, function_tool
from langsmith import traceable

logger = structlog.get_logger(__name__)

description_path = os.path.join(os.path.dirname(__file__), "SAFETY_CRITICAL_ROUTER_DESCRIPTION.md")
with open(description_path, encoding="utf-8") as f:
    SAFETY_CRITICAL_ROUTER_DESCRIPTION = f.read().strip()


@traceable(run_type="tool", name="safety_critical_router")
async def _safety_critical_router_impl(
    ctx: RunContextWrapper[Any],
    is_safety_critical: Annotated[
        bool,
        "Is this a safety-critical request with clear and present danger to life? "
        "(e.g. fire, gas leak, suspicious person with threats, medical emergencies, criminal activity, etc.)",
    ],
    is_maintenance_related: Annotated[
        bool,
        "Is this a maintenance related request? "
        "(e.g. broken AC, leaky faucet, broken light, electrical outage, gas leak, etc.)",
    ],
):
    result = ""
    if is_safety_critical and is_maintenance_related:
        result = "This is an emergency maintenance related request.  Follow the emergency maintenance flow."
    elif not is_safety_critical and is_maintenance_related:
        result = "This is a maintenance related request.  Follow the maintenance Facilities Thinker flow."
    elif is_safety_critical and not is_maintenance_related:
        result = "This is a safety-critical request.  Follow the safety-critical Handoff flow."
    elif not is_safety_critical and not is_maintenance_related:
        result = "This is not a safety-critical OR a maintenance-related request.  Please continue with the standard workflow"
    return result


@function_tool(
    description_override=SAFETY_CRITICAL_ROUTER_DESCRIPTION,
)
async def safety_critical_router(
    ctx: RunContextWrapper[Any],
    is_safety_critical: Annotated[
        bool,
        "Is this a safety-critical request with clear and present danger to life? "
        "(e.g. fire, gas leak, suspicious person with threats, medical emergencies, criminal activity, etc.)",
    ],
    is_maintenance_related: Annotated[
        bool,
        "Is this a maintenance related request? "
        "(e.g. broken AC, leaky faucet, broken light, electrical outage, gas leak, etc.)",
    ],
) -> str:
    return await _safety_critical_router_impl(
        ctx,
        is_safety_critical,
        is_maintenance_related,
    )
