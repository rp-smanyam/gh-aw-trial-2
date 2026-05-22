import abc
import asyncio
import os
import time
from contextlib import AsyncExitStack
from enum import Enum
from typing import TYPE_CHECKING, Any, List

import langsmith as ls
import structlog
from agents import (
    Agent,
    HandoffOutputItem,
    MessageOutputItem,
    RunResult,
    ToolCallItem,
    ToolCallOutputItem,
)
from agents.realtime import RealtimeAgent
from agents.result import RunErrorDetails, RunResultBase
from mcp.types import CallToolResult
from pydantic import BaseModel, Field

from agent_leasing.agent.qna_taxonomy import QnATopic
from agent_leasing.api.model import Flow, Product
from agent_leasing.models.context import SessionScope
from agent_leasing.settings import settings

if TYPE_CHECKING:
    from agent_leasing.clients.mcp import CachingMCPServer

logger = structlog.getLogger()

FLOWS_TO_RECORD = [
    "qna_flow",
    "community_flow",
    "packages_flow",
    "guest_parking_flow",
    "policy_and_ledger_flow",
    "facilities_flow",
    "handoff_to_human_flow",
]


class ResidentResponderOutput(BaseModel):
    response: str
    language_code: str = Field(min_length=2, max_length=2, default="en")
    workflow_codes: list[str] = Field(description="List of workflow codes", default=[])
    qna_topics: list[QnATopic] = Field(
        description=(
            "Q&A taxonomy topics this turn touches, in the form CATEGORY.SUBTOPIC. "
            "Populate only when qna_flow is in workflow_codes; empty otherwise. "
            "Multiple topics allowed when one turn spans more than one. "
            "Closed enumeration — values outside the schema are rejected."
        ),
        default_factory=list,
    )
    user_frustrated: bool = Field(
        description=(
            "True when the user's latest message expresses frustration, anger, "
            "profanity directed at the service, or repeated dissatisfaction with "
            "prior responses. False otherwise. Conversation-level signal — set "
            "once on the turn the frustration first surfaces; downstream "
            "deduplicates across the conversation."
        ),
        default=False,
    )

    def extract_flows(self) -> list[Flow]:
        return [Flow(name=code) for code in self.workflow_codes if code in FLOWS_TO_RECORD]


class AgentArchitecture(str, Enum):
    SINGLE_AGENT = "SINGLE_AGENT"
    RESPONDER_THINKER = "RESPONDER_THINKER"


