import os
import time
from abc import ABC, abstractmethod

import jinja2
import structlog
from agents import Agent, RunContextWrapper, custom_span
from agents.mcp import MCPServerStreamableHttp, create_static_tool_filter
from agents.realtime import RealtimeAgent

from agent_leasing.agent.hooks import RenterAIAgentHooks
from agent_leasing.agent.resident_one_agent.agent_helper import (
    prefetch_property_overview_and_insights,
)
from agent_leasing.agent.tools import (
    call_facilities_thinker_via_api,
    create_link,
    create_mcp_post_processors,
    create_mcp_pre_processors,
    get_emergency_service_transfer_fxn,
    get_property_marketing_info,
    modify_events_output,
    queue_resolution_ack,
    transfer_to_staff_text,
    verification_pre_processor,
    verify_resident_identity,
    voice_sms_consent_confirmed_post_processor,
)
from agent_leasing.agent.tools.mcp_post_processors import (
    create_voice_normalize_extras,
    modify_get_rent_information,
    sr_priority_post_processor,
)
from agent_leasing.agent.tools.verification_check import PROTECTED_TOOLS
from agent_leasing.agent.util import (
    AgentWithMCP,
    ResidentResponderOutput,
    get_channel_from_context,
    get_enabled_input_guardrails,
    get_enabled_output_guardrails,
    is_enabled,
)
from agent_leasing.api.auth.auth_helper import (
    get_facilities_mcp_auth_token,
    get_knock_mcp_auth_token,
    get_loft_mcp_auth_token,
    get_onsite_mcp_auth_token,
)
from agent_leasing.clients.ldp import (
    MODULE_TO_MCP_TOOLS,
    LDPError,
    fetch_ldp_property_data,
    get_available_services,
    get_disabled_modules_with_pte,
    get_disabled_tools_from_disabled_modules,
)
from agent_leasing.clients.mcp import CachingMCPServer
from agent_leasing.kafka.task_activity.emit import task_activity_post_processor
from agent_leasing.models.context import SessionScope
from agent_leasing.settings import build_model_settings, settings
from agent_leasing.util.helpers import is_office_currently_open
from agent_leasing.util.tracing_utils import log_prompt_to_langsmith, log_prompt_to_langsmith_child, set_span_data

logger = structlog.getLogger()

agent_hooks = RenterAIAgentHooks()


def get_mcp_servers(
    context: SessionScope | None,
) -> dict[str, MCPServerStreamableHttp]:
    """Get MCP server instances (for per-request connections)."""
    mcp_servers = {
        "knock_mcp_server": _create_knock_mcp_server(context),
        "loft_mcp_server": _create_loft_mcp_server(context),
    }
    if settings.facilities_thinker_api_enabled is False:
        # Don't need the Facilities MCP server if the module is disabled
        if is_enabled("MR", context.disabled_modules):
            mcp_servers["facilities_mcp_server"] = _create_facilities_mcp_server(context)
    # Don't need the Policy & Ledger MCP server if the module is disabled
    if is_enabled("PAYMENT_CENTER", context.disabled_modules):
        mcp_servers["policy_and_ledger_mcp_server"] = _create_policy_and_ledger_mcp_server(context)

    return mcp_servers


def _get_enabled_tools(context: SessionScope, tools: list[str]) -> list[str]:
    """Only return tools if they aren't in disabled_tools."""
    return [tool for tool in tools if is_enabled(tool, context.disabled_tools)]


