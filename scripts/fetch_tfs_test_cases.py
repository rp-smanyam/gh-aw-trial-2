#!/usr/bin/env python3
"""
TFS Test Case Fetcher

Fetches TFS test case work items, parses their step XML, and extracts
Q&A pairs relevant to the AI agent chatbot.

Usage:
    # Discover and fetch all test cases from known suites
    uv run scripts/fetch_tfs_test_cases.py --all

    # Fetch all test cases in a specific suite
    uv run scripts/fetch_tfs_test_cases.py --suite 2368561 --plan 273269

    # Fetch specific test cases by ID
    uv run scripts/fetch_tfs_test_cases.py 2386781 2386786 2386789

    # Output as markdown table
    uv run scripts/fetch_tfs_test_cases.py --all --format markdown

    # Output as JSON
    uv run scripts/fetch_tfs_test_cases.py --all --format json

    # Use cached data (skip API call)
    uv run scripts/fetch_tfs_test_cases.py --all --cached

    # Regenerate docs/SANDBOX_TEST_CASES.md from TFS (or cache)
    uv run scripts/fetch_tfs_test_cases.py --update-docs
    uv run scripts/fetch_tfs_test_cases.py --update-docs --cached

Requires TFS_PAT_TOKEN environment variable (PAT with Work Items Read + Test Management Read scopes).
"""

import argparse
import base64
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional  # noqa: UP035

from dotenv import load_dotenv

# Load .env from the project root (parent of scripts/)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Try to import httpx, fall back to urllib if not available
try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    import urllib.error
    import urllib.request

# Try to import rich for colored output, fall back to plain text
try:
    from rich import print as rprint
    from rich.console import Console

    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None

TFS_BASE_URL = "https://tfs.realpage.com/tfs/Realpage"

CACHE_DIR = Path(__file__).resolve().parent.parent / ".tfs_cache"
DOCS_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "SANDBOX_TEST_CASES.md"

# Known test suites used by QA for the Resident AI chat
KNOWN_SUITES: list[dict] = [
    {
        "suite_id": 2368561,
        "plan_id": 273269,
        "project": "Consumer Solutions",
        "name": "Resident Chat (English)",
        "source": "Issue 11 comment (Aneesh)",
    },
]

# Facilities tests — suite unknown, fall back to explicit IDs
KNOWN_EXPLICIT_IDS: list[dict] = [
    {
        "ids": [2492184, 2492185, 2492186, 2492187],
        "name": "Facilities (Email)",
        "source": "Issue 11 comment (Aneesh) — suite unknown",
    },
]

# High-priority patterns — if these match, the step is always treated as agent Q&A
# (even if skip patterns also match, e.g. "Go to your email and ask a question like ...")
STRONG_AGENT_PATTERNS = [
    r'ask\s+a\s+question\s+like\s+"',  # ask a question like "..."
    r"ask\s+a\s+question\s+like\s+\u201c",  # smart quotes variant
    r'send\s+an?\s+email.*ask\s+.*"',  # send an email...ask..."..."
    r'(type|enter|send|ask)\b.*"[^"]{10,}"',  # action verb + quoted question
]

# Keywords indicating a step is about asking the chatbot a question
AGENT_QUESTION_PATTERNS = [
    r"type\b.*\b(question|message|chat|text)",
    r"ask\b",
    r"send\b.*\b(message|question|text)",
    r"enter\b.*\b(question|message|text|query)",
    r"input\b.*\b(question|message|text)",
    r"in\s+the\s+(chat|message|text)\s*(box|field|area|input)",
    r'"[^"]{10,}"',  # quoted text that looks like a question
    r"\u201c[^\u201d]{10,}\u201d",  # smart-quoted text
]

# Keywords indicating a step is about UI navigation (not agent Q&A)
SKIP_PATTERNS = [
    r"^(open|navigate|go to|browse|launch|click|tap|select|log\s*in|sign\s*in|log\s*out|close|minimize|maximize)\b",
    r"\b(browser|url|http|www\.)\b",
    r"\b(menu|button|tab|dropdown|sidebar|toolbar|header|footer|modal|dialog|popup)\b",
    r"\b(refresh|reload|scroll|hover)\b",
    r"^verify\b.*\b(displayed|visible|shown|appears|loaded)\b",
    r"^check\b.*\b(displayed|visible|shown|appears|loaded)\b",
    r"^wait\b",
]