class AgentWithMCP(abc.ABC):
    """Interface for agent implementations."""

    # Class-level cache for instruction files: {file_path: {version: content}}
    _PROMPT_CACHE: dict[str, dict[int, str]] = {}

    def __init__(self, context: SessionScope) -> None:
        self.context = context
        self.mcp_servers = {}
        self.agent_architecture = AgentArchitecture.SINGLE_AGENT
        self._cleanup_lock: asyncio.Lock = asyncio.Lock()
        self.name = "agent-wth-mcp"

    @staticmethod
    async def _connect_mcp_servers(exit_stack: AsyncExitStack, mcp_servers: dict) -> None:
        """Connect MCP servers in parallel, then register cleanup on the exit stack.

        Uses connect()/cleanup() instead of enter_async_context() so that all
        connections run concurrently via asyncio.gather(). Cleanup callbacks are
        registered sequentially after gather (push_async_callback is synchronous).

        Failed servers are removed from mcp_servers dict. External CancelledError
        is propagated.

        Args:
            exit_stack: AsyncExitStack to register cleanup callbacks on
            mcp_servers: Dict of {name: mcp_server} to connect (modified in place)
        """
        if not mcp_servers:
            return

        total_start = time.monotonic()
        # Track servers that connected successfully so we can clean them up
        # if gather is interrupted before we register exit stack callbacks.
        connected_mcps: list[tuple[str, object]] = []

        async def _connect_one(name: str, mcp) -> tuple[str, BaseException | None]:
            timeout = getattr(mcp, "client_session_timeout_seconds", None) or 5
            if settings.startup_latency_logging_enabled:
                logger.info(f"Connecting MCP server: {name}")
            server_start = time.monotonic()
            try:
                with ls.trace(name=f"connect_mcp:{name}", run_type="tool"):
                    await asyncio.wait_for(mcp.connect(), timeout=timeout)
                duration_ms = int((time.monotonic() - server_start) * 1000)
                logger.info(
                    f"MCP connect complete: {name}",
                    event_type="mcp_connect_complete",
                    mcp_server=name,
                    duration_ms=duration_ms,
                )
                connected_mcps.append((name, mcp))
                return name, None
            except BaseException as e:
                return name, e

        # Connect all servers in parallel.
        # _connect_one never raises (catches BaseException), so gather always
        # completes unless the outer task is cancelled. The finally block
        # ensures connected servers are cleaned up if we're interrupted before
        # registering exit stack callbacks.
        registered_names: set[str] = set()
        try:
            results = await asyncio.gather(
                *(_connect_one(name, mcp) for name, mcp in mcp_servers.items()),
            )

            # Process results sequentially: register cleanup for successes, collect failures
            failed_servers = []
            connected_count = 0
            cancelled_error: asyncio.CancelledError | None = None

            for name, error in results:
                if error is None:
                    # Register cleanup callback (synchronous, safe to call sequentially)
                    mcp = mcp_servers[name]
                    exit_stack.push_async_callback(lambda m=mcp: m.cleanup())
                    registered_names.add(name)
                    connected_count += 1
                elif isinstance(error, asyncio.CancelledError):
                    # Check if this was external cancellation
                    outer_task = asyncio.current_task()
                    if outer_task is not None and outer_task.cancelling() > 0:
                        cancelled_error = error
                    else:
                        logger.warning(f"MCP initialization cancelled for {name}")
                        failed_servers.append(name)
                elif isinstance(error, TimeoutError):
                    logger.error(f"MCP timeout error for {name}: {type(error).__name__}: {error!r}")
                    failed_servers.append(name)
                else:
                    logger.error(f"MCP error for {name}: {type(error).__name__}: {error!r}")
                    failed_servers.append(name)

            for name in failed_servers:
                logger.info(f"Removing MCP server {name} due to error")
                del mcp_servers[name]

            if settings.startup_latency_logging_enabled:
                total_ms = int((time.monotonic() - total_start) * 1000)
                logger.info(
                    "MCP servers connected",
                    event_type="all_mcp_connected",
                    total_ms=total_ms,
                    connected=connected_count,
                    failed=len(failed_servers),
                )

            if cancelled_error is not None:
                raise cancelled_error
        except BaseException:
            # Gather was interrupted (e.g. external cancellation) before we could
            # register all cleanup callbacks. Clean up any connected servers that
            # aren't on the exit stack to prevent leaked anyio tasks.
            for name, mcp in connected_mcps:
                if name not in registered_names:
                    try:
                        await asyncio.wait_for(asyncio.shield(mcp.cleanup()), timeout=5)
                    except Exception:
                        logger.warning(f"Failed to cleanup orphaned MCP server: {name}")
            raise

    async def __aenter__(self) -> "AgentWithMCP":
        """Connect MCP servers and create agent instance."""
        with ls.trace(name="agent_init", run_type="chain"):
            self._mcp_exit_stack = AsyncExitStack()
            await self._connect_mcp_servers(self._mcp_exit_stack, self.mcp_servers)

            agent = await self._create_agent()

            if self.context:
                self.context.reset()

            self.agent_instance = agent
        return self

    async def __aexit__(self, exc_type, exc_value, exc_tb) -> None:
        """Disconnect MCP servers via exit stack with timeout protection."""
        if hasattr(self, "_mcp_exit_stack"):
            async with self._cleanup_lock:
                logger.debug("Starting MCP cleanup via exit stack")
                # Timeout: 5s per server, minimum 10s
                num_servers = max(len(self.mcp_servers), 1)
                timeout = max(num_servers * 5, 10)
                try:
                    await asyncio.wait_for(self._mcp_exit_stack.aclose(), timeout=timeout)
                    logger.debug("MCP cleanup complete")
                except TimeoutError:
                    logger.warning(f"MCP cleanup timed out after {timeout}s")
                except Exception as e:
                    logger.warning(f"MCP cleanup error (suppressed): {e}")

        self.mcp_servers.clear()

    @abc.abstractmethod
    def agent(self) -> Agent | RealtimeAgent:
        pass

    @classmethod
    def _get_prompt(cls, filename: str, version: int | None = 0) -> str:
        """Get prompt content from file, with support for versioned files.

        This method caches all versions of a prompt file on first access to avoid
        repeated file I/O. Supports versioned prompts with the pattern:
        - version 0: {filename} (e.g., "INSTRUCTIONS.md")
        - version N: {base}_V{N}.{ext} (e.g., "INSTRUCTIONS_V2.md")

        Args:
            filename: Full path to the base instruction file.
            version: Version number to load (default: 0 for base file).

        Returns:
            Content of the requested prompt file.

        Raises:
            FileNotFoundError: If the requested version doesn't exist.
        """
        # Use the absolute path as cache key
        file_path = os.path.abspath(filename)

        # Load all versions on first access to this file
        if file_path not in cls._PROMPT_CACHE:
            cls._PROMPT_CACHE[file_path] = {}
            file_dir = os.path.dirname(file_path)
            base_name = os.path.basename(file_path)
            name_without_ext, ext = os.path.splitext(base_name)

            # Load base version (version 0)
            if os.path.exists(file_path):
                with open(file_path, encoding="utf-8") as f:
                    cls._PROMPT_CACHE[file_path][0] = f.read()

            # Scan directory for versioned files
            if os.path.isdir(file_dir):
                for entry in os.listdir(file_dir):
                    if entry.startswith(f"{name_without_ext}_V") and entry.endswith(ext):
                        try:
                            # Extract version number from filename
                            version_part = entry[len(name_without_ext) + 2 : -len(ext)]
                            file_version = int(version_part)
                            # Only accept positive version numbers
                            if file_version < 1:
                                continue
                            versioned_path = os.path.join(file_dir, entry)
                            with open(versioned_path, encoding="utf-8") as f:
                                cls._PROMPT_CACHE[file_path][file_version] = f.read()
                        except (ValueError, OSError) as e:
                            logger.warning(f"Failed to load versioned prompt {entry}: {e}")

            logger.info(f"Loaded {len(cls._PROMPT_CACHE[file_path])} version(s) of {base_name}")

        # Return requested version, with fallback to version 0
        if version not in cls._PROMPT_CACHE[file_path]:
            if version != 0 and 0 in cls._PROMPT_CACHE[file_path]:
                # Fallback to version 0 if requested version not found
                logger.warning(
                    f"Version {version} of {os.path.basename(filename)} not found, falling back to version 0"
                )
                return cls._PROMPT_CACHE[file_path][0]

            # No version 0 fallback available
            available = sorted(cls._PROMPT_CACHE[file_path].keys())
            raise FileNotFoundError(
                f"Version {version} of {os.path.basename(filename)} not found. Available versions: {available}"
            )

        return cls._PROMPT_CACHE[file_path][version]


