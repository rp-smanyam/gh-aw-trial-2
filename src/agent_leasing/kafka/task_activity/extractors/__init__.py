"""Activity extractors.

Each extractor is a pure function that turns a tool's output into zero
or more `TaskActivityEvent` dicts.

- **MCP tools**: registered in `MCP_EXTRACTORS` (keyed on tool name).
  The post-processor factory binds the extractor at registration time.
- **Facilities thinker**: `extract_facilities_thinker_events` is itself
  the extractor; it dispatches internally on `response["action_taken"]`.
- **Local function tools**: caller imports its extractor directly and
  invokes `publish_task_activity`.
"""

from collections.abc import Callable

from agent_leasing.kafka.task_activity.extractors.community_event_signup import (
    MCP_TOOL_NAME as COMMUNITY_EVENT_SIGNUP_MCP_TOOL_NAME,
)
from agent_leasing.kafka.task_activity.extractors.community_event_signup import (
    extract_community_event_signup_events,
)
from agent_leasing.kafka.task_activity.extractors.facilities_thinker import (
    extract_facilities_thinker_events,
)
from agent_leasing.kafka.task_activity.extractors.frustrated_user import (
    extract_frustrated_user_events,
)
from agent_leasing.kafka.task_activity.extractors.guest_parking import (
    MCP_TOOL_NAME as GUEST_PARKING_MCP_TOOL_NAME,
)
from agent_leasing.kafka.task_activity.extractors.guest_parking import (
    extract_guest_parking_events,
)
from agent_leasing.kafka.task_activity.extractors.handoff import extract_handoff_events
from agent_leasing.kafka.task_activity.extractors.lease_info import (
    MCP_TOOL_NAME as LEASE_INFO_MCP_TOOL_NAME,
)
from agent_leasing.kafka.task_activity.extractors.lease_info import (
    extract_lease_info_events,
)
from agent_leasing.kafka.task_activity.extractors.packages import (
    MCP_TOOL_NAME as PACKAGES_MCP_TOOL_NAME,
)
from agent_leasing.kafka.task_activity.extractors.packages import (
    extract_packages_events,
)
from agent_leasing.kafka.task_activity.extractors.qna import extract_qna_events
from agent_leasing.kafka.task_activity.extractors.rent_balance import (
    MCP_TOOL_NAME as RENT_BALANCE_MCP_TOOL_NAME,
)
from agent_leasing.kafka.task_activity.extractors.rent_balance import (
    extract_rent_balance_events,
)
from agent_leasing.kafka.task_activity.extractors.sr_created import (
    MCP_TOOL_NAME as SR_CREATED_MCP_TOOL_NAME,
)
from agent_leasing.kafka.task_activity.extractors.sr_created import (
    extract_sr_created_events,
)

MCP_EXTRACTORS: dict[str, Callable[..., list[dict]]] = {
    SR_CREATED_MCP_TOOL_NAME: extract_sr_created_events,
    RENT_BALANCE_MCP_TOOL_NAME: extract_rent_balance_events,
    LEASE_INFO_MCP_TOOL_NAME: extract_lease_info_events,
    GUEST_PARKING_MCP_TOOL_NAME: extract_guest_parking_events,
    COMMUNITY_EVENT_SIGNUP_MCP_TOOL_NAME: extract_community_event_signup_events,
    PACKAGES_MCP_TOOL_NAME: extract_packages_events,
}

__all__ = [
    "MCP_EXTRACTORS",
    "extract_community_event_signup_events",
    "extract_facilities_thinker_events",
    "extract_frustrated_user_events",
    "extract_guest_parking_events",
    "extract_handoff_events",
    "extract_lease_info_events",
    "extract_packages_events",
    "extract_qna_events",
    "extract_rent_balance_events",
    "extract_sr_created_events",
]
