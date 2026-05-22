"""
Pre-processing functions for MCP tool inputs.

These functions are applied to tool arguments before the tool is called.
They can modify, filter, or transform tool inputs as needed.
"""

from typing import TYPE_CHECKING, Any, Callable

import structlog
from agents import custom_span
from mcp.types import CallToolResult

from agent_leasing.settings import settings
from agent_leasing.util.tracing_utils import set_span_data

if TYPE_CHECKING:
    from agent_leasing.models.context import SessionScope

logger = structlog.getLogger(__name__)


class VerificationError(Exception):
    """Raised when verification is required but not completed."""

    pass


# ALL Pre Processors (Guardrails)


def mcp_input_guardrails(arguments: dict[str, Any] | None, **kwargs) -> CallToolResult:
    """Guardrails for MCP tool outputs."""

    with custom_span(
        "MCP Input Guardrails",
        data={},
    ) as c_span:
        # Detect and redact PII in the tool output

        arguments, clamped_numbers = _mcp_numeric_guardrail(arguments)

        # limit output length based on settings values

        arguments, too_long = _mcp_length_guardrail_input(arguments)

        set_span_data(
            c_span,
            clamped_numbers=clamped_numbers,
            too_long=too_long,
            sanitized_input=arguments,
        )

        return arguments


def _mcp_numeric_guardrail(arguments: dict[str, Any] | None) -> tuple[dict[str, Any] | None, bool]:
    """Clamps numeric ranges for each numeric or numeric as string in the arguments"""
    if arguments is None:
        return arguments, False

    processed_arguments, clamped_numbers = _process_value(arguments, _clamp_numeric_range)
    return processed_arguments, clamped_numbers


def _clamp_numeric_range(value: str | int | float) -> str | int | float:
    """Clamp a numeric range for a single value"""
    # Handle numeric types directly
    if isinstance(value, int | float):
        if value < settings.mcp_min_numeric_value:
            return settings.mcp_min_numeric_value
        elif value > settings.mcp_max_numeric_value:
            return settings.mcp_max_numeric_value
        return value

    # Handle string representations of numbers
    if isinstance(value, str):
        try:
            # Try to parse as float first (handles both int and float strings)
            numeric_value = float(value)

            # Clamp the value
            if numeric_value < settings.mcp_min_numeric_value:
                clamped = settings.mcp_min_numeric_value
            elif numeric_value > settings.mcp_max_numeric_value:
                clamped = settings.mcp_max_numeric_value
            else:
                clamped = numeric_value

            # Return as string in the same format (preserve int vs float)
            if "." in value or "e" in value.lower():
                return str(clamped)
            else:
                return str(int(clamped))
        except ValueError:
            # Not a numeric string, return as-is
            return value

    return value


def _mcp_length_guardrail_input(arguments: dict[str, Any] | None) -> tuple[dict[str, Any] | None, bool]:
    """Trim individual strings to max length"""
    if arguments is None:
        return arguments, False

    processed_arguments, too_long = _process_value(arguments, _truncate_string)
    return processed_arguments, too_long


def _truncate_string(value: str) -> str:
    """Truncate a string to max length"""
    if isinstance(value, str) and len(value) > settings.mcp_max_input_length:
        return value[: settings.mcp_max_input_length]
    return value


def _process_value(value: Any, func: Callable[[Any], Any]) -> tuple[Any, bool]:
    """Recursively process a value, applying func and handling nested structures.

    Args:
        value: The value to process (can be dict, list, or primitive)
        func: Function to apply to primitive values (str, int, float)

    Returns:
        Tuple of (processed_value, changed) where changed is True if any value was modified
    """
    changed = False

    if isinstance(value, dict):
        # Recursively process dictionary values
        result = {}
        for k, v in value.items():
            result[k], value_changed = _process_value(v, func)
            changed = changed or value_changed
        return result, changed
    elif isinstance(value, list):
        # Recursively process list items
        result = []
        for item in value:
            processed_item, item_changed = _process_value(item, func)
            result.append(processed_item)
            changed = changed or item_changed
        return result, changed
    elif isinstance(value, int | float | str):
        # Apply the function to primitive values
        processed_value = func(value)
        _func_changed = processed_value != value
        return processed_value, _func_changed
    else:
        # Return other types as-is
        return value, False


# Utility functions for creating processor dictionaries


def create_mcp_pre_processors(guardrail_tools: list[str], extras: dict[str, list] | None = None) -> dict[str, list]:
    """Create tool pre-processors dict with mcp_input_guardrails for all tools.

    Args:
        guardrail_tools: List of tool names to create processors for
        extras: Optional dict mapping tool names to additional processors to append.
                Can include tools not in the base list.

    Returns:
        Dictionary mapping tool names to list of pre-processor functions
    """
    processors = {tool: [mcp_input_guardrails] for tool in guardrail_tools}

    if extras:
        for tool, extra_processors in extras.items():
            if tool not in processors:
                processors[tool] = []
            processors[tool].extend(extra_processors)

    return processors


def verification_pre_processor(tool_name: str) -> Callable[[dict], dict]:
    """Pre-processor factory that creates a verification checker for a specific tool.

    Args:
        tool_name: The name of the tool being protected

    Returns:
        A pre-processor function that checks verification status and raises
        VerificationError if verification is required but not completed.
        The returned function receives ``context`` as a keyword argument at
        call time (injected by CachingMCPServer) so it always uses the
        current request's SessionScope, not a stale closure reference.
    """
    # Import here to avoid circular imports
    from agent_leasing.agent.tools.verification_check import check_verification_status

    def check(arguments: dict, context: "SessionScope" = None, **kwargs) -> dict:
        is_verified, error_msg = check_verification_status(context, tool_name)
        if not is_verified:
            logger.info(
                "Verification check failed in MCP pre-processor",
                tool_name=tool_name,
                error=error_msg,
            )
            raise VerificationError(error_msg)
        return arguments

    return check