def get_architecture_from_context(context: SessionScope) -> AgentArchitecture:
    """Determine the agent architecture based on the product in the request context."""
    if "resident_one_" in context.ask_request.product:
        return AgentArchitecture.SINGLE_AGENT
    else:
        return AgentArchitecture.RESPONDER_THINKER


# Lazy-loaded channel instructions
CHANNEL_INSTRUCTIONS = {}

# Lazy-loaded channel instructions
THINKER_CHANNEL_INSTRUCTIONS = {}

AGENT_MAP = {
    Product.SIMPLE.value: "agent_leasing.agent.simple.agent.SimpleAgent",
    # Resident (One Agent)
    Product.RESIDENT_ONE_CHAT.value: "agent_leasing.agent.resident_one_agent.agent.ResidentAgent",
    Product.RESIDENT_ONE_SMS.value: "agent_leasing.agent.resident_one_agent.agent.ResidentAgent",
    Product.RESIDENT_ONE_EMAIL.value: "agent_leasing.agent.resident_one_agent.agent.ResidentAgent",
    Product.RESIDENT_ONE_VOICE.value: "agent_leasing.agent.resident_one_agent.realtime.ResidentRealtimeResponderAgent",
}


def log_internal_messages(result: RunResult | RunErrorDetails):
    """Log internal OpenAI messages. RunResult os returned from Runner.run"""

    # Build booleans for duck typing - check for attributes instead of types
    has_new_items = hasattr(result, "new_items")
    has_guardrails = hasattr(result, "input_guardrail_results") and hasattr(result, "output_guardrail_results")

    if has_new_items:
        _log_successful_internal_messages(result)
    else:
        raise ValueError(f"Needs to be of a class with new_items: {type(result)}")

    if has_guardrails:
        _log_guardrail_internal_messages(result)