# TODO: This function should be removed once Facilities Thinker is fully migrated to API calls (settings.facilities_thinker_api_enabled is always True)
def _create_facilities_mcp_server(context: SessionScope) -> CachingMCPServer:
    """Create the Facilities MCP Server configuration."""
    enabled_tools = _get_enabled_tools(
        context,
        [
            "create_service_request",  # Facilities
            "get_active_service_requests",  # Facilities
        ],
    )

    # Add verification pre-processor for non-CHAT channels
    pre_processor_extras = {}
    channel = get_channel_from_context(context)
    if channel != "CHAT" and settings.identity_verification_enabled:
        for tool_name in ("create_service_request", "get_active_service_requests"):
            if tool_name in enabled_tools:
                pre_processor_extras[tool_name] = [verification_pre_processor(tool_name)]

    voice_extras = create_voice_normalize_extras(context, enabled_tools) or {}

    # create_service_request chain order: task_activity sees raw output
    # (pre voice-rewrite, pre priority-strip); voice_normalize rewrites
    # IDs for TTS; sr_priority strips non-emergency priority fields.
    post_processor_extras: dict[str, list] = {tool: list(procs) for tool, procs in voice_extras.items()}
    if "create_service_request" in enabled_tools:
        post_processor_extras["create_service_request"] = [
            task_activity_post_processor("create_service_request"),
            *post_processor_extras.get("create_service_request", []),
            sr_priority_post_processor,
        ]

    return CachingMCPServer(
        name="Facilities MCP Server",
        params={"url": settings.facilities_mcp_server, "headers": {}},
        auth_function=get_facilities_mcp_auth_token if settings.facilities_mcp_auth_enabled else None,
        cache_tools_list=True,
        cacheable_tools=[],
        client_session_timeout_seconds=30,
        tool_filter=create_static_tool_filter(allowed_tool_names=enabled_tools),
        tool_pre_processors=create_mcp_pre_processors(
            guardrail_tools=enabled_tools,
            extras=pre_processor_extras if pre_processor_extras else None,
        ),
        tool_post_processors=create_mcp_post_processors(
            guardrail_tools=enabled_tools,
            extras=post_processor_extras if post_processor_extras else None,
        ),
        context=context,
        # Timeout and retry for idempotent (read-only) tools
        tool_call_timeout_seconds=30,
        idempotent_tools=[
            "get_active_service_requests",
        ],
        max_retries=1,
    )


def _create_knock_mcp_server(context: SessionScope) -> CachingMCPServer:
    """Create the Knock MCP Server configuration."""

    channel = get_channel_from_context(context)

    if channel == "VOICE":
        allowed_tool_names_knock = [
            "check_resident_sms_opt_in_status",
            "send_sms_on_behalf_of_manager",
            "update_resident_sms_consent_information",
        ]
    else:
        # SMS channel uses pre-agent gate for consent (sms_consent.py), not agent tools
        # Chat/Email channels don't need SMS tools
        allowed_tool_names_knock = []

    enabled_tools = _get_enabled_tools(
        context,
        allowed_tool_names_knock,
    )

    # Add post-processor to set sms_consent_confirmed flag for VOICE channel
    post_processor_extras = None
    if channel == "VOICE" and "update_resident_sms_consent_information" in enabled_tools:
        post_processor_extras = {
            "update_resident_sms_consent_information": [voice_sms_consent_confirmed_post_processor()]
        }

    return CachingMCPServer(
        name="Knock MCP Server",
        params={"url": settings.knock_mcp_server, "headers": {}},
        cache_tools_list=True,
        auth_function=get_knock_mcp_auth_token if settings.knock_mcp_auth_enabled else None,
        tool_filter=create_static_tool_filter(allowed_tool_names=enabled_tools),
        tool_post_processors=create_mcp_post_processors(guardrail_tools=enabled_tools, extras=post_processor_extras),
        tool_pre_processors=create_mcp_pre_processors(guardrail_tools=enabled_tools),
        client_session_timeout_seconds=10,
        cacheable_tools=[],
        context=context,
        # Timeout and retry for idempotent (read-only) tools
        tool_call_timeout_seconds=15,
        idempotent_tools=[
            "check_resident_sms_opt_in_status",
        ],
        max_retries=1,
    )


