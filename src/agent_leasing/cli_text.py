#!/usr/bin/env python3
# ruff: noqa: T201
"""
CLI client for the non-streaming agent endpoint.

Connects to /v1/agent/ask and displays responses.

When a payload file is provided, it sets up the conversation context (product info,
session IDs, etc.), and the conversation continues with that context maintained.

The program requires a running server to connect to.

Examples:
    # Start with default context
    $ uv run cli-text

    # Start with payload file for context, then continue conversation
    $ uv run cli-text src/agent_leasing/api/example_data/resident/chat/example_ask_request_ll.json

    # Specify custom base URL and timeout
    $ uv run cli-text --url https://alpha-agent-leasing.knocktest.com --timeout 60

Commands:
    - Type your message and press Enter to send
    - Type 'exit', 'quit', or 'q' to end the conversation
    - Press Ctrl+C to interrupt at any time

The payload file should contain the JSON request body for the ask endpoint.
It sets the context (product, session IDs, product_info) for all subsequent messages.
"""

import argparse
import json
import sys

import httpx


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
        "chat_session_id": "cli-text-session",
        "request_id": "cli-text-request",
        "product_info": {"knock_property_id": "21521", "knock_prospect_id": "95946"},
    }


def send_agent_request(url: str, payload: dict, timeout: int, show_header: bool = True) -> bool:
    """Send request to ask endpoint and display response.

    Returns True if successful, False if there was an error.
    """
    endpoint = f"{url}/v1/agent/ask"

    if show_header:
        print(f"Connecting to: {endpoint}")
        print("-" * 80)

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(endpoint, json=payload)
            response.raise_for_status()

            if response.status_code == httpx.codes.NO_CONTENT:
                print("\nAgent: [No response content]")
                print("-" * 80)
                print(json.dumps(dict(response.headers)))
                print("-" * 80)
                return True

            try:
                data = response.json()
            except json.JSONDecodeError:
                print("\nAgent: [No response content]")
                print("-" * 80)
                print(json.dumps(dict(response.headers)))
                print("-" * 80)
                return True

            # Extract and display the response
            # Response is nested: content.chat is a stringified JSON with "response" field
            content_obj = data.get("content", {})
            chat_str = content_obj.get("chat", "") if content_obj else ""
            if chat_str:
                try:
                    chat_data = json.loads(chat_str)
                    content = chat_data.get("response", "")
                except json.JSONDecodeError:
                    content = chat_str
            else:
                content = ""

            if content:
                print(f"\nAgent: {content}")
            else:
                print("\nAgent: [No response content]")

            print("-" * 80)
            print(json.dumps(dict(response.headers)))
            print("-" * 80)
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
        description="Send requests to the agent ask endpoint.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run cli-text src/agent_leasing/api/example_data/resident/chat/example_ask_request_ll.json
  uv run cli-text src/agent_leasing/api/example_data/resident/chat/example_ask_request_ll.json --url http://localhost:8000
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
        # Send the request
        success = send_agent_request(args.url, payload, args.timeout, show_header=first_turn)
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
