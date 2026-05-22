#!/usr/bin/env python3
"""Fetch a real payload from CloudWatch logs for local testing.

Queries CloudWatch Insights for the most recent payload matching a given
resident ID (and optionally property ID), then saves it as JSON.

Usage:
  uv run scripts/fetch_payload.py alpha voice 141
  uv run scripts/fetch_payload.py beta chat 141 --property-id 21521
  uv run scripts/fetch_payload.py prod sms 141 --days 14
  uv run scripts/fetch_payload.py alpha voice 141 --output data/payloads/custom.json
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ENVS = {
    "alpha": {"profile": None},
    "beta": {"profile": "beta"},
    "prod": {"profile": "prod"},
}

CHANNELS = {"voice", "chat", "sms", "email"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch a payload from CloudWatch logs for local testing.",
    )
    parser.add_argument("environment", choices=ENVS.keys(), help="Target environment")
    parser.add_argument("channel", choices=sorted(CHANNELS), help="Channel type")
    parser.add_argument("resident_id", help="knock_resident_id to filter on")
    parser.add_argument("--property-id", help="knock_property_id to filter on")
    parser.add_argument(
        "--days", type=int, default=7, help="How far back to search (default: 7)"
    )
    parser.add_argument("--output", type=Path, help="Override default output path")
    return parser.parse_args()


def build_log_group(env: str, channel: str) -> str:
    if channel == "voice":
        return f"/ecs/{env}-agent-leasing-voice"
    return f"/ecs/{env}-agent-leasing"


def build_query(channel: str, resident_id: str, property_id: str | None) -> str:
    lines = ["fields @timestamp, @message"]

    if channel == "voice":
        lines.append("| filter event like 'Setting up real-time agent'")
    else:
        lines.append("| filter event like 'Input:'")

    lines.append(f"| filter payload.product_info.knock_resident_id = '{resident_id}'")

    if property_id:
        lines.append(
            f"| filter payload.product_info.knock_property_id = '{property_id}'"
        )

    lines.append("| sort @timestamp desc")
    lines.append("| limit 1")
    return "\n".join(lines)


def run_aws(cmd: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"AWS CLI error:\n{result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return result


def start_query(
    log_group: str,
    query: str,
    start_epoch: int,
    end_epoch: int,
    profile: str | None,
) -> str:
    cmd = [
        "aws",
        "logs",
        "start-query",
        "--log-group-name",
        log_group,
        "--start-time",
        str(start_epoch),
        "--end-time",
        str(end_epoch),
        "--query-string",
        query,
    ]
    if profile:
        cmd.extend(["--profile", profile])

    result = run_aws(cmd)
    return json.loads(result.stdout)["queryId"]


def poll_query_results(query_id: str, profile: str | None) -> list[dict]:
    cmd = ["aws", "logs", "get-query-results", "--query-id", query_id]
    if profile:
        cmd.extend(["--profile", profile])

    delay = 1.0
    deadline = time.time() + 60

    while time.time() < deadline:
        time.sleep(delay)
        result = run_aws(cmd)
        data = json.loads(result.stdout)
        status = data["status"]

        if status == "Complete":
            return data.get("results", [])
        if status in ("Failed", "Cancelled"):
            print(f"Query {status.lower()}.", file=sys.stderr)
            sys.exit(1)

        delay = min(delay * 2, 5.0)

    print("Query timed out after 60 seconds.", file=sys.stderr)
    sys.exit(1)


def extract_payload(results: list[list[dict]]) -> dict:
    # Find the @message field in the first result row
    message_raw = None
    for field in results[0]:
        if field["field"] == "@message":
            message_raw = field["value"]
            break

    if message_raw is None:
        print("No @message field in query results.", file=sys.stderr)
        sys.exit(1)

    try:
        message = json.loads(message_raw)
    except json.JSONDecodeError:
        print(f"Failed to parse @message as JSON. Raw value:\n{message_raw[:500]}", file=sys.stderr)
        sys.exit(1)

    if "payload" not in message:
        print(
            f"No 'payload' key in message. Available keys: {', '.join(message.keys())}",
            file=sys.stderr,
        )
        sys.exit(1)

    payload = message["payload"]

    # Handle double-serialized payload
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            print(
                f"Failed to parse double-serialized payload. Raw value:\n{payload[:500]}",
                file=sys.stderr,
            )
            sys.exit(1)

    return payload


def build_output_path(
    env: str, channel: str, resident_id: str, property_id: str | None
) -> Path:
    root = Path(__file__).resolve().parent.parent
    name = f"{env}-{channel}-r{resident_id}"
    if property_id:
        name += f"-p{property_id}"
    name += ".json"
    return root / "data" / "payloads" / name


def main():
    args = parse_args()

    env = args.environment
    channel = args.channel
    resident_id = args.resident_id
    property_id = args.property_id
    profile = ENVS[env]["profile"]

    log_group = build_log_group(env, channel)
    query = build_query(channel, resident_id, property_id)

    end_epoch = int(time.time())
    start_epoch = end_epoch - (args.days * 86400)

    print(f"Searching {log_group} for resident {resident_id}...")
    if property_id:
        print(f"  property filter: {property_id}")
    print(f"  time range: last {args.days} day(s)")

    query_id = start_query(log_group, query, start_epoch, end_epoch, profile)
    print(f"  query ID: {query_id}")

    results = poll_query_results(query_id, profile)

    if not results:
        print(
            f"\nNo results found. Try increasing --days (currently {args.days}).",
            file=sys.stderr,
        )
        sys.exit(1)

    payload = extract_payload(results)

    output_path = args.output or build_output_path(env, channel, resident_id, property_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n")

    # Print summary
    product = payload.get("product", "unknown")
    property_name = payload.get("product_info", {}).get("property_name", "unknown")
    print(f"\nSaved to {output_path}")
    print(f"  product: {product}")
    print(f"  property: {property_name}")


if __name__ == "__main__":
    main()