def _create_loft_mcp_server(context: SessionScope) -> CachingMCPServer:
    """Create the Loft MCP Server configuration."""

    enabled_tools = _get_enabled_tools(
        context,
        [
            "cancel_community_event",  # Community Events
            "fetch_community_events",  # Community Events
            "fetch_user_signed_up_community_events",  # Community Events
            "get_residents_packages",  # Packages
            "issue_guest_parking_pass",  # Guest Parking
            "sign_up_community_events",  # Community Events
        ],
    )

    # Add verification pre-processor for non-CHAT channels
    pre_processor_extras = {}
    channel = get_channel_from_context(context)
    if channel != "CHAT" and settings.identity_verification_enabled and "issue_guest_parking_pass" in enabled_tools:
        pre_processor_extras["issue_guest_parking_pass"] = [verification_pre_processor("issue_guest_parking_pass")]

    post_processor_extras: dict[str, list] = {"fetch_community_events": [modify_events_output]}
    voice_extras = create_voice_normalize_extras(context, enabled_tools)
    for tool, processors in voice_extras.items():
        post_processor_extras.setdefault(tool, []).extend(processors)

    # Wire task-activity emission for the Loft MCP tools we extract from.
    # Place the post-processor first in the chain so it sees the raw tool
    # output before any voice-normalization rewrites.
    for tool_name in ("issue_guest_parking_pass", "sign_up_community_events", "get_residents_packages"):
        if tool_name in enabled_tools:
            post_processor_extras[tool_name] = [
                task_activity_post_processor(tool_name),
                *post_processor_extras.get(tool_name, []),
            ]

    return CachingMCPServer(
        name="Loft MCP Server",
        params={"url": settings.loft_mcp_server, "headers": {}},
        auth_function=get_loft_mcp_auth_token if settings.loft_mcp_auth_enabled else None,
        cache_tools_list=True,
        cacheable_tools=["get_residents_packages"],
        client_session_timeout_seconds=10,
        tool_filter=create_static_tool_filter(allowed_tool_names=enabled_tools),
        tool_post_processors=create_mcp_post_processors(
            guardrail_tools=enabled_tools,
            extras=post_processor_extras,
        ),
        tool_pre_processors=create_mcp_pre_processors(
            guardrail_tools=enabled_tools,
            extras=pre_processor_extras if pre_processor_extras else None,
        ),
        context=context,
        # Timeout and retry for idempotent (read-only) tools
        tool_call_timeout_seconds=15,
        idempotent_tools=[
            "fetch_community_events",
            "fetch_user_signed_up_community_events",
            "get_residents_packages",
        ],
        max_retries=1,
    )


def _create_policy_and_ledger_mcp_server(context: SessionScope) -> CachingMCPServer:
    """Create the OneSite (Policy & Ledger) MCP Server configuration."""

    enabled_tools = _get_enabled_tools(
        context,
        [
            "get_lease_term_information",  # Policy & Ledger
            "get_rent_information",  # Policy & Ledger
            "get_fas_account_statement",  # Policy & Ledger
            "get_resident_autopay_and_transactions",  # Policy & Ledger
            "get_property_details",  # Policy & Ledger
            "get_custom_reminders",  # Policy & Ledger
            "manage_custom_reminders",  # Policy & Ledger
        ],
    )

    # Conditionally add legacy post-processor for old rent format
    post_processor_extras: dict[str, list] = {}
    if not settings.onesite_new_rent_format:
        post_processor_extras["get_rent_information"] = [modify_get_rent_information]

    voice_extras = create_voice_normalize_extras(context, enabled_tools)
    for tool, processors in voice_extras.items():
        post_processor_extras.setdefault(tool, []).extend(processors)

    # Wire task-activity emission. The post-processor sees the original
    # tool output (pre voice-normalize) and pre any legacy rent reformatting,
    # so it stays first in the chain.
    for tool_name in ("get_rent_information", "get_lease_term_information"):
        if tool_name in enabled_tools:
            post_processor_extras[tool_name] = [
                task_activity_post_processor(tool_name),
                *post_processor_extras.get(tool_name, []),
            ]

    # Add verification pre-processor for non-CHAT channels. Every tool listed in
    # PROTECTED_TOOLS that is also enabled on this server must be guarded — otherwise
    # the request reaches mcp-onesite without identity verification.
    pre_processor_extras: dict[str, list] = {}
    channel = get_channel_from_context(context)
    if channel != "CHAT" and settings.identity_verification_enabled:
        for tool_name in enabled_tools:
            if tool_name in PROTECTED_TOOLS:
                pre_processor_extras[tool_name] = [verification_pre_processor(tool_name)]

    return CachingMCPServer(
        name="OneSite MCP Server",
        params={"url": settings.onesite_mcp_server, "headers": {}},
        auth_function=get_onsite_mcp_auth_token if settings.onesite_mcp_auth_enabled else None,
        cache_tools_list=True,
        cacheable_tools=[],
        client_session_timeout_seconds=35,
        tool_filter=create_static_tool_filter(allowed_tool_names=enabled_tools),
        tool_pre_processors=create_mcp_pre_processors(
            guardrail_tools=enabled_tools,
            extras=pre_processor_extras if pre_processor_extras else None,
        ),
        tool_post_processors=create_mcp_post_processors(
            guardrail_tools=enabled_tools,
            extras=post_processor_extras if post_processor_extras else None,
        ),
        context=context,
        # Timeout and retry for idempotent (read-only) tools
        tool_call_timeout_seconds=30,
        idempotent_tools=[
            "get_lease_term_information",
            "get_rent_information",
            "get_fas_account_statement",
            "get_resident_autopay_and_transactions",
            "get_property_details",
            "get_custom_reminders",
        ],
        max_retries=1,
    )


