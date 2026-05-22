"""Analyze create_service_request failures in LangSmith.

Usage:
    uv run python scripts/service_request_failures.py [--days N] [--projects PROJECT...] [--csv FILE]

Examples:
    uv run python scripts/service_request_failures.py
    uv run python scripts/service_request_failures.py --days 7
    uv run python scripts/service_request_failures.py --csv data/sr_failures.csv
    uv run python scripts/service_request_failures.py --projects prod_renter_ai_resident_voice prod_renter_ai_resident_sms
"""

import argparse
import csv
import json
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_PROJECTS = ["prod_renter_ai_resident_voice"]

TOOL_NAME = "create_service_request"


def get_api_key() -> str:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    for line in env_path.read_text().splitlines():
        if line.startswith("LANGSMITH_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("LANGSMITH_API_KEY not found in .env")


def get_project_id(api_key: str, project_name: str) -> str:
    req = urllib.request.Request(
        f"https://api.smith.langchain.com/api/v1/sessions?name={project_name}",
        headers={"x-api-key": api_key},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    if isinstance(data, list) and data:
        return data[0]["id"]
    if isinstance(data, dict) and "id" in data:
        return data["id"]
    raise RuntimeError(f"Project not found: {project_name}")


def query_call_tool_runs(api_key: str, project_id: str, start_time: str, cursor=None):
    body = {
        "session": [project_id],
        "is_root": False,
        "filter": 'eq(name, "call_tool")',
        "start_time": start_time,
        "limit": 100,
    }
    if cursor:
        body["cursor"] = cursor
    for attempt in range(5):
        req = urllib.request.Request(
            "https://api.smith.langchain.com/api/v1/runs/query",
            data=json.dumps(body).encode(),
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:
                wait = 2 ** attempt
                print(f"  Rate limited, retrying in {wait}s...", file=sys.stderr)
                time.sleep(wait)
            else:
                raise


def extract_output_fields(run: dict) -> dict:
    """Extract service_request_created, agent_response, and service_request_id from run outputs."""
    outputs = run.get("outputs") or {}
    structured = outputs.get("structuredContent") or {}
    content_list = outputs.get("content") or []

    sr_created = structured.get("service_request_created")
    agent_resp = structured.get("agent_response", "")
    sr_id = structured.get("service_request_id")

    for c in content_list:
        if isinstance(c, dict) and c.get("type") == "text":
            try:
                parsed = json.loads(c.get("text", ""))
                if sr_created is None:
                    sr_created = parsed.get("service_request_created")
                if not agent_resp:
                    agent_resp = parsed.get("agent_response", "")
                if sr_id is None:
                    sr_id = parsed.get("service_request_id")
            except (json.JSONDecodeError, AttributeError):
                pass

    return {
        "service_request_created": sr_created,
        "agent_response": agent_resp,
        "service_request_id": sr_id,
        "is_error": outputs.get("isError", False),
    }


def is_error_or_empty_response(fields: dict) -> bool:
    """Only error responses and null/empty responses are true failures."""
    agent_resp = fields.get("agent_response")
    if agent_resp == "An error occurred while creating the service request.":
        return True
    if agent_resp in ("None", "", None):
        return True
    return False


def classify_failure(run: dict, fields: dict) -> str:
    agent_resp = fields["agent_response"]

    if agent_resp == "An error occurred while creating the service request.":
        return "Generic MCP error"
    if agent_resp in ("None", "", None):
        return "Null/None response"
    return f"Other: {str(agent_resp)[:100]}"


def fetch_trace_metadata(api_key: str, trace_id: str) -> dict:
    req = urllib.request.Request(
        f"https://api.smith.langchain.com/api/v1/runs/{trace_id}",
        headers={"x-api-key": api_key},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        run = json.loads(resp.read())
    extra = run.get("extra") or {}
    return extra.get("metadata") or {}


def is_true_failure(run: dict, fields: dict) -> bool:
    if run.get("error") and "CancelledError" in str(run.get("error")):
        return False
    if fields["is_error"] or run.get("error"):
        return True
    return is_error_or_empty_response(fields)


def collect_service_request_runs(api_key: str, project_id: str, start_time: str) -> list[dict]:
    runs = []
    cursor = None
    page = 0
    while True:
        page += 1
        data = query_call_tool_runs(api_key, project_id, start_time, cursor)
        batch = data.get("runs", [])
        if not batch:
            break
        for r in batch:
            if (r.get("inputs") or {}).get("tool_name") == TOOL_NAME:
                runs.append(r)
        cursor = (data.get("cursors") or {}).get("next")
        if not cursor:
            break
        print(f"  Page {page}: scanned {len(batch)} call_tool runs, {len(runs)} {TOOL_NAME} so far")
    return runs


def analyze_and_print(runs: list[dict], project_name: str):
    daily = defaultdict(lambda: {"success": 0, "failure": 0, "failure_modes": defaultdict(int), "traces": []})
    failures_by_category = defaultdict(list)

    for run in runs:
        day = (run.get("start_time") or "unknown")[:10]
        fields = extract_output_fields(run)

        if is_true_failure(run, fields):
            daily[day]["failure"] += 1
            category = classify_failure(run, fields)
            daily[day]["failure_modes"][category] += 1
            daily[day]["traces"].append(run.get("trace_id", "")[:36])
            failures_by_category[category].append({"day": day, "trace_id": run.get("trace_id", "")[:36]})
        else:
            daily[day]["success"] += 1

    total_success = sum(d["success"] for d in daily.values())
    total_failure = sum(d["failure"] for d in daily.values())
    grand_total = total_success + total_failure

    print(f"\n{'=' * 70}")
    print(f"  {project_name}")
    print(f"  {grand_total} calls | {total_success} success | {total_failure} failure", end="")
    if grand_total:
        print(f" ({total_failure / grand_total * 100:.1f}% failure rate)")
    else:
        print(" (no data)")
    print(f"{'=' * 70}")

    print(f"\n{'Date':<12} {'Total':>6} {'OK':>6} {'Fail':>6} {'Rate':>7}")
    print("-" * 40)
    for day in sorted(daily):
        info = daily[day]
        total = info["success"] + info["failure"]
        pct = info["failure"] / total * 100 if total else 0
        print(f"{day:<12} {total:>6} {info['success']:>6} {info['failure']:>6} {pct:>6.1f}%")

    if failures_by_category:
        print(f"\n--- Failure Modes ---\n")
        for cat in sorted(failures_by_category, key=lambda c: -len(failures_by_category[c])):
            items = failures_by_category[cat]
            by_day = defaultdict(int)
            for f in items:
                by_day[f["day"]] += 1
            day_str = ", ".join(f"{d}: {n}" for d, n in sorted(by_day.items()))
            print(f"  [{len(items):>2}x] {cat}")
            print(f"       Days: {day_str}")
            traces = [f["trace_id"] for f in items[:3]]
            print(f"       Traces: {', '.join(traces)}")
            print()


def collect_failures(runs: list[dict]) -> list[dict]:
    """Return list of failure dicts with run-level info (before trace metadata fetch)."""
    failures = []
    for run in runs:
        fields = extract_output_fields(run)
        if not is_true_failure(run, fields):
            continue
        failures.append({
            "trace_id": run.get("trace_id", ""),
            "failure_time": run.get("start_time", ""),
            "failure_mode": classify_failure(run, fields),
            "agent_response": fields["agent_response"],
        })
    return failures


def export_csv(api_key: str, failures: list[dict], csv_path: str):
    """Fetch trace metadata for each failure and write CSV."""
    # Dedupe trace IDs — multiple failures can share a trace
    unique_traces = {f["trace_id"]: None for f in failures}
    print(f"\nFetching metadata for {len(unique_traces)} unique trace(s)...")
    for i, trace_id in enumerate(unique_traces, 1):
        if i % 10 == 0 or i == len(unique_traces):
            print(f"  {i}/{len(unique_traces)}", file=sys.stderr)
        unique_traces[trace_id] = fetch_trace_metadata(api_key, trace_id)

    # Collect all metadata keys across traces
    all_meta_keys: set[str] = set()
    for meta in unique_traces.values():
        all_meta_keys.update(meta.keys())
    sorted_meta_keys = sorted(all_meta_keys)

    fixed_columns = ["trace_id", "failure_time", "channel", "failure_mode", "agent_response"]
    # Exclude keys already represented by fixed columns
    extra_meta_keys = [k for k in sorted_meta_keys if k not in ("channel",)]
    header = fixed_columns + extra_meta_keys

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for failure in sorted(failures, key=lambda r: r["failure_time"]):
            meta = unique_traces.get(failure["trace_id"]) or {}
            row = [
                failure["trace_id"],
                failure["failure_time"],
                meta.get("channel", ""),
                failure["failure_mode"],
                failure["agent_response"],
            ]
            for k in extra_meta_keys:
                val = meta.get(k, "")
                row.append(json.dumps(val) if isinstance(val, (dict, list)) else val)
            writer.writerow(row)

    print(f"\nWrote {len(failures)} rows to {csv_path}")


def main():
    parser = argparse.ArgumentParser(description="Analyze create_service_request failures in LangSmith")
    parser.add_argument("--days", type=int, default=3, help="Number of days to look back (default: 3)")
    parser.add_argument("--projects", nargs="+", default=DEFAULT_PROJECTS, help="LangSmith project names")
    parser.add_argument("--csv", type=str, default=None, help="Export failed traces to CSV file")
    args = parser.parse_args()

    api_key = get_api_key()
    start_time = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%dT00:00:00Z")
    print(f"Searching from {start_time} across {len(args.projects)} project(s)...\n")

    all_failures: list[dict] = []
    for project_name in args.projects:
        print(f"Fetching {project_name}...")
        project_id = get_project_id(api_key, project_name)
        runs = collect_service_request_runs(api_key, project_id, start_time)
        analyze_and_print(runs, project_name)
        all_failures.extend(collect_failures(runs))

    if args.csv and all_failures:
        export_csv(api_key, all_failures, args.csv)


if __name__ == "__main__":
    main()