def _log_successful_internal_messages(result: RunResult | RunErrorDetails):
    """Flow for logging the new_item messages"""
    for new_item in result.new_items:
        agent_name = new_item.agent.name
        if isinstance(new_item, MessageOutputItem):
            # logger.debug(f"Output: {ItemHelpers.text_message_output(new_item)}")
            pass
        elif isinstance(new_item, HandoffOutputItem):
            logger.debug(f"Handed off from {new_item.source_agent.name} to {new_item.target_agent.name}")
        elif isinstance(new_item, ToolCallItem):
            logger.info(f"{agent_name}: Calling tool ({new_item.raw_item.name})")
        elif isinstance(new_item, ToolCallOutputItem):
            logger.info(f"{agent_name}: Tool call output: {new_item.output}")
        else:
            logger.debug(f"{agent_name}: Skipping item: {new_item.__class__.__name__}")


def _log_guardrail_internal_messages(result: RunErrorDetails):
    """Specific case for custom guardrail outputs (e.g., CompetitorBlockingGuardrailOutput)"""
    # this is a hack, since the object does not contain the guardrail name
    input_guardrails_triggered = [
        r.guardrail.name for r in result.input_guardrail_results if r.output.tripwire_triggered
    ]
    output_guardrails_triggered = [
        r.guardrail.name for r in result.output_guardrail_results if r.output.tripwire_triggered
    ]
    guardrails_triggered = input_guardrails_triggered + output_guardrails_triggered
    logger.debug(f"Guardrail {guardrails_triggered} triggered!")


def show_streamed_events(result: RunResultBase):
    for new_item in result.new_items:
        agent_name = new_item.agent.name
        if isinstance(new_item, MessageOutputItem):
            # logger.info(f"{agent_name}: {ItemHelpers.text_message_output(new_item)}")
            pass
        elif isinstance(new_item, HandoffOutputItem):
            logger.info(f"Handed off from {new_item.source_agent.name} to {new_item.target_agent.name}")
        elif isinstance(new_item, ToolCallItem):
            logger.info(f"{agent_name}: Calling a tool")
        elif isinstance(new_item, ToolCallOutputItem):
            logger.info(f"{agent_name}: Tool call output: {new_item.output}")
        else:
            logger.info(f"{agent_name}: Skipping item: {new_item.__class__.__name__}")


class UnsupportedAgentException(ValueError):
    pass


def agent_selector(agent_name: str, context) -> "AgentWithMCP":
    """Select and return the appropriate agent implementation based on agent_name.

    Args:
        agent_name: The name of the agent to use.
        context: The current session context.

    Returns:
        An agent implementation.
    """

    class_path = AGENT_MAP.get(agent_name)
    if not class_path:
        raise UnsupportedAgentException(f"Unsupported agent: {agent_name}")

    # Dynamically import the agent class
    module_path, class_name = class_path.rsplit(".", 1)
    module = __import__(module_path, fromlist=[class_name])
    agent_class = getattr(module, class_name)
    return agent_class(context)


def extract_tool_result(result: CallToolResult) -> str | dict | None:
    """Extract tool output as str or dict from CallToolResult."""
    if result.structuredContent:
        return result.structuredContent.get("result", result.structuredContent)
    if result.content:
        return result.content[0].text
    logger.warning(f"Could not determine type of content in the response: {result.content}")
    return None


def get_channel_instructions(context: SessionScope) -> tuple[str, str]:
    """Get channel name and channel-specific instructions based on the product type."""
    channel = get_channel_from_context(context)

    if not CHANNEL_INSTRUCTIONS.get(channel):
        # Load channel-specific instructions
        try:
            channels_dir = os.path.join(os.path.dirname(__file__), "channels")
            channel_file = os.path.join(channels_dir, f"{channel}.md")

            if os.path.exists(channel_file):
                with open(channel_file, encoding="utf-8") as f:
                    CHANNEL_INSTRUCTIONS[channel] = f.read()
            else:
                logger.warning(f"Channel file not found: {channel_file}")
        except Exception as e:
            logger.error(f"Error loading channel instructions for {channel}: {e}")

    return channel, CHANNEL_INSTRUCTIONS.get(
        channel, f"Channel-specific instructions for {channel} are not available."
    )


def get_channel_from_context(context: SessionScope) -> str:
    """Get channel name based on the product type in context."""
    product = getattr(context.ask_request, "product", None)

    # Determine channel based on product type
    return get_channel_from_product(product)


def get_channel_from_product(product: Product | str) -> str:
    if isinstance(product, str):
        if "VOICE" in product.upper():
            return "VOICE"
        elif "SMS" in product.upper():
            return "SMS"
        elif "CHAT" in product.upper():
            return "CHAT"
        elif "EMAIL" in product.upper():
            return "EMAIL"
        else:
            logger.warning(f"Defaulting to CHAT for product: {product}")
            return "CHAT"
    else:
        logger.warning("Defaulting to CHAT for product (not a string)")
        return "CHAT"