async def ensure_disabled_modules_and_tools_loaded(context: SessionScope) -> None:
    """Fetch disabled modules/tools once so prompt rendering has accurate feature flags."""
    if context.disabled_modules or context.disabled_tools or getattr(context, "pte_setting", None) is not None:
        return

    ldp_start = time.monotonic()
    with custom_span("Get property data from LDP", data={}):
        context.disabled_modules, context.pte_setting = await get_disabled_modules_with_pte(context.property_id)
        context.disabled_tools = get_disabled_tools_from_disabled_modules(
            MODULE_TO_MCP_TOOLS, context.disabled_modules
        )
        set_span_data(
            disabled_modules=context.disabled_modules,
            disabled_tools=context.disabled_tools,
            pte_setting=context.pte_setting,
        )

    if settings.startup_latency_logging_enabled:
        ldp_ms = int((time.monotonic() - ldp_start) * 1000)
        logger.info(
            "LDP modules fetched",
            event_type="ldp_fetched",
            ldp_ms=ldp_ms,
            property_id=context.property_id,
        )


class BaseResidentAgent(AgentWithMCP, ABC):
    """Base class for resident agents."""

    def __init__(self, context: SessionScope):
        super().__init__(context)
        # If application-level setting is greater than one in the payload use that instead
        if settings.resident_one_prompt_version > context.ask_request.prompt_version:
            version = settings.resident_one_prompt_version
        else:
            version = context.ask_request.prompt_version

        instructions_file = os.path.join(os.path.dirname(__file__), "INSTRUCTIONS.md")
        self.prompt = self._get_prompt(instructions_file, version=version)

        self.disabled_modules: dict[str, list] = {}
        self.name = "resident-one-agent"

    def agent(self) -> Agent | RealtimeAgent:
        return self.agent_instance  # noqa

    async def __aenter__(self):
        # Only fetch disabled modules/tools if not already set on context.
        # Use pte_setting presence to distinguish "fetched empty" from "not fetched yet".
        await ensure_disabled_modules_and_tools_loaded(self.context)

        # Initialize MCP servers after disabled modules are set
        self.mcp_servers = get_mcp_servers(self.context)

        # Add temp facilities MCP server for SR prefetch into the main batch
        # so it connects in parallel with the other servers (saves ~300ms)
        self._prefetch_facilities_server = None
        if (
            settings.facilities_thinker_api_enabled
            and settings.sr_prefetch_via_mcp
            and is_enabled("MR", self.context.disabled_modules)
            and "insights" in settings.welcome_message_sections
        ):
            self._prefetch_facilities_server = _create_facilities_mcp_server(self.context)
            self.mcp_servers["_prefetch_facilities_mcp_server"] = self._prefetch_facilities_server

        # Call parent __aenter__ to connect all MCP servers in parallel and create agent
        result = await super().__aenter__()

        # Prefetch property data (legacy path) and insights once during initialization
        channel = get_channel_from_context(self.context)
        prefetch_ms = 0

        if settings.property_marketing_info_tool_enabled:
            # Tool path: always prefetch insights (property_data is never used as a guard).
            prefetch_start = time.monotonic()
            with custom_span(
                "Pre-fetch Property Overview and Insights",
                data={"welcome_message_sections": settings.welcome_message_sections, "channel": channel},
            ):
                tools_called = await prefetch_property_overview_and_insights(
                    self.mcp_servers,
                    self.context,
                    resident_summary=None,
                    prefetch_facilities_server=self._prefetch_facilities_server,
                )
                set_span_data(tools_called=tools_called)
            prefetch_ms = int((time.monotonic() - prefetch_start) * 1000)
            if settings.startup_latency_logging_enabled:
                logger.info("Prefetch complete", prefetch_ms=prefetch_ms)
        elif not self.context.property_data:
            # Legacy path: gate all prefetch on property_data not already set
            # (e.g., skip when used as thinker tool inside realtime agent).
            prefetch_start = time.monotonic()
            try:
                ldp_data = await fetch_ldp_property_data(self.context.property_id)
                resident_summary = ldp_data.get("resident_summary")
            except LDPError:
                resident_summary = None
            with custom_span(
                "Pre-fetch Property Overview and Insights",
                data={"welcome_message_sections": settings.welcome_message_sections, "channel": channel},
            ):
                tools_called = await prefetch_property_overview_and_insights(
                    self.mcp_servers,
                    self.context,
                    resident_summary=resident_summary,
                    prefetch_facilities_server=self._prefetch_facilities_server,
                )
                set_span_data(tools_called=tools_called)
            prefetch_ms = int((time.monotonic() - prefetch_start) * 1000)
            if settings.startup_latency_logging_enabled:
                logger.info(
                    "Prefetch complete",
                    event_type="prefetch_complete",
                    prefetch_ms=prefetch_ms,
                )

        # Cleanup temp prefetch server early (only used for the one-shot SR call).
        # cleanup() is idempotent, so the exit stack callback will be a harmless no-op.
        if self._prefetch_facilities_server:
            await self._prefetch_facilities_server.cleanup()
            self.mcp_servers.pop("_prefetch_facilities_mcp_server", None)
            self._prefetch_facilities_server = None

        return result

    @abstractmethod
    async def _create_agent(self):
        pass

    async def _get_agent_instructions(
        self,
        run_context: RunContextWrapper[SessionScope],
        agent: Agent[SessionScope],  # noqa
    ) -> str:
        """Gets instructions for the agent and injects data from the context."""

        environment = jinja2.Environment()
        template = environment.from_string(self.prompt)
        channel = get_channel_from_context(run_context.context)
        available_services = get_available_services(self.context.disabled_modules)
        product_info = run_context.context.ask_request.product_info if run_context.context.ask_request else None
        is_office_open = is_office_currently_open(
            office_hours=product_info.office_hours if product_info else None,
            property_timezone=product_info.property_timezone if product_info else None,
            now=run_context.context.current_time,
        )

        former_type = getattr(product_info, "former_type", None) if product_info else None

        base_prompt = template.render(
            current_time=run_context.context.current_time.isoformat(),
            context=run_context.context,
            channel=channel,
            disabled_modules=self.context.disabled_modules,
            disabled_tools=self.context.disabled_tools,
            available_services=available_services,
            custom_greeting=run_context.context.custom_greeting,
            settings=settings,
            is_office_open=is_office_open,
            former_type=former_type,
        )

        run_context.context.rendered_system_prompt = base_prompt
        self._log_instructions_prompt_trace(run_context, channel, base_prompt, available_services, is_office_open)

        return base_prompt

    def _log_instructions_prompt_trace(
        self,
        run_context: RunContextWrapper[SessionScope],
        channel: str,
        rendered_prompt: str,
        available_services: list[str],
        is_office_open: bool | None,
    ) -> None:
        """Log the rendered INSTRUCTIONS.md prompt to LangSmith as a ChatPromptTemplate span."""
        ctx = run_context.context
        context_variables = self._build_instructions_context_variables(
            ctx, channel, available_services, is_office_open
        )

        if channel == "VOICE":
            self._log_voice_prompt_trace(ctx, "INSTRUCTIONS.md", rendered_prompt, context_variables)
        else:
            log_prompt_to_langsmith(
                prompt_name="INSTRUCTIONS.md",
                rendered_prompt=rendered_prompt,
                context_variables=context_variables,
                parent=ctx.langsmith_run_tree,
            )

    def _log_voice_prompt_trace(
        self,
        ctx: SessionScope,
        prompt_name: str,
        rendered_prompt: str,
        context_variables: dict,
    ) -> None:
        """Log a prompt to LangSmith as a child of the voice root run.

        Looks up the root run by preference:
        1. ``ctx.root_run`` — set directly by the v2 voice handler.
        2. ``ctx._session_handler.root_run`` — legacy back-reference set by
           ``twilio_handler`` (v1).
        """
        root_run = getattr(ctx, "root_run", None)
        if root_run is None:
            handler = getattr(ctx, "_session_handler", None)
            root_run = getattr(handler, "root_run", None) if handler else None
        logger.debug(
            f"LangSmith prompt logging for {prompt_name}",
            channel="VOICE",
            has_root_run=root_run is not None,
        )
        if root_run:
            log_prompt_to_langsmith_child(
                parent_run=root_run,
                prompt_name=prompt_name,
                rendered_prompt=rendered_prompt,
                context_variables=context_variables,
            )

    def _build_base_context_variables(
        self,
        ctx: SessionScope,
        channel: str,
        available_services: list[str],
    ) -> dict:
        """Build the context variables shared by all prompt templates."""
        ask_req = ctx.ask_request
        product_info = ask_req.product_info if ask_req else None

        return {
            "channel": channel,
            "current_time": ctx.current_time.isoformat(),
            "language_code": ctx.language_code,
            "previous_response_id": ctx.previous_response_id,
            "disabled_modules": self.context.disabled_modules,
            "disabled_tools": self.context.disabled_tools,
            "available_services": available_services,
            "property_name": getattr(product_info, "property_name", None) if product_info else None,
            "uc_first_name": getattr(product_info, "uc_first_name", None) if product_info else None,
            "uc_last_name": getattr(product_info, "uc_last_name", None) if product_info else None,
            "property_data": ctx.property_data,
            "resident_data": getattr(ask_req, "resident_data", None) if ask_req else None,
            "packages": ctx.packages,
            "service_requests": ctx.service_requests,
            "signed_up_community_events": ctx.signed_up_community_events,
            "emergency_service_product": getattr(ask_req, "emergency_service_product", None) if ask_req else None,
            "callback_number": getattr(ask_req, "callback_number", None) if ask_req else None,
        }

    def _build_instructions_context_variables(
        self,
        ctx: SessionScope,
        channel: str,
        available_services: list[str],
        is_office_open: bool | None,
    ) -> dict:
        """Build the full context variables for INSTRUCTIONS.md (extends base with identity/feature flags)."""
        variables = self._build_base_context_variables(ctx, channel, available_services)
        ask_req = ctx.ask_request
        product_info = ask_req.product_info if ask_req else None

        variables.update(
            {
                "pte_setting": getattr(ctx, "pte_setting", None),
                "facilities_thinker_api_enabled": settings.facilities_thinker_api_enabled,
                "onesite_new_rent_format": settings.onesite_new_rent_format,
                "identity_verified": ctx.is_identity_verified(channel),
                "identity_verified_with_birth_year": ctx.is_identity_verified_with_birth_year(channel),
                "knock_resident_id": getattr(product_info, "knock_resident_id", None) if product_info else None,
                "ab_resident_id": getattr(product_info, "ab_resident_id", None) if product_info else None,
                "uc_company_id": getattr(product_info, "uc_company_id", None) if product_info else None,
                "uc_property_id": getattr(product_info, "uc_property_id", None) if product_info else None,
                "uc_community_id": getattr(product_info, "uc_community_id", None) if product_info else None,
                "uc_resident_household_id": getattr(product_info, "uc_resident_household_id", None)
                if product_info
                else None,
                "uc_resident_member_id": getattr(product_info, "uc_resident_member_id", None)
                if product_info
                else None,
                "is_office_open": is_office_open,
                "former_type": getattr(product_info, "former_type", None) if product_info else None,
            }
        )
        return variables


