"""
Count how often each test has failed over a given time period using GitHub Actions logs.

Examples:
    - `uv run python scripts/test_failure_frequency.py`
    - `uv run python scripts/test_failure_frequency.py --hours 48 --workflow ci.yml`
"""

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone


def get_failed_runs(hours: int, workflow: str | None) -> list[dict]:
    """Fetch failed workflow runs within the lookback window."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    cmd = [
        "gh", "run", "list",
        "--status=failure",
        "--json=databaseId,createdAt",
        "--limit=200",
    ]
    if workflow:
        cmd.extend(["--workflow", workflow])

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    runs = json.loads(result.stdout)

    return [
        run for run in runs
        if datetime.fromisoformat(run["createdAt"]) >= cutoff
    ]


def get_failed_tests(run_id: int) -> list[str]:
    """Extract FAILED test identifiers from a run's failed logs."""
    result = subprocess.run(
        ["gh", "run", "view", str(run_id), "--log-failed"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []

    failed_tests = []
    for line in result.stdout.splitlines():
        match = re.search(r"FAILED\s+(tests/.+?)(?:\s+-\s|\s*$)", line)
        if match:
            failed_tests.append(match.group(1))
    return failed_tests


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Count test failure frequency from GitHub Actions."
    )
    parser.add_argument(
        "--hours", type=int, default=24,
        help="Lookback period in hours (default: 24)",
    )
    parser.add_argument(
        "--workflow", type=str, default=None,
        help="Filter to a specific workflow file (e.g. tests.yml)",
    )
    args = parser.parse_args()

    print(f"Fetching failed runs from the last {args.hours} hours...")
    runs = get_failed_runs(args.hours, args.workflow)
    if not runs:
        print("No failed runs found in the given time window.")
        sys.exit(0)

    print(f"Found {len(runs)} failed run(s). Scanning logs...")
    counter: Counter[str] = Counter()
    for run in runs:
        failed_tests = get_failed_tests(run["databaseId"])
        counter.update(failed_tests)

    if not counter:
        print("No FAILED test lines found in the logs.")
        sys.exit(0)

    print()
    print(f"Test Failure Frequency (last {args.hours} hours)")
    print("=" * 50)
    max_name_len = max(len(name) for name in counter)
    for name, count in counter.most_common():
        print(f"{name:<{max_name_len}}  {count}")


if __name__ == "__main__":
    main()
