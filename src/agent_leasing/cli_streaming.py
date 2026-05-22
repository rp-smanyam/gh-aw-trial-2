#!/usr/bin/env python3
# ruff: noqa: T201
"""
CLI client for the streaming agent endpoint.

Connects to /v1/agent/stream and displays streaming responses in real-time.

When a payload file is provided, it sets up the conversation context (product info,
session IDs, etc.), and the conversation continues with that context maintained.

The program requires a running server to connect to.

Examples:
    # Start with default context
    $ uv run cli-streaming

    # Start with payload file for context, then continue conversation
    $ uv run cli-streaming src/agent_leasing/api/example_data/resident/chat/example_ask_request_ll.json

    # Specify custom base URL and timeout
    $ uv run cli-streaming --url https://alpha-agent-leasing.knocktest.com --timeout 60

Commands:
    - Type your message and press Enter to send
    - Type 'exit', 'quit', or 'q' to end the conversation
    - Press Ctrl+C to interrupt at any time

The payload file should contain the JSON request body for the streaming endpoint.
It sets the context (product, session IDs, product_info) for all subsequent messages.
See docs/STREAMING.md for payload structure examples.
"""

import argparse
import json
import sys

import httpx
from httpx_sse import connect_sse


def load_payload(filename: str) -> dict:
    """Load JSON payload from file."""
    try:
        with open(filename, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: File '{filename}' not found.", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in '{filename}': {e}", file=sys.stderr)
        sys.exit(1)


def prompt_for_message() -> str | None:
    """Prompt user for a message. Returns None if user wants to quit."""
    try:
        message = input("\nYou: ").strip()
        if not message:
            print("Message cannot be empty. Type 'exit' or 'quit' to end the conversation.", file=sys.stderr)
            return prompt_for_message()
        if message.lower() in ("exit", "quit", "q"):
            return None
        return message
    except (KeyboardInterrupt, EOFError):
        print("\nExiting...", file=sys.stderr)
        return None


def create_default_payload(message: str) -> dict:
    """Create a minimal payload for testing."""
    return {
        "product": "resident_one_chat",
        "prompt": message,
        "chat_session_id": "cli-streaming-session",
        "request_id": "cli-streaming-request",
        "product_info": {"knock_property_id": "21521", "knock_prospect_id": "95946"},
    }


def stream_agent_response(url: str, payload: dict, timeout: int, show_header: bool = True) -> bool:
    """Connect to streaming endpoint and display responses.

    Returns True if successful, False if there was an error.
    """
    endpoint = f"{url}/v1/agent/stream"

    if show_header:
        print(f"Connecting to: {endpoint}")
        print("-" * 80)

    try:
        with httpx.Client(timeout=timeout) as client:
            with connect_sse(client, "POST", endpoint, json=payload) as event_source:
                for sse in event_source.iter_sse():
                    # Handle the [DONE] marker
                    if sse.data == "[DONE]":
                        print("\n" + "-" * 80)
                        print("Stream complete.")
                        break

                    try:
                        data = json.loads(sse.data)

                        # Extract fields
                        content = data.get("content", "")
                        status = data.get("status", "")
                        phase = data.get("phase", "")
                        # elapsed = data.get("elapsed", 0)
                        done = data.get("done", False)

                        # print content as it arrives (no newline for continuous text)
                        if content:
                            print(content, end="", flush=True)

                        # Show status updates for non-generating phases
                        if phase != "generating" and not content:
                            print(f"[{phase}]", end=" ", flush=True)

                        # Check for completion or error
                        if status == "error":
                            print(f"\nError: {content}", file=sys.stderr)
                            return False
                        elif done:
                            # Final message received
                            pass

                    except json.JSONDecodeError:
                        # If it's not JSON, just print the raw data
                        print(f"[Raw: {sse.data}]")

        return True

    except httpx.TimeoutException:
        print(f"\nError: Request timed out after {timeout} seconds.", file=sys.stderr)
        return False
    except httpx.ConnectError:
        print(f"\nError: Could not connect to {endpoint}", file=sys.stderr)
        print("Make sure the server is running.", file=sys.stderr)
        return False
    except httpx.HTTPStatusError as e:
        print(f"\nError: HTTP {e.response.status_code}", file=sys.stderr)
        print(e.response.text, file=sys.stderr)
        return False
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.", file=sys.stderr)
        return False
    except Exception as e:
        print(f"\nUnexpected error: {e}", file=sys.stderr)
        return False


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Stream responses from the agent endpoint.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run cli-streaming src/agent_leasing/api/example_data/resident/chat/example_ask_request_ll.json
  uv run cli-streaming src/agent_leasing/api/example_data/resident/chat/example_ask_request_ll.json --url http://localhost:8000
        """,
    )

    parser.add_argument(
        "payload",
        nargs="?",
        help="JSON file containing the request payload",
    )

    parser.add_argument(
        "--url",
        default="http://localhost:8000",
        help="Base URL of the agent service (default: http://localhost:8000)",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Request timeout in seconds (default: 120)",
    )

    args = parser.parse_args()

    # Load or create initial payload for context
    if args.payload:
        payload = load_payload(args.payload)
        print(f"Loaded context from: {args.payload}")

        # Always get the initial prompt from the user and store it under prompt
        message = prompt_for_message()
        if message is None:
            print("Goodbye!")
            sys.exit(0)

        payload["prompt"] = message
    else:
        print("No payload file provided. Using default context.")
        message = prompt_for_message()
        if message is None:
            print("Goodbye!")
            sys.exit(0)
        payload = create_default_payload(message)

    print("Type 'exit', 'quit', or 'q' to end the conversation.\n")

    # Multi-turn conversation loop
    first_turn = True
    while True:
        # Stream the response
        success = stream_agent_response(args.url, payload, args.timeout, show_header=first_turn)
        first_turn = False

        if not success:
            # Error occurred, ask if user wants to try again
            retry = input("\nWould you like to try another message? (y/n): ").strip().lower()
            if retry != "y":
                break

        # Prompt for next message
        message = prompt_for_message()
        if message is None:
            print("\nGoodbye!")
            break

        # Update payload with new prompt, keep other fields for context
        payload["prompt"] = message


if __name__ == "__main__":
    main()
