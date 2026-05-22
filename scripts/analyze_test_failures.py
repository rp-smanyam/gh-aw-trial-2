"""
Analyze test failures across CI runs to find systemic flakiness.

Unlike test_failure_frequency.py, this script:
- Scans ALL runs (not just failed ones) to catch pool-absorbed failures
- Extracts channel info (email/chat/sms/voice) from test names
- Filters out single-PR failures to surface only cross-PR patterns
- Produces a markdown report suitable for triage

Examples:
    # Analyze failures from the last 24 hours (default)
    uv run python scripts/analyze_test_failures.py

    # Since a specific date
    uv run python scripts/analyze_test_failures.py --since 2026-03-30

    # Since a specific PR was merged (uses PR merge date as start)
    uv run python scripts/analyze_test_failures.py --since-pr 1185

    # Last 72 hours, write report to file
    uv run python scripts/analyze_test_failures.py --hours 72 --output data/failures.md

    # Minimum failure threshold
    uv run python scripts/analyze_test_failures.py --min-count 3
"""

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone


def run_gh(args: list[str], timeout: int = 120) -> str:
    """Run a gh CLI command and return stdout."""
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def get_pr_merge_time(pr_number: int) -> str | None:
    """Get the merge time of a PR as an ISO string."""
    output = run_gh([
        "pr", "view", str(pr_number),
        "--json=mergedAt",
        "--jq=.mergedAt",
    ])
    merged = output.strip()
    return merged if merged and merged != "null" else None


def get_runs(since: str, workflow: str = "ci.yml") -> list[dict]:
    """Fetch all CI runs since a given ISO timestamp."""
    output = run_gh([
        "run", "list",
        "--workflow", workflow,
        "--limit=200",
        "--json=databaseId,conclusion,createdAt,headBranch,event",
    ])
    if not output:
        return []

    runs = json.loads(output)
    return [
        r for r in runs
        if r["createdAt"] >= since and r["conclusion"] in ("success", "failure")
    ]


def extract_failed_tests(run_id: int) -> list[str]:
    """Extract all FAILED test case names from a run's full logs.

    Scans full logs (not just --log-failed) to catch pool-absorbed failures.
    """
    output = run_gh(
        ["run", "view", str(run_id), "--log"],
        timeout=300,
    )
    if not output:
        return []

    failed = []
    for line in output.splitlines():
        match = re.search(r"FAILED (tests/\S+)", line)
        if match:
            failed.append(match.group(1))
    return list(set(failed))


def parse_test_id(full_name: str) -> tuple[str, str]:
    """Extract (test_case_id, channel) from a full pytest node ID.

    Examples:
        tests/.../test_non_realtime_flow.py::test_response_correctness_email[foo_1]
        -> ("foo_1", "email")

        tests/.../test_realtime_thinker_flow.py::test_response_correctness_voice[bar]
        -> ("bar", "voice")

        tests/.../test_realtime_instruction_adherence.py::test_instruction_adherence[baz]
        -> ("baz", "other")
    """
    # Extract parametrized case ID
    case_match = re.search(r"\[(.+)\]$", full_name)
    case_id = case_match.group(1) if case_match else full_name

    # Extract channel from test function name
    channel = "other"
    for ch in ("email", "chat", "sms", "voice"):
        if f"_{ch}" in full_name:
            channel = ch
            break

    return case_id, channel


