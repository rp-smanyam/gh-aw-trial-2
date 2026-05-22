#!/usr/bin/env python3
"""Mirror a GitHub issue to a Jira ticket in the KNCK project.

Environment:
  JIRA_BASE_URL   (default: https://knockr.atlassian.net)
  JIRA_EMAIL
  JIRA_API_TOKEN
  GH_ISSUE_NUMBER
  GH_ISSUE_TITLE
  GH_ISSUE_URL
  GH_ISSUE_BODY   (optional)

Writes `jira_key` and `created` to GITHUB_OUTPUT when available. Skips creation
if an existing KNCK ticket already references the GitHub issue URL in its
description.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from urllib import error, request

PROJECT_KEY = "KNCK"
ISSUE_TYPE = "Task"
AGILE_TEAM_ID = "13180"
LABELS = ["automated-analysis", "repo-quality-auto"]
MAX_BODY_EXCERPT = 2000


def need(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def auth_header(email: str, token: str) -> str:
    return base64.b64encode(f"{email}:{token}".encode()).decode()


def jira_request(url: str, auth: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except error.HTTPError as exc:
        detail = exc.read().decode()
        raise SystemExit(
            f"Jira {method} {url} failed: {exc.code} {exc.reason} - {detail}"
        ) from exc


def find_existing(base_url: str, auth: str, gh_url: str) -> str | None:
    jql = f'project = "{PROJECT_KEY}" AND description ~ "{gh_url}"'
    body = {"jql": jql, "maxResults": 1, "fields": ["summary"]}
    data = jira_request(
        f"{base_url}/rest/api/3/search/jql", auth, method="POST", body=body
    )
    issues = data.get("issues") or []
    return issues[0]["key"] if issues else None


def build_description(gh_url: str, gh_number: str, gh_title: str, gh_body: str) -> dict:
    content: list[dict] = [
        {
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "Mirrored from GitHub issue "},
                {
                    "type": "text",
                    "text": f"#{gh_number}: {gh_title}",
                    "marks": [{"type": "link", "attrs": {"href": gh_url}}],
                },
            ],
        }
    ]
    excerpt = (gh_body or "").strip()
    if excerpt:
        if len(excerpt) > MAX_BODY_EXCERPT:
            excerpt = excerpt[:MAX_BODY_EXCERPT] + "…"
        content.append(
            {"type": "paragraph", "content": [{"type": "text", "text": excerpt}]}
        )
    return {"type": "doc", "version": 1, "content": content}


def create_ticket(base_url: str, auth: str, title: str, description: dict) -> str:
    body = {
        "fields": {
            "project": {"key": PROJECT_KEY},
            "issuetype": {"name": ISSUE_TYPE},
            "summary": title[:250],
            "description": description,
            "labels": LABELS,
            "customfield_10432": {"id": AGILE_TEAM_ID},
        }
    }
    data = jira_request(
        f"{base_url}/rest/api/3/issue", auth, method="POST", body=body
    )
    key = data.get("key")
    if not key:
        raise SystemExit(f"Jira creation returned no key: {data}")
    return key


def write_output(key: str, created: bool) -> None:
    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        with open(out_path, "a") as f:
            f.write(f"jira_key={key}\n")
            f.write(f"created={'true' if created else 'false'}\n")
    print(key)


def main() -> None:
    base_url = os.environ.get(
        "JIRA_BASE_URL", "https://knockr.atlassian.net"
    ).rstrip("/")
    email = need("JIRA_EMAIL")
    token = need("JIRA_API_TOKEN")
    gh_number = need("GH_ISSUE_NUMBER")
    gh_title = need("GH_ISSUE_TITLE")
    gh_url = need("GH_ISSUE_URL")
    gh_body = os.environ.get("GH_ISSUE_BODY", "")

    auth = auth_header(email, token)
    existing = find_existing(base_url, auth, gh_url)
    if existing:
        print(f"Existing Jira ticket found: {existing}", file=sys.stderr)
        write_output(existing, created=False)
        return

    title = gh_title if gh_title.startswith("[Auto]") else f"[Auto] {gh_title}"
    description = build_description(gh_url, gh_number, gh_title, gh_body)
    key = create_ticket(base_url, auth, title, description)
    print(f"Created Jira ticket: {key}", file=sys.stderr)
    write_output(key, created=True)


if __name__ == "__main__":
    main()
