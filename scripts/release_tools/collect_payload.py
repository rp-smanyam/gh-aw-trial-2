#!/usr/bin/env python3
"""
Collect Jira issues for a fixVersion, enumerate merged GitHub PRs, and emit a JSON payload.

Required environment variables:
  JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN
  FIX_VERSION, JIRA_PROJECT_KEY
  BASE_RELEASE, TARGET_RELEASE, BASE_TAG, TARGET_TAG
  GITHUB_REPOSITORY (provided automatically in GitHub Actions)

Optional:
  CROSSORG_GITHUB_TOKEN (defaults to the workflow token)

Local debugging:
  export the variables above, then run
  `python scripts/release_tools/collect_payload.py --output /tmp/release_payload.json`
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib import error, parse, request

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


def need(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def build_auth_header(email: str, token: str) -> str:
    return base64.b64encode(f"{email}:{token}".encode()).decode()


def jira_search(
    base_url: str,
    auth_header: str,
    project_key: str,
    fix_version: str,
    start_at: int,
) -> Dict[str, Any]:
    jql = f'project = "{project_key}" AND fixVersion = "{fix_version}" ORDER BY key ASC'
    
    body = {
        "jql": jql,
        "maxResults": 100,
        "fields": ["summary", "status", "assignee"]
    }

    payload = json.dumps(body).encode('utf-8')

    url = f"{base_url}/rest/api/3/search/jql"
    req = request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Basic {auth_header}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except error.HTTPError as exc:
        detail = exc.read().decode()
        raise SystemExit(
            f"Failed to query Jira: {exc.code} {exc.reason} - {detail}"
        ) from exc

    issues = data.get("issues") or []
    total = data.get("total", len(issues))
    max_results = data.get("maxResults", len(issues))
    return {"issues": issues, "total": total, "maxResults": max_results}


def github_get_json(
    url: str,
    github_token: str | None,
    params: Dict[str, Any] | None = None,
    rate_limit_delay: float = 0.1,
) -> Any:
    """Make a GitHub API request with rate limiting.
    
    Args:
        url: GitHub API URL
        github_token: Optional GitHub token for authentication
        params: Optional query parameters
        rate_limit_delay: Delay in seconds between requests (default 0.1s = 600 requests/min max)
    """
    # Add delay to respect rate limits (900 points/min secondary limit)
    time.sleep(rate_limit_delay)
    
    if params:
        encoded = parse.urlencode(params)
        url = f"{url}&{encoded}" if "?" in url else f"{url}?{encoded}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "release-branch-helper",
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    req = request.Request(url, headers=headers)
    try:
        with request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except error.HTTPError as exc:  # pragma: no cover - requires live HTTP
        detail = exc.read().decode()
        raise SystemExit(
            f"GitHub request failed: {exc.code} {exc.reason} - {detail}"
        ) from exc


def github_get_paginated(url: str, github_token: str | None) -> List[Dict[str, Any]]:
    collected: List[Dict[str, Any]] = []
    page = 1
    while True:
        batch = github_get_json(
            url,
            github_token=github_token,
            params={"per_page": 100, "page": page},
        )
        if not isinstance(batch, list):
            raise SystemExit(
                f"Expected list response from GitHub but received {type(batch).__name__}"
            )
        collected.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return collected


def github_search_prs(
    issue_key: str,
    github_token: str | None,
) -> List[Dict[str, Any]]:
    query = f'is:pr "{issue_key}" in:title org:RealPage org:knockrentals'
    url = f"https://api.github.com/search/issues?q={parse.quote(query)}&per_page=100"
    print(f"Searching for PRs related to {issue_key}...")
    data = github_get_json(url, github_token=github_token, rate_limit_delay=0.3)
    items = data.get("items", [])
    pull_requests: List[Dict[str, Any]] = []
    for item in items:
        pr_url = item.get("pull_request", {}).get("url")
        if not pr_url:
            continue
        print(f"  Fetching PR details for {item.get('html_url', 'unknown')}...")
        pr_data = github_get_json(pr_url, github_token=github_token, rate_limit_delay=0.3)
        repo = pr_data.get("base", {}).get("repo", {}).get("full_name")
        if not pr_data.get("merged_at"):
            continue
        commits = []
        merge_commit_sha = pr_data.get("merge_commit_sha")
        if merge_commit_sha:
            # For squash merges, get the merge commit details
            commit_url = f"https://api.github.com/repos/{repo}/commits/{merge_commit_sha}"
            print(f"    Fetching commit details for {merge_commit_sha[:7]}...")
            commit_data = github_get_json(commit_url, github_token=github_token, rate_limit_delay=0.3)
            if commit_data:
                commits.append(
                    {
                        "sha": commit_data.get("sha"),
                        "message": (commit_data.get("commit", {}).get("message") or "").splitlines()[0],
                        "html_url": commit_data.get("html_url"),
                    }
                )
        pull_requests.append(
            {
                "repo": repo,
                "number": pr_data.get("number"),
                "title": pr_data.get("title"),
                "url": pr_data.get("html_url"),
                "merged_at": pr_data.get("merged_at"),
                "base_ref": pr_data.get("base", {}).get("ref"),
                "head_ref": pr_data.get("head", {}).get("ref"),
                "author": pr_data.get("user", {}).get("login"),
                "commits": commits,
            }
        )
    pull_requests.sort(key=lambda pr: pr.get("merged_at") or "")
    return pull_requests


def compile_payload() -> Dict[str, Any]:
    jira_base = need("JIRA_BASE_URL").rstrip("/")
    jira_email = need("JIRA_EMAIL")
    jira_token = need("JIRA_API_TOKEN")
    fix_version = need("FIX_VERSION")
    project_key = need("JIRA_PROJECT_KEY")
    repo = need("GITHUB_REPOSITORY")
    base_release = need("BASE_RELEASE")
    target_release = need("TARGET_RELEASE")
    base_tag = need("BASE_TAG")
    target_tag = need("TARGET_TAG")
    github_token = os.environ.get("CROSSORG_GITHUB_TOKEN")

    auth_header = build_auth_header(jira_email, jira_token)

    issues: List[Dict[str, Any]] = []
    start = 0
    while True:
        try:
            chunk = jira_search(
                jira_base,
                auth_header,
                project_key,
                fix_version,
                start_at=start,
            )
        except error.HTTPError as exc:  # pragma: no cover - requires live HTTP
            detail = exc.read().decode()
            raise SystemExit(
                f"Failed to query Jira: {exc.code} {exc.reason} - {detail}"
            ) from exc
        issues.extend(chunk.get("issues", []))
        start += chunk.get("maxResults", 0)
        if start >= chunk.get("total", 0):
            break

    compiled_issues: List[Dict[str, Any]] = []
    for issue in issues:
        fields = issue.get("fields", {})
        assignee = fields.get("assignee") or {}
        pull_requests = github_search_prs(issue_key=issue.get("key"), github_token=github_token)
        compiled_issues.append(
            {
                "key": issue.get("key"),
                "id": issue.get("id"),
                "summary": fields.get("summary"),
                "status": (fields.get("status") or {}).get("name"),
                "assignee": assignee.get("displayName") or assignee.get("emailAddress") or "Unassigned",
                "url": f"{jira_base}/browse/{issue.get('key')}",
                "pull_requests": pull_requests,
            }
        )

    seen_commits = set()
    commits_with_metadata: List[Dict[str, Any]] = []
    for issue in compiled_issues:
        for pr in issue["pull_requests"]:
            for commit in pr["commits"]:
                sha = commit.get("sha")
                if sha and sha not in seen_commits:
                    seen_commits.add(sha)
                    commits_with_metadata.append(
                        {
                            "sha": sha,
                            "repo": pr["repo"],
                            "issue_key": issue["key"],
                            "pr_number": pr["number"],
                            "pr_url": pr["url"],
                            "merged_at": pr["merged_at"],
                        }
                    )

    # Sort commits chronologically by merge date
    commits_with_metadata.sort(key=lambda c: c.get("merged_at") or "")

    ordered_commits = [
        {
            "sha": c["sha"],
            "repo": c["repo"],
            "issue_key": c["issue_key"],
            "pr_number": c["pr_number"],
            "pr_url": c["pr_url"],
            "merged_at": c["merged_at"],
        }
        for c in commits_with_metadata
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repository": repo,
        "fix_version": fix_version,
        "base_release": base_release,
        "target_release": target_release,
        "base_tag": base_tag,
        "target_tag": target_tag,
        "issues": compiled_issues,
        "ordered_commits": ordered_commits,
    }


def main() -> None:
    # Load .env file if it exists (for local testing)
    if load_dotenv:
        env_path = Path(__file__).parent / ".env"
        print(f"Looking for .env at: {env_path}")
        if env_path.exists():
            result = load_dotenv(env_path, override=True)
            print(f"Loaded environment variables from {env_path}: {result}")
            # Verify some key variables loaded
            if os.environ.get("GITHUB_REPOSITORY"):
                print(f"  GITHUB_REPOSITORY: {os.environ.get('GITHUB_REPOSITORY')}")
        else:
            print(f"  .env file not found at {env_path}")
    
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="release_payload.json",
        help="Path to write the JSON payload (default: %(default)s)",
    )
    args = parser.parse_args()

    payload = compile_payload()
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    print(
        f"Collected {len(payload['issues'])} Jira issues for fixVersion '{payload['fix_version']}'."
    )
    print(f"Total unique commits discovered: {len(payload['ordered_commits'])}.")
    print(f"Commit list: {', '.join(p['sha'] for p in payload['ordered_commits'])}")
    print(f"Payload written to {args.output}.")


if __name__ == "__main__":
    main()