def _collect_failure_data(
    runs: list[dict],
    verbose: bool = False,
) -> tuple[
    dict[str, dict[str, list[int]]],  # case_data: case_id -> {channel -> [run_ids]}
    dict[str, set[str]],               # case_branches: case_id -> {branches}
    int,                                # runs_with_failures
    int,                                # skipped (clean)
    int,                                # infra_failures
]:
    """Scan runs and build per-test-case failure data."""
    run_failures: dict[int, list[str]] = {}
    skipped = 0
    infra_failures = 0

    for i, r in enumerate(runs):
        run_id = r["databaseId"]
        if verbose:
            print(
                f"  [{i + 1}/{len(runs)}] Scanning run {run_id} "
                f"({r['headBranch']}, {r['conclusion']})...",
                file=sys.stderr,
            )
        failed = extract_failed_tests(run_id)

        if not failed and r["conclusion"] == "failure":
            infra_failures += 1
            continue
        if not failed:
            skipped += 1
            continue

        run_failures[run_id] = failed

    case_data: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    case_branches: dict[str, set[str]] = defaultdict(set)
    run_branch = {r["databaseId"]: r["headBranch"] for r in runs}

    for run_id, failures in run_failures.items():
        branch = run_branch[run_id]
        for full_name in failures:
            case_id, channel = parse_test_id(full_name)
            case_data[case_id][channel].append(run_id)
            case_branches[case_id].add(branch)

    return case_data, case_branches, len(run_failures), skipped, infra_failures


def _total_count(channels: dict[str, list[int]]) -> int:
    return sum(len(rids) for rids in channels.values())


def analyze_json(
    runs: list[dict],
    min_count: int = 2,
    verbose: bool = False,
) -> str:
    """Analyze failures across runs and produce a JSON report."""
    if not runs:
        return json.dumps({"failures": [], "summary": {"runs_scanned": 0}})

    case_data, case_branches, runs_with_failures, skipped, infra_failures = (
        _collect_failure_data(runs, verbose)
    )

    cross_pr_cases = {
        case_id: channels
        for case_id, channels in case_data.items()
        if len(case_branches[case_id]) >= 2
    }

    sorted_cases = sorted(
        cross_pr_cases.items(),
        key=lambda x: _total_count(x[1]),
        reverse=True,
    )

    failures = []
    for case_id, channels in sorted_cases:
        count = _total_count(channels)
        if count < min_count:
            continue

        all_run_ids: list[int] = []
        for rids in channels.values():
            all_run_ids.extend(rids)

        failures.append({
            "case_id": case_id,
            "count": count,
            "channels": {ch: len(rids) for ch, rids in channels.items()},
            "branches": sorted(case_branches[case_id]),
            "run_ids": sorted(set(all_run_ids))[:10],
        })

    result = {
        "summary": {
            "runs_scanned": len(runs),
            "runs_with_failures": runs_with_failures,
            "runs_clean": skipped,
            "infra_failures": infra_failures,
            "window_start": runs[-1]["createdAt"],
            "window_end": runs[0]["createdAt"],
            "cross_pr_failure_count": len(failures),
        },
        "failures": failures,
    }
    return json.dumps(result, indent=2)


