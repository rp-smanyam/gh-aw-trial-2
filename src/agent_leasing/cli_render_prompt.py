#!/usr/bin/env python3
# ruff: noqa: T201
"""
CLI tool for offline rendering of Jinja2 prompt templates.

Renders prompt templates with a SessionScope context constructed from an AskRequest
payload file, producing the fully resolved prompt text. This enables testing prompt
variations across different payloads, channels, and models in external platforms
like OpenAI Playground or Builder.

Examples:
    # Render resident instructions for a chat payload
    $ uv run cli-render-prompt resident:instructions example_data/resident/chat/example_ask_request_ll.json

    # Render voice responder prompt
    $ uv run cli-render-prompt resident:voice_responder example_data/resident/voice/example_ask_request_knck.json

    # Override channel
    $ uv run cli-render-prompt resident:instructions payload.json --channel VOICE

    # JSON output with metadata
    $ uv run cli-render-prompt resident:instructions payload.json --json

    # List available prompts
    $ uv run cli-render-prompt --list
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime

import jinja2

from agent_leasing.agent.util import AgentWithMCP, get_channel_from_product
from agent_leasing.api.model import AskRequest
from agent_leasing.models.context import SessionScope
from agent_leasing.settings import settings

# Prompt registry: identifier -> file path relative to agent package
PROMPT_REGISTRY = {
    "resident:instructions": os.path.join(os.path.dirname(__file__), "agent", "resident_one_agent", "INSTRUCTIONS.md"),
    "resident:voice_responder": os.path.join(
        os.path.dirname(__file__), "agent", "resident_one_agent", "VOICE_RESPONDER.md"
    ),
    "simple:prompt": os.path.join(os.path.dirname(__file__), "agent", "simple", "PROMPT.md"),
}


def load_payload(filename: str) -> dict:
    """Load JSON payload from file."""
    try:
        with open(filename) as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: File '{filename}' not found.", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in '{filename}': {e}", file=sys.stderr)
        sys.exit(1)


def load_context_overrides(filename: str) -> dict:
    """Load optional JSON overrides for SessionScope fields."""
    try:
        with open(filename) as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: Context file '{filename}' not found.", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in context file '{filename}': {e}", file=sys.stderr)
        sys.exit(1)


def build_session_scope(
    ask_request: AskRequest,
    live_overrides: dict | None = None,
    overrides: dict | None = None,
    current_time: datetime | None = None,
) -> SessionScope:
    """Construct a SessionScope from an AskRequest with safe defaults for runtime fields.

    Runtime-populated fields get safe defaults so templates render without errors.
    Precedence (highest wins): overrides (--context) > live_overrides (--live) > defaults.
    """
    defaults = {
        "ask_request": ask_request,
        "current_time": current_time or datetime.now(),
        "disabled_modules": [],
        "disabled_tools": [],
        "packages": None,
        "service_requests": None,
        "signed_up_community_events": None,
        "identity_verified": {},
        "identity_verified_with_birth_year": {},
        "sms_consent_status": None,
    }
    # Legacy path: seed property_data so the prompt template has a value to render.
    if not settings.property_marketing_info_tool_enabled:
        defaults["property_data"] = ""

    if live_overrides:
        defaults.update(live_overrides)
    if overrides:
        defaults.update(overrides)

    return SessionScope(**defaults)


async def fetch_live_data(property_id: str) -> dict:
    """Fetch property_data and disabled_modules from LDP (flag=False) or disabled_modules only (flag=True)."""
    from agent_leasing.clients.ldp import (
        ALL_MODULES,
        MODULE_TO_MCP_TOOLS,
        fetch_ldp_property_data,
        get_disabled_tools_from_disabled_modules,
    )

    data = await fetch_ldp_property_data(property_id)
    disabled_modules = [m for m in ALL_MODULES if m not in (data.get("enabled_modules") or [])]
    disabled_tools = get_disabled_tools_from_disabled_modules(MODULE_TO_MCP_TOOLS, disabled_modules)
    result = {
        "disabled_modules": disabled_modules,
        "disabled_tools": disabled_tools,
    }
    # Populate property_data for the legacy prompt-injection path
    if not settings.property_marketing_info_tool_enabled:
        result["property_data"] = data.get("resident_summary") or ""
    return result


def render_prompt(
    template_text: str,
    context: SessionScope,
    channel: str,
    prompt_id: str,
) -> str:
    """Render a Jinja2 prompt template with the given context.

    Uses the same rendering logic as the agent classes:
    - BaseResidentAgent._get_agent_instructions for resident prompts
    - SimpleAgent._get_agent_instructions for simple prompts
    """
    environment = jinja2.Environment()
    template = environment.from_string(template_text)

    # Simple prompts use fewer template variables
    if prompt_id.startswith("simple:"):
        return template.render(
            current_time=context.current_time.isoformat(),
            context=context,
        )

    # Resident prompts use the full set of template variables
    return template.render(
        current_time=context.current_time.isoformat(),
        context=context,
        channel=channel,
        disabled_modules=context.disabled_modules,
        disabled_tools=context.disabled_tools,
        settings=settings,
    )


def list_prompts() -> None:
    """Print available prompt identifiers with file paths."""
    print("Available prompt identifiers:\n")
    for identifier, filepath in sorted(PROMPT_REGISTRY.items()):
        rel_path = os.path.relpath(filepath)
        print(f"  {identifier:<30s} {rel_path}")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Render Jinja2 prompt templates offline with an AskRequest payload.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run cli-render-prompt resident:instructions example_data/resident/chat/example_ask_request_ll.json
  uv run cli-render-prompt resident:voice_responder example_data/resident/voice/example_ask_request_knck.json
  uv run cli-render-prompt resident:instructions payload.json --channel VOICE
  uv run cli-render-prompt resident:instructions payload.json --json
  uv run cli-render-prompt --list
        """,
    )

    parser.add_argument(
        "prompt_id",
        nargs="?",
        help="Prompt identifier (e.g., resident:instructions, resident:voice_responder, simple:prompt)",
    )

    parser.add_argument(
        "payload_file",
        nargs="?",
        help="JSON file with AskRequest payload",
    )

    parser.add_argument(
        "--version",
        type=int,
        default=None,
        help="Prompt version (default: from payload's prompt_version, or 0)",
    )

    parser.add_argument(
        "--channel",
        choices=["CHAT", "SMS", "EMAIL", "VOICE"],
        default=None,
        help="Override channel (default: derived from product)",
    )

    parser.add_argument(
        "--context",
        dest="context_file",
        default=None,
        help="JSON file with SessionScope field overrides",
    )

    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Fetch property_data and disabled_modules from LDP (requires .env credentials)"
            if not settings.property_marketing_info_tool_enabled
            else "Fetch disabled_modules from LDP (requires .env credentials)"
        ),
    )

    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output as JSON with metadata",
    )

    parser.add_argument(
        "--list",
        action="store_true",
        help="List available prompt identifiers and exit",
    )

    parser.add_argument(
        "--current-time",
        default=None,
        help="Override current_time (ISO format; default: now)",
    )

    parser.add_argument(
        "-o",
        dest="output_file",
        default=None,
        help="Write to file instead of stdout",
    )

    args = parser.parse_args()

    # Handle --list
    if args.list:
        list_prompts()
        sys.exit(0)

    # Validate required arguments
    if not args.prompt_id:
        parser.error("prompt_id is required (use --list to see available identifiers)")
    if not args.payload_file:
        parser.error("payload_file is required")

    # Validate prompt_id
    if args.prompt_id not in PROMPT_REGISTRY:
        print(
            f"Error: Unknown prompt identifier '{args.prompt_id}'. "
            f"Available: {', '.join(sorted(PROMPT_REGISTRY.keys()))}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load payload and parse AskRequest
    payload = load_payload(args.payload_file)
    ask_request = AskRequest(**payload)

    # Determine version
    if args.version is not None:
        version = args.version
    elif ask_request.prompt_version is not None:
        version = ask_request.prompt_version
    else:
        version = 0

    # Load context overrides
    overrides = None
    if args.context_file:
        overrides = load_context_overrides(args.context_file)

    # Parse current_time override
    current_time = None
    if args.current_time:
        try:
            current_time = datetime.fromisoformat(args.current_time)
        except ValueError:
            print(f"Error: Invalid ISO format for --current-time: '{args.current_time}'", file=sys.stderr)
            sys.exit(1)

    # Fetch live data from LDP if requested
    live_data = {}
    if args.live:
        try:
            live_data = asyncio.run(fetch_live_data(ask_request.property_id))
        except Exception as e:
            print(f"Warning: --live fetch failed: {e}", file=sys.stderr)

    # Build SessionScope
    context = build_session_scope(
        ask_request, live_overrides=live_data or None, overrides=overrides, current_time=current_time
    )

    # Determine channel
    if args.channel:
        channel = args.channel
    else:
        channel = get_channel_from_product(ask_request.product)

    # Load prompt template
    prompt_file = PROMPT_REGISTRY[args.prompt_id]
    template_text = AgentWithMCP._get_prompt(prompt_file, version=version)

    # Render
    rendered = render_prompt(template_text, context, channel, args.prompt_id)

    # Output
    if args.json_output:
        output = json.dumps(
            {
                "prompt_id": args.prompt_id,
                "channel": channel,
                "version": version,
                "rendered": rendered,
            },
            indent=2,
        )
    else:
        output = rendered

    if args.output_file:
        with open(args.output_file, "w") as f:
            f.write(output)
        print(f"Written to {args.output_file}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