class ResidentAgent(BaseResidentAgent):
    """Resident agent that can run standalone or be used as a thinker tool."""

    async def _create_agent(self):
        channel = get_channel_from_context(self.context)

        local_tools = [create_link]
        if settings.property_marketing_info_tool_enabled:
            local_tools.append(get_property_marketing_info)

        # Voice call management is handled by the realtime responder.
        if channel != "VOICE":
            local_tools.append(
                get_emergency_service_transfer_fxn(
                    self.context.ask_request.emergency_service_product,
                    context=self.context,
                )
            )
            local_tools.append(transfer_to_staff_text)

        # Add verification tool for non-CHAT channels
        if channel != "CHAT" and settings.identity_verification_enabled:
            local_tools.append(verify_resident_identity)

        if settings.facilities_thinker_api_enabled is True:
            if is_enabled("MR", self.context.disabled_modules):
                local_tools.append(call_facilities_thinker_via_api)
                local_tools.append(queue_resolution_ack)

        agent = Agent(
            name="Resident Agent (One)",
            instructions=self._get_agent_instructions,
            model=settings.resident_one_model,
            model_settings=build_model_settings(
                model=settings.resident_one_model,
                effort=settings.resident_one_model_reasoning_effort,
                verbosity=settings.resident_one_model_verbosity,
                temperature=settings.model_temperature,
                max_tokens=settings.resident_one_model_max_tokens,
                service_tier=settings.model_service_tier,
            ),
            hooks=agent_hooks,
            tools=local_tools,
            mcp_servers=list(self.mcp_servers.values()),
            output_guardrails=get_enabled_output_guardrails() if channel != "VOICE" else [],
            input_guardrails=get_enabled_input_guardrails(),
            output_type=ResidentResponderOutput,
        )

        return agent