def analyze(
    runs: list[dict],
    min_count: int = 2,
    verbose: bool = False,
) -> str:
    """Analyze failures across runs and produce a markdown report."""
    lines: list[str] = []

    if not runs:
        return "No runs found in the given time window.\n"

    case_data, case_branches, runs_with_failures, skipped, infra_failures = (
        _collect_failure_data(runs, verbose)
    )

    # Filter: only keep tests that failed across 2+ distinct branches
    cross_pr_cases = {
        case_id: channels
        for case_id, channels in case_data.items()
        if len(case_branches[case_id]) >= 2
    }

    # Also collect single-branch failures for the appendix
    single_pr_cases = {
        case_id: channels
        for case_id, channels in case_data.items()
        if len(case_branches[case_id]) < 2
    }

    sorted_cases = sorted(
        cross_pr_cases.items(),
        key=lambda x: _total_count(x[1]),
        reverse=True,
    )

    # Report header
    lines.append("# CI Test Failure Analysis\n")
    lines.append(f"**Runs scanned**: {len(runs)} ({runs_with_failures} with test failures, "
                 f"{skipped} clean, {infra_failures} infra-only failures)\n")
    lines.append(f"**Window**: {runs[-1]['createdAt'][:19]}Z to {runs[0]['createdAt'][:19]}Z\n")
    lines.append(f"**Cross-PR failures**: {len(cross_pr_cases)} unique test cases\n")
    lines.append(f"**Single-PR failures**: {len(single_pr_cases)} unique test cases (excluded from main table)\n")

    if not sorted_cases:
        lines.append("\nNo cross-PR test failures found.\n")
        return "\n".join(lines)

    # Main table
    lines.append("\n## Cross-PR Failures (systemic)\n")
    lines.append("| Count | Test Case | email | chat | sms | voice | other | Branches |")
    lines.append("|------:|-----------|------:|-----:|----:|------:|------:|----------|")

    for case_id, channels in sorted_cases:
        count = _total_count(channels)
        if count < min_count:
            continue
        email = len(channels.get("email", []))
        chat = len(channels.get("chat", []))
        sms = len(channels.get("sms", []))
        voice = len(channels.get("voice", []))
        other = len(channels.get("other", []))
        branches = ", ".join(sorted(case_branches[case_id]))
        e = str(email) if email else "-"
        c = str(chat) if chat else "-"
        s = str(sms) if sms else "-"
        v = str(voice) if voice else "-"
        o = str(other) if other else "-"
        lines.append(f"| **{count}** | `{case_id}` | {e} | {c} | {s} | {v} | {o} | {branches} |")

    # Single-occurrence cross-PR failures
    low_freq = [
        (case_id, _total_count(channels))
        for case_id, channels in sorted_cases
        if _total_count(channels) < min_count
    ]
    if low_freq:
        lines.append(f"\n**Below threshold ({min_count})**: "
                     + ", ".join(f"`{c}` ({n})" for c, n in low_freq))

    # Single-PR appendix
    if single_pr_cases:
        lines.append("\n## Single-PR Failures (likely PR-specific, not systemic)\n")
        sorted_single = sorted(
            single_pr_cases.items(),
            key=lambda x: _total_count(x[1]),
            reverse=True,
        )
        for case_id, channels in sorted_single[:20]:
            count = _total_count(channels)
            branch = next(iter(case_branches[case_id]))
            lines.append(f"- `{case_id}` ({count}x on `{branch}`)")
        if len(sorted_single) > 20:
            lines.append(f"- ... and {len(sorted_single) - 20} more")

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze test failures across CI runs to find systemic flakiness.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--hours", type=int, default=None,
        help="Lookback period in hours (default: 24)",
    )
    group.add_argument(
        "--since", type=str, default=None,
        help="Start date (ISO format, e.g. 2026-03-30 or 2026-03-30T17:00:00Z)",
    )
    group.add_argument(
        "--since-pr", type=int, default=None,
        help="Start from when a specific PR was merged",
    )
    parser.add_argument(
        "--workflow", type=str, default="ci.yml",
        help="Workflow file to scan (default: ci.yml)",
    )
    parser.add_argument(
        "--min-count", type=int, default=2,
        help="Minimum failure count to include in main table (default: 2)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Write report to file instead of stdout",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output structured JSON instead of markdown",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print progress to stderr",
    )
    args = parser.parse_args()

    # Determine start time
    if args.since_pr:
        print(f"Looking up merge time for PR #{args.since_pr}...", file=sys.stderr)
        merge_time = get_pr_merge_time(args.since_pr)
        if not merge_time:
            print(f"Error: PR #{args.since_pr} has not been merged.", file=sys.stderr)
            sys.exit(1)
        since = merge_time
        print(f"PR #{args.since_pr} merged at {since}", file=sys.stderr)
    elif args.since:
        since = args.since
        if "T" not in since:
            since += "T00:00:00Z"
    else:
        hours = args.hours or 24
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        since = cutoff.isoformat()

    print(f"Fetching runs since {since}...", file=sys.stderr)
    runs = get_runs(since, args.workflow)
    print(f"Found {len(runs)} runs.", file=sys.stderr)

    if not runs:
        print("No runs found.", file=sys.stderr)
        sys.exit(0)

    print("Scanning logs for test failures (this may take a few minutes)...", file=sys.stderr)
    if args.json_output:
        report = analyze_json(runs, min_count=args.min_count, verbose=args.verbose)
    else:
        report = analyze(runs, min_count=args.min_count, verbose=args.verbose)

    if args.output:
        with open(args.output, "w") as f:
            f.write(report)
        print(f"Report written to {args.output}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