def make_auth_header(pat_token: str) -> dict[str, str]:
    """Create Basic auth header from PAT token."""
    encoded = base64.b64encode(f":{pat_token}".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


def http_get_json(
    url: str, headers: dict[str, str], timeout: int = 30
) -> tuple[Optional[dict | list], Optional[str]]:
    """Make an HTTP GET request and return (json_body, error)."""
    if HTTPX_AVAILABLE:
        try:
            with httpx.Client(timeout=timeout, verify=True) as client:
                response = client.get(url, headers=headers)
                if response.status_code != 200:
                    return None, f"HTTP {response.status_code}: {response.text[:200]}"
                return response.json(), None
        except httpx.TimeoutException:
            return None, "Request timed out"
        except httpx.RequestError as e:
            return None, str(e)
    else:
        try:
            req = urllib.request.Request(url, headers={**headers, "User-Agent": "fetch_tfs/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body), None
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            return None, f"HTTP {e.code}: {body}"
        except urllib.error.URLError as e:
            return None, str(e.reason)
        except Exception as e:
            return None, str(e)


def discover_test_case_ids(
    plan_id: int, suite_id: int, pat_token: str, project: str = "Consumer Solutions", verbose: bool = False
) -> list[int]:
    """Fetch test case IDs from a TFS test suite."""
    project_encoded = urllib.parse.quote(project)
    url = (
        f"{TFS_BASE_URL}/{project_encoded}/_apis/test/Plans/{plan_id}"
        f"/suites/{suite_id}/testcases?api-version=5.0"
    )
    if verbose:
        _print_dim(f"  Fetching suite {suite_id} from plan {plan_id}...")
        _print_dim(f"  URL: {url}")

    headers = make_auth_header(pat_token)
    data, error = http_get_json(url, headers)

    if error:
        _print_error(f"Failed to fetch suite {suite_id}: {error}")
        return []

    if not isinstance(data, dict) or "value" not in data:
        _print_error(f"Unexpected response format for suite {suite_id}")
        if verbose and data:
            _print_dim(f"  Response: {json.dumps(data)[:200]}")
        return []

    ids = []
    for item in data["value"]:
        # The testCase field contains the work item reference
        test_case = item.get("testCase", {})
        tc_id = test_case.get("id")
        if tc_id:
            ids.append(int(tc_id))

    if verbose:
        _print_dim(f"  Found {len(ids)} test cases: {ids}")

    return ids


def fetch_work_items(
    ids: list[int], pat_token: str, verbose: bool = False
) -> list[dict]:
    """Batch fetch work item details from TFS."""
    if not ids:
        return []

    # TFS API supports up to 200 IDs per request
    all_items = []
    for batch_start in range(0, len(ids), 200):
        batch_ids = ids[batch_start : batch_start + 200]
        ids_str = ",".join(str(i) for i in batch_ids)
        url = (
            f"{TFS_BASE_URL}/_apis/wit/workitems"
            f"?ids={ids_str}&$expand=all&api-version=5.0"
        )

        if verbose:
            _print_dim(f"  Fetching {len(batch_ids)} work items...")

        headers = make_auth_header(pat_token)
        data, error = http_get_json(url, headers)

        if error:
            _print_error(f"Failed to fetch work items: {error}")
            continue

        if isinstance(data, dict) and "value" in data:
            all_items.extend(data["value"])
        elif isinstance(data, list):
            all_items.extend(data)

    return all_items


def strip_html(text: str) -> str:
    """Remove HTML tags and decode common entities."""
    if not text:
        return ""
    # Remove HTML tags
    cleaned = re.sub(r"<[^>]+>", "", text)
    # Decode common HTML entities
    cleaned = cleaned.replace("&amp;", "&")
    cleaned = cleaned.replace("&lt;", "<")
    cleaned = cleaned.replace("&gt;", ">")
    cleaned = cleaned.replace("&quot;", '"')
    cleaned = cleaned.replace("&#39;", "'")
    cleaned = cleaned.replace("&nbsp;", " ")
    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def parse_test_steps(steps_xml: str) -> list[dict]:
    """Parse TFS test step XML into a list of {action, expected_result, step_id}."""
    if not steps_xml:
        return []

    try:
        root = ET.fromstring(steps_xml)
    except ET.ParseError:
        return []

    steps = []
    for step_elem in root.findall(".//step"):
        step_id = step_elem.get("id", "")
        params = step_elem.findall("parameterizedString")

        action = ""
        expected_result = ""
        if len(params) >= 1:
            action = strip_html(params[0].text or "")
        if len(params) >= 2:
            expected_result = strip_html(params[1].text or "")

        if action:
            steps.append(
                {
                    "step_id": step_id,
                    "action": action,
                    "expected_result": expected_result,
                }
            )

    return steps


def is_agent_question(action: str) -> bool:
    """Determine if a step action describes asking the chatbot a question."""
    action_lower = action.lower()

    # Strong patterns override skip patterns (e.g. "Go to email and ask a question like ...")
    for pattern in STRONG_AGENT_PATTERNS:
        if re.search(pattern, action_lower):
            return True

    # Check if it matches skip patterns (UI navigation)
    for pattern in SKIP_PATTERNS:
        if re.search(pattern, action_lower):
            return False

    # Check if it matches agent question patterns
    for pattern in AGENT_QUESTION_PATTERNS:
        if re.search(pattern, action_lower):
            return True

    return False


def extract_agent_qa(steps: list[dict]) -> list[dict]:
    """Filter test steps for agent-relevant Q&A pairs.

    Returns steps that look like questions typed into the chatbot.
    For ambiguous steps, includes them with a note.
    """
    qa_pairs = []
    for step in steps:
        action = step["action"]
        if is_agent_question(action):
            qa_pairs.append(
                {
                    "question": action,
                    "expected_result": step.get("expected_result", ""),
                    "step_id": step.get("step_id", ""),
                }
            )
    return qa_pairs


def load_cached(ids: list[int]) -> Optional[list[dict]]:
    """Load cached work item responses from .tfs_cache/."""
    if not CACHE_DIR.exists():
        return None

    items = []
    missing = []
    for wid in ids:
        cache_file = CACHE_DIR / f"{wid}.json"
        if cache_file.exists():
            with open(cache_file) as f:
                items.append(json.load(f))
        else:
            missing.append(wid)

    if missing:
        return None

    return items


def save_cache(work_items: list[dict]) -> None:
    """Save raw work item responses to .tfs_cache/."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for item in work_items:
        wid = item.get("id")
        if wid:
            cache_file = CACHE_DIR / f"{wid}.json"
            with open(cache_file, "w") as f:
                json.dump(item, f, indent=2)


def _print_dim(text: str) -> None:
    if RICH_AVAILABLE:
        rprint(f"[dim]{text}[/dim]")
    else:
        print(text)


def _print_error(text: str) -> None:
    if RICH_AVAILABLE:
        rprint(f"[red]{text}[/red]")
    else:
        print(f"ERROR: {text}", file=sys.stderr)


def process_work_items(work_items: list[dict], verbose: bool = False) -> list[dict]:
    """Process work items and extract Q&A pairs.

    Returns a list of dicts with test case info and extracted Q&A.
    """
    results = []
    for item in work_items:
        wid = item.get("id", "?")
        fields = item.get("fields", {})
        title = fields.get("System.Title", "Untitled")
        steps_xml = fields.get("Microsoft.VSTS.TCM.Steps", "")
        tfs_url = item.get("_links", {}).get("html", {}).get("href", "")

        steps = parse_test_steps(steps_xml)
        qa_pairs = extract_agent_qa(steps)

        if verbose:
            _print_dim(f"  TC {wid}: {title} — {len(steps)} steps, {len(qa_pairs)} Q&A pairs")

        results.append(
            {
                "id": wid,
                "title": title,
                "tfs_url": tfs_url,
                "total_steps": len(steps),
                "qa_pairs": qa_pairs,
                "all_steps": steps if verbose else [],
            }
        )

    return results


def format_human(results: list[dict], verbose: bool = False) -> str:
    """Format results as human-readable text."""
    lines = []
    for tc in results:
        lines.append(f"\n{'='*60}")
        lines.append(f"Test Case {tc['id']}: {tc['title']}")
        lines.append(f"  Total steps: {tc['total_steps']}, Agent Q&A: {len(tc['qa_pairs'])}")

        if tc["qa_pairs"]:
            for i, qa in enumerate(tc["qa_pairs"], 1):
                lines.append(f"\n  Q{i}: {qa['question']}")
                if qa["expected_result"]:
                    lines.append(f"  A{i}: {qa['expected_result']}")
        else:
            lines.append("  (no agent Q&A pairs found)")

        if verbose and tc.get("all_steps"):
            lines.append("\n  --- All steps ---")
            for step in tc["all_steps"]:
                marker = "*" if is_agent_question(step["action"]) else " "
                lines.append(f"  [{marker}] Step {step['step_id']}: {step['action']}")
                if step["expected_result"]:
                    lines.append(f"      Expected: {step['expected_result']}")

    return "\n".join(lines)


def format_markdown(results: list[dict]) -> str:
    """Format results as markdown tables grouped by test case."""
    lines = []
    for tc in results:
        lines.append(f"\n#### TC {tc['id']}: {tc['title']}")
        lines.append("")

        if tc["qa_pairs"]:
            lines.append("| TFS ID | Test Question | Expected Behavior |")
            lines.append("|--------|--------------|-------------------|")
            for qa in tc["qa_pairs"]:
                q = qa["question"].replace("|", "\\|")
                a = qa["expected_result"].replace("|", "\\|") if qa["expected_result"] else "—"
                lines.append(f"| {tc['id']} | {q} | {a} |")
        else:
            lines.append("_(no agent Q&A pairs extracted)_")

        lines.append("")

    return "\n".join(lines)


def format_json(results: list[dict]) -> str:
    """Format results as JSON."""
    return json.dumps(results, indent=2)


FACILITIES_IDS = {2492184, 2492185, 2492186, 2492187}

CATEGORY_ORDER = [
    "OneSite",
    "Facilities",
    "Policy / General Knowledge",
    "Handoff (Knock-dependent — mocked in sandbox)",
    "Module Configuration (Knock-dependent — mocked in sandbox)",
]


def categorize_test_case(title: str, tc_id: int) -> tuple[str, bool]:
    """Return (category, is_spanish)."""
    is_spanish = "spanish" in title.lower()
    t = title.lower()
    if tc_id in FACILITIES_IDS or "email" in t or "sr " in t or "service request" in t:
        return "Facilities", is_spanish
    if "handoff" in t:
        return "Handoff (Knock-dependent — mocked in sandbox)", is_spanish
    if "enabled" in t or "disabled" in t or "module" in t:
        return "Module Configuration (Knock-dependent — mocked in sandbox)", is_spanish
    if "policy" in t or "amenties" in t or "parking" in t or "packages" in t:
        return "Policy / General Knowledge", is_spanish
    return "OneSite", is_spanish


def _escape_md(text: str) -> str:
    """Escape pipe characters for markdown table cells."""
    return text.replace("|", "\\|")


def format_docs_markdown(results: list[dict]) -> str:
    """Generate the full docs/SANDBOX_TEST_CASES.md content."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Group by (is_spanish, category)
    english: dict[str, list[dict]] = defaultdict(list)
    spanish: dict[str, list[dict]] = defaultdict(list)

    for tc in results:
        category, is_spanish = categorize_test_case(tc["title"], tc["id"])
        bucket = spanish if is_spanish else english
        bucket[category].append(tc)

    lines = [
        "<!-- Auto-generated by scripts/fetch_tfs_test_cases.py — do not edit manually -->",
        f"<!-- Regenerate: uv run scripts/fetch_tfs_test_cases.py --update-docs -->",
        f"<!-- Last updated: {today} -->",
        "",
        "# Sandbox Test Cases",
        "",
        "Test questions extracted from TFS test cases used by QA for the Resident AI chat.",
        "Source: [Suite 2368561](https://tfs.realpage.com/tfs/Realpage/Consumer%20Solutions/"
        "_testManagement?planId=273269&suiteId=2368561) (plan 273269) + Facilities email tests.",
        "",
        "To regenerate this file from TFS:",
        "",
        "```bash",
        "uv run scripts/fetch_tfs_test_cases.py --update-docs          # fetch from TFS API",
        "uv run scripts/fetch_tfs_test_cases.py --update-docs --cached  # use local cache",
        "```",
        "",
        "Requires `TFS_PAT_TOKEN` — see [.sample.env](../.sample.env).",
        "",
    ]

    def _render_section(section_name: str, groups: dict[str, list[dict]]) -> None:
        lines.append(f"## {section_name}")
        lines.append("")
        has_content = False
        for category in CATEGORY_ORDER:
            tcs = groups.get(category, [])
            if not tcs:
                continue
            has_content = True
            lines.append(f"### {category}")
            lines.append("")
            lines.append("| TFS Test Case | Question | Expected Behavior |")
            lines.append("|---------------|----------|-------------------|")
            for tc in tcs:
                tc_label = f"{tc['id']} — {tc['title']}"
                tc_link = f"[{_escape_md(tc_label)}]({tc['tfs_url']})" if tc["tfs_url"] else str(tc["id"])
                if tc["qa_pairs"]:
                    for qa in tc["qa_pairs"]:
                        q = _escape_md(qa["question"])
                        a = _escape_md(qa["expected_result"]) if qa["expected_result"] else "—"
                        lines.append(f"| {tc_link} | {q} | {a} |")
                else:
                    lines.append(f"| {tc_link} | _(no agent Q&A extracted)_ | — |")
            lines.append("")
        if not has_content:
            lines.append("_(no test cases in this section)_")
            lines.append("")

    _render_section("English", english)
    _render_section("Spanish", spanish)

    return "\n".join(lines)


def collect_all_ids(pat_token: str, verbose: bool = False) -> list[int]:
    """Collect all test case IDs from known suites and explicit IDs."""
    all_ids = []

    for suite in KNOWN_SUITES:
        if verbose:
            _print_dim(f"Discovering suite: {suite['name']} (suite {suite['suite_id']})")
        ids = discover_test_case_ids(
            suite["plan_id"], suite["suite_id"], pat_token,
            project=suite.get("project", "Consumer Solutions"), verbose=verbose,
        )
        all_ids.extend(ids)

    for group in KNOWN_EXPLICIT_IDS:
        if verbose:
            _print_dim(f"Adding explicit IDs: {group['name']} — {group['ids']}")
        all_ids.extend(group["ids"])

    # Deduplicate while preserving order
    seen = set()
    unique_ids = []
    for i in all_ids:
        if i not in seen:
            seen.add(i)
            unique_ids.append(i)

    return unique_ids


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch TFS test case work items and extract agent Q&A pairs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run scripts/fetch_tfs_test_cases.py --all
  uv run scripts/fetch_tfs_test_cases.py --suite 2368561 --plan 273269
  uv run scripts/fetch_tfs_test_cases.py 2386781 2386786 2386789
  uv run scripts/fetch_tfs_test_cases.py --all --format markdown
  uv run scripts/fetch_tfs_test_cases.py --all --cached
  uv run scripts/fetch_tfs_test_cases.py --update-docs
  uv run scripts/fetch_tfs_test_cases.py --update-docs --cached
        """,
    )
    parser.add_argument(
        "ids",
        nargs="*",
        type=int,
        help="Specific test case work item IDs to fetch",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Fetch all test cases from known suites",
    )
    parser.add_argument(
        "--suite",
        type=int,
        help="TFS test suite ID to discover test cases from",
    )
    parser.add_argument(
        "--plan",
        type=int,
        help="TFS test plan ID (required with --suite)",
    )
    parser.add_argument(
        "--project",
        default="Consumer Solutions",
        help="TFS project name (default: Consumer Solutions)",
    )
    parser.add_argument(
        "--format",
        choices=["human", "markdown", "json"],
        default="human",
        help="Output format (default: human)",
    )
    parser.add_argument(
        "--cached",
        action="store_true",
        help="Use cached data (skip API calls)",
    )
    parser.add_argument(
        "--update-docs",
        action="store_true",
        help="Regenerate docs/SANDBOX_TEST_CASES.md with all test cases",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output including all steps",
    )

    args = parser.parse_args()

    # --update-docs implies --all
    if args.update_docs:
        args.all = True

    # Validate arguments
    if not args.all and not args.ids and not args.suite:
        parser.error("Provide --all, --suite, or specific test case IDs")

    if args.suite and not args.plan:
        parser.error("--plan is required when using --suite")

    # Get PAT token
    pat_token = os.environ.get("TFS_PAT_TOKEN", "")
    if not pat_token and not args.cached:
        _print_error(
            "TFS_PAT_TOKEN environment variable not set.\n"
            "Create a PAT at https://tfs.realpage.com/tfs/Realpage/_details/security/tokens\n"
            "Required scopes: Work Items (Read), Test Management (Read)"
        )
        return 1

    # Collect test case IDs
    ids: list[int] = []

    if args.all:
        if args.cached:
            # In cached mode, load whatever is in the cache dir
            if CACHE_DIR.exists():
                ids = [
                    int(f.stem)
                    for f in CACHE_DIR.glob("*.json")
                    if f.stem.isdigit()
                ]
                ids.sort()
                if not ids:
                    _print_error("No cached data found")
                    return 1
                _print_dim(f"Found {len(ids)} cached work items")
            else:
                _print_error(f"Cache directory not found: {CACHE_DIR}")
                return 1
        else:
            ids = collect_all_ids(pat_token, verbose=args.verbose)
    elif args.suite:
        ids = discover_test_case_ids(args.plan, args.suite, pat_token, project=args.project, verbose=args.verbose)
    elif args.ids:
        ids = args.ids

    if not ids:
        _print_error("No test case IDs to fetch")
        return 1

    if RICH_AVAILABLE:
        rprint(f"[bold]Fetching {len(ids)} test cases...[/bold]")
    else:
        print(f"Fetching {len(ids)} test cases...")

    # Fetch work items (from cache or API)
    work_items: list[dict] = []

    if args.cached:
        cached = load_cached(ids)
        if cached:
            work_items = cached
            _print_dim("Loaded from cache")
        else:
            _print_error("Cache incomplete — some work items not cached. Run without --cached first.")
            return 1
    else:
        work_items = fetch_work_items(ids, pat_token, verbose=args.verbose)
        if work_items:
            save_cache(work_items)
            _print_dim(f"Cached {len(work_items)} work items to {CACHE_DIR}")

    if not work_items:
        _print_error("No work items retrieved")
        return 1

    # Process and output
    results = process_work_items(work_items, verbose=args.verbose)

    if args.update_docs:
        docs_content = format_docs_markdown(results)
        DOCS_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        DOCS_OUTPUT_PATH.write_text(docs_content)
        if RICH_AVAILABLE:
            rprint(f"[green]Wrote {DOCS_OUTPUT_PATH}[/green]")
        else:
            print(f"Wrote {DOCS_OUTPUT_PATH}")
    elif args.format == "markdown":
        print(format_markdown(results))
    elif args.format == "json":
        print(format_json(results))
    else:
        print(format_human(results, verbose=args.verbose))

    # Summary
    total_qa = sum(len(tc["qa_pairs"]) for tc in results)
    if RICH_AVAILABLE:
        rprint(f"\n[bold]Summary:[/bold] {len(results)} test cases, {total_qa} agent Q&A pairs extracted")
    else:
        print(f"\nSummary: {len(results)} test cases, {total_qa} agent Q&A pairs extracted")

    return 0


if __name__ == "__main__":
    sys.exit(main())