def is_disabled(item: str, disabled_items: List[str] | None) -> bool:
    """Return True when item is in disabled_items; None/empty means not disabled."""
    if not disabled_items:
        return False
    return item in disabled_items


def is_enabled(item: str, disabled_items: List[str] | None) -> bool:
    """Return True when item is not in disabled_items; None/empty means enabled."""
    return not is_disabled(item, disabled_items)


def get_enabled_input_guardrails() -> list:
    """Build list of enabled input guardrails based on settings configuration.

    Returns:
        List of guardrail objects that are enabled in settings.enabled_input_guardrails
    """
    from agent_leasing.agent import (
        pii_input_guardrail,
        prisma_airs_input_guardrail,
        prompt_injection_input_guardrail,
        security_input_guardrail,
    )
    from agent_leasing.settings import settings

    guardrail_map = {
        "security": security_input_guardrail,
        "pii": pii_input_guardrail,
        "prompt_injection": prompt_injection_input_guardrail,
        "prisma_airs": prisma_airs_input_guardrail,
    }

    return [guardrail_map[name] for name in settings.enabled_input_guardrails if name in guardrail_map]


def get_enabled_output_guardrails() -> list:
    """Build list of enabled output guardrails based on settings configuration.

    Returns:
        List of guardrail objects that are enabled in settings.enabled_output_guardrails
    """
    from agent_leasing.agent import (
        competitor_blocking_guardrail,
        fair_housing_output_guardrail,
        legal_advice_output_guardrail,
        pii_output_guardrail,
        prisma_airs_output_guardrail,
        security_output_guardrail,
        unauthorized_promises_output_guardrail,
    )
    from agent_leasing.settings import settings

    guardrail_map = {
        "security": security_output_guardrail,
        "pii": pii_output_guardrail,
        "fair_housing": fair_housing_output_guardrail,
        "competitor_blocking": competitor_blocking_guardrail,
        "prisma_airs": prisma_airs_output_guardrail,
        "unauthorized_promises": unauthorized_promises_output_guardrail,
        "legal_advice": legal_advice_output_guardrail,
    }

    return [guardrail_map[name] for name in settings.enabled_output_guardrails if name in guardrail_map]


async def call_and_save_tool(
    mcp_server: "CachingMCPServer",
    tool_name: str,
    arguments: dict[str, Any],
    context: SessionScope,
    store_attribute: str,
    extract_attribute: str | None = None,
    filter_function=None,
    skip_pre_processors: bool = False,
    skip_post_processors: bool = False,
):
    """Call an MCP tool and cache the result in the session context.

    If the attribute already exists on the context, the cached value is used
    and no tool call is made.

    Args:
        mcp_server: MCP server instance to call the tool on
        tool_name: Name of the tool to call
        arguments: Arguments to pass to the tool
        context: Session context to store the result in
        store_attribute: Attribute name to store the result under
        extract_attribute: Optional key/attribute to extract from the result.
            If provided and the result is a dict, extracts result[extract_attribute].
            If provided and the result is an object, extracts getattr(result, extract_attribute).
        filter_function: Function to filter results before storing
        skip_pre_processors: If True, skip MCP pre-processors (e.g. for internal
            prefetch calls that should bypass verification checks).
        skip_post_processors: If True, skip MCP post-processors (e.g. for prefetch
            calls — task-activity emitters and other side effects must not fire
            from internal-fetch paths).
    """
    # Early return if value is already cached
    if getattr(context, store_attribute, None):
        logger.info(f"Using cached value for {store_attribute} from {tool_name}")
        return

    # Call the tool and extract the result
    tool_output = await mcp_server.call_tool(
        tool_name,
        arguments,
        skip_pre_processors=skip_pre_processors,
        skip_post_processors=skip_post_processors,
    )
    result = extract_tool_result(tool_output)

    # Extract specific attribute if requested
    if extract_attribute and result:
        if isinstance(result, dict) and extract_attribute in result:
            result = result[extract_attribute]
        elif hasattr(result, extract_attribute):
            result = getattr(result, extract_attribute)

    if filter_function:
        result = filter_function(result)

    logger.info(f"Stored {store_attribute} in context from {tool_name}")
    setattr(context, store_attribute, result)

    # previously, there was no return value
    # returning tool_name for span data purposes
    return tool_name
