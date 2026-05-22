#!/usr/bin/env python3
"""
Generate release artifacts (release matrix, cherry-pick command, summary) from the payload JSON.

Local debugging:
  `python scripts/release_tools/build_report.py --payload /tmp/release_payload.json \
     --matrix /tmp/ticket-matrix.md --command /tmp/cherry.txt`
"""
from __future__ import annotations

import argparse
import html
import json
import os
from collections import OrderedDict
from typing import Any, Dict, List


def sanitize(text: str | None) -> str:
    if not text:
        return ""
    # First escape HTML entities, then escape markdown pipes
    return html.escape(text).replace("|", "\\|").replace("\n", " ")


def ensure_parent(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def render_matrix(issues: List[Dict[str, Any]], current_repo: str | None = None) -> List[str]:
    """Generate a markdown table of issues sorted by merge date."""
    # Collect all rows (one per commit, or one per issue if no commits)
    all_rows = []
    for issue in issues:
        issue_rows = _create_commit_rows_for_issue(issue)
        all_rows.extend(issue_rows)
    
    # Sort by merged date (earliest first), incomplete tickets (no date) at bottom
    def sort_key(row):
        return (row["merged_at"] or "9999-99-99", row["issue_key"])
    
    all_rows.sort(key=sort_key)
    
    # Generate markdown table
    lines = [
        "| Ticket | Repository | Summary | Status | Date | Pull Request |",
        "| -------- | ------- | ------ | ---------------- | ------------ | ---------- |",
    ]
    
    for row in all_rows:
        pr_info = _format_pr_info(row)
        repo_info = row.get('pr_repo', ' - ')
        
        # Apply gray styling to entire row content for external repos
        is_external = current_repo and repo_info != current_repo
        
        def style_row(text: str) -> str:
            if not is_external:
                return f"**{text}**"
            return text
        
        ticket = f"[{row['issue_key']}]({row['issue_url']})"
        repo_info = style_row(repo_info)
        summary = row['issue_summary']
        status = row['issue_status']
        date = row['merged_date']
        pr_info = pr_info
        
        lines.append(
            f"| {ticket} | {repo_info} | {summary} | {status} | {date} | {pr_info} |"
        )  
    return lines


def _create_commit_rows_for_issue(issue: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Create row data for all commits in an issue's PRs."""
    rows = []
    issue_key = issue.get("key", "")
    issue_summary = sanitize(issue.get("summary") or "")
    issue_status = sanitize(issue.get("status") or "")
    issue_url = issue.get("url", "")
    
    prs = issue.get("pull_requests", [])
    if not prs:
        # Issue has no PRs - create one row with empty PR info
        return [{
            "issue_key": issue_key,
            "issue_summary": issue_summary,
            "issue_status": issue_status,
            "issue_url": issue_url,
            "pr_repo": " - ",
            "pr_number": "",
            "pr_url": "",
            "pr_author": "",
            "base_ref": "",
            "head_ref": "",
            "merged_date": "",
            "merged_at": "",
            "sha": "",
            "message": "",
        }]
    
    # Issue has PRs - create rows for each PR/commit
    for pr in prs:
        pr_rows = _create_rows_for_pr(pr, issue_key, issue_summary, issue_status, issue_url)
        rows.extend(pr_rows)
    
    return rows


def _create_rows_for_pr(pr: Dict[str, Any], issue_key: str, issue_summary: str, 
                       issue_status: str, issue_url: str) -> List[Dict[str, Any]]:
    """Create row data for all commits in a PR."""
    rows = []
    pr_repo = pr.get("repo", "")
    pr_number = pr.get("number", "")
    pr_url = pr.get("url", "")
    pr_author = pr.get("author", "")
    base_ref = pr.get("base_ref", "")
    head_ref = pr.get("head_ref", "")
    merged_at = pr.get("merged_at", "")
    
    commits = pr.get("commits", [])
    if not commits:
        # PR exists but no commits found
        merged_date = merged_at if merged_at else ""
        return [{
            "issue_key": issue_key,
            "issue_summary": issue_summary,
            "issue_status": issue_status,
            "issue_url": issue_url,
            "pr_repo": pr_repo,
            "pr_number": pr_number,
            "pr_url": pr_url,
            "pr_author": pr_author,
            "base_ref": base_ref,
            "head_ref": head_ref,
            "merged_date": merged_date,
            "merged_at": merged_at,
            "sha": "",
            "message": "No commits discovered",
        }]
    
    # PR has commits - create one row per commit
    for commit in commits:
        sha = commit.get("sha", "")
        message = sanitize(commit.get("message") or "")
        merged_date = merged_at if merged_at else ""
        
        rows.append({
            "issue_key": issue_key,
            "issue_summary": issue_summary,
            "issue_status": issue_status,
            "issue_url": issue_url,
            "pr_repo": pr_repo,
            "pr_number": pr_number,
            "pr_url": pr_url,
            "pr_author": pr_author,
            "base_ref": base_ref,
            "head_ref": head_ref,
            "merged_date": merged_date,
            "merged_at": merged_at,
            "sha": sha,
            "message": message,
        })
    
    return rows


def _format_pr_info(row: Dict[str, Any]) -> str:
    """Format PR information for display in the table."""
    if not row["pr_number"]:
        return "—"
    
    return (
        f"[#{row['pr_number']} ({row['pr_repo']})]({row['pr_url']}) by @{row['pr_author']}<br>"
        f"Base: `{row['base_ref']}` → Head: `{row['head_ref']}`<br>"
        f"`{row['sha'][:7]}` {row['message']}"
    )


def _format_branch_setup(meta: Dict[str, Any], shas: List[str]) -> str:
    parts = [
        "```bash",
        f"git checkout {meta['base_release']}",
        f"git pull origin {meta['base_release']}",
        f"git checkout -b {meta['target_release']} {meta['base_release']}",
        "```",
    ]
    if shas:
        parts.insert(-1, f"git cherry-pick {' '.join(shas)}")
    return "\n".join(parts)


def _format_additional_repo(repo: str | None, shas: List[str]) -> str:
    repo_label = repo or "unknown-repo"
    return "\n".join(
        [
            f"### {repo_label}",
            "```bash",
            f"git cherry-pick {' '.join(shas)}",
            "```",
        ]
    )


def build_cherry_pick(meta: Dict[str, Any], ordered_commits: List[Dict[str, Any]], current_repo: str | None = None) -> str:
    if not ordered_commits:
        return "# No commits discovered for cherry-picking"

    if not current_repo:
        cherry_pick_parts = [c["sha"] for c in ordered_commits if c.get("sha")]
        return _format_branch_setup(meta, cherry_pick_parts)

    repo_to_shas: Dict[str | None, List[str]] = OrderedDict()
    for commit in ordered_commits:
        sha = commit.get("sha")
        if not sha:
            continue
        repo_key = commit.get("repo")
        repo_to_shas.setdefault(repo_key, []).append(sha)

    has_primary_commits = any(c.get("repo") == current_repo for c in ordered_commits)
    lines: List[str] = []

    if has_primary_commits:
        primary_shas = repo_to_shas.get(current_repo, [])
        lines.append(_format_branch_setup(meta, primary_shas))
    else:
        lines.append(f"# No commits found for repository {current_repo}")

    extras = [
        _format_additional_repo(repo, shas)
        for repo, shas in repo_to_shas.items()
        if repo != current_repo and shas
    ]
    if extras:
        lines.append("## Additional repositories")
        lines.extend(extras)

    return "\n\n".join(line for line in lines if line).strip()


def write_file(path: str, content: str) -> None:
    ensure_parent(path)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True, help="Path to release_payload.json")
    parser.add_argument("--matrix", required=True, help="Output path for ticket-matrix.md")
    parser.add_argument("--command", required=True, help="Output path for cherry-pick command text")
    parser.add_argument(
        "--summary",
        default=os.environ.get("GITHUB_STEP_SUMMARY"),
        help="Optional path to write the Markdown summary (defaults to $GITHUB_STEP_SUMMARY if set)",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.payload):
        raise SystemExit(f"Payload file not found: {args.payload}")

    try:
        with open(args.payload, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (IOError, json.JSONDecodeError) as e:
        raise SystemExit(f"Failed to read payload file: {e}")

    # Validate payload structure
    required_keys = ["issues", "ordered_commits", "fix_version", "base_release", "target_release"]
    for key in required_keys:
        if key not in payload:
            raise SystemExit(f"Invalid payload: missing required key '{key}'")

    issues = payload.get("issues", [])
    ordered_commits = payload.get("ordered_commits", [])
    meta = {
        "fix_version": payload.get("fix_version"),
        "base_release": payload.get("base_release"),
        "target_release": payload.get("target_release"),
        "base_tag": payload.get("base_tag"),
        "target_tag": payload.get("target_tag"),
    }

    current_repo = payload.get("repository")
    
    matrix_lines = render_matrix(issues, current_repo)
    write_file(args.matrix, "\n".join(matrix_lines) + "\n")

    cherry_pick_cmd = build_cherry_pick(meta, ordered_commits, current_repo)
    write_file(args.command, cherry_pick_cmd + "\n")

    print(f"Matrix written to {args.matrix}")
    print(f"Cherry-pick command written to {args.command}")


    summary_lines = [
        "# Release Branch Helper",
        "",
        f"- Fix Version: `{meta['fix_version']}`",
        f"- Base Release: `{meta['base_release']}` (tag `{meta['base_tag']}`)",
        f"- Target Release: `{meta['target_release']}` (tag `{meta['target_tag']}`)",
        f"- Tickets discovered: **{len(issues)}**",
        f"- Unique commits: **{len(ordered_commits)}**",
        "",
        "## Release Matrix",
        "",
    ]
    summary_lines.extend(matrix_lines)
    summary_lines.extend(
        [
            "",
            "## Cherry-pick Command",
            "",
            cherry_pick_cmd,
        ]
    )

    if args.summary:
        write_file(args.summary, "\n".join(summary_lines))

    print(f"Wrote release matrix to {args.matrix}")
    print(f"Wrote cherry-pick command to {args.command}")
    if args.summary:
        print(f"Wrote summary to {args.summary}")


if __name__ == "__main__":
    main()
