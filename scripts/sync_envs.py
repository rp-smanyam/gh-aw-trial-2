#!/usr/bin/env python3
"""Sync local env files with AWS Secrets Manager.

Each env file has two sections:
  OVERRIDES  — local developer overrides, never modified by this script.
  AWS        — values pulled from AWS Secrets Manager.

This script pulls secrets from AWS and updates only the AWS section:
  - Keys already set (uncommented) in OVERRIDES are skipped.
  - AWS keys with changed values are updated in place.
  - Missing keys are added alphabetically to the AWS section.
  - Values containing spaces are wrapped in double quotes.
  - If the env file doesn't exist, it is created from scratch.

Usage:
  uv run scripts/sync_envs.py              # sync all environments
  uv run scripts/sync_envs.py alpha        # sync alpha only
  uv run scripts/sync_envs.py beta prod    # sync beta and prod
  uv run scripts/sync_envs.py --help       # show this help
"""

import argparse
import json
import subprocess
from pathlib import Path

ENVS = {
    "alpha": {"file": ".alpha.env", "profile": None, "label": "ALPHA"},
    "beta": {"file": ".beta.env", "profile": "beta", "label": "BETA"},
    "prod": {"file": ".prod.env", "profile": "prod", "label": "PROD"},
}

SECRET_ID = "agent-leasing"

BANNER_TEMPLATE = """\
###############
###############
#### {label} ####
###############
###############

###############
###############
## OVERRIDES ##
###############
###############

###############
###############
##### AWS #####
###############
###############

"""


def pull_secrets(profile: str | None) -> dict:
    cmd = [
        "aws", "secretsmanager", "get-secret-value",
        "--secret-id", SECRET_ID,
        "--query", "SecretString",
        "--output", "text",
    ]
    if profile:
        cmd.extend(["--profile", profile])
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def format_value(key: str, value: str) -> str:
    if " " in value:
        return f'{key}="{value}"'
    return f"{key}={value}"


def make_banner_line(text: str, width: int = 15) -> str:
    """Create a centered banner line like '#### ALPHA ####' padded to width with #."""
    inner = f" {text} "
    remaining = width - len(inner)
    left = remaining // 2
    right = remaining - left
    return "#" * left + inner + "#" * right


def create_env_file(path: Path, label: str, secrets: dict) -> dict:
    label_line = make_banner_line(label)
    border = "#" * len(label_line)
    lines = [
        border,
        border,
        label_line,
        border,
        border,
        "",
        "###############",
        "###############",
        "## OVERRIDES ##",
        "###############",
        "###############",
        "",
        "###############",
        "###############",
        "##### AWS #####",
        "###############",
        "###############",
        "",
    ]
    for key in sorted(secrets):
        lines.append(format_value(key, secrets[key]))
    lines.append("")
    path.write_text("\n".join(lines))
    return {"skipped": [], "updated": [], "added": list(sorted(secrets.keys()))}


def sync_env_file(path: Path, secrets: dict) -> dict:
    lines = path.read_text().splitlines(keepends=True)

    # Find AWS banner line
    aws_banner_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "##### AWS #####":
            aws_banner_idx = i
            break

    if aws_banner_idx is None:
        print(f"  ERROR: No AWS section found in {path}")
        return {"skipped": [], "updated": [], "added": []}

    # Parse OVERRIDES keys (uncommented, before AWS banner)
    overrides_keys = set()
    for i in range(0, aws_banner_idx):
        stripped = lines[i].strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            overrides_keys.add(stripped.split("=", 1)[0])

    # Parse AWS section keys
    aws_keys = {}  # key -> (line_index, raw_value)
    for i in range(aws_banner_idx, len(lines)):
        stripped = lines[i].strip()
        if "=" in stripped and not stripped.startswith("#"):
            key, val = stripped.split("=", 1)
            aws_keys[key] = (i, val)

    skipped = []
    updated = []
    added = []

    for key in sorted(secrets):
        aws_val = secrets[key]
        if key in overrides_keys:
            skipped.append(key)
            continue
        if key in aws_keys:
            line_idx, local_val = aws_keys[key]
            local_clean = local_val.strip("'\"")
            aws_clean = aws_val.strip("'\"")
            if local_clean != aws_clean:
                updated.append((key, aws_val, local_val, line_idx))
                lines[line_idx] = format_value(key, aws_val) + "\n"
        else:
            added.append(key)

    # Insert added keys alphabetically in AWS section
    if added:
        # Find where AWS content starts (after banner)
        aws_content_start = aws_banner_idx + 1
        while aws_content_start < len(lines) and (
            lines[aws_content_start].strip().startswith("#")
            or lines[aws_content_start].strip() == ""
        ):
            aws_content_start += 1

        # Collect existing AWS content lines
        aws_content = []
        trailing = []
        found_content = False
        for i in range(aws_content_start, len(lines)):
            stripped = lines[i].strip()
            if "=" in stripped and not stripped.startswith("#"):
                found_content = True
                aws_content.append(lines[i])
            else:
                if found_content:
                    trailing.append(lines[i])
                else:
                    aws_content.append(lines[i])

        # Add new keys
        for key in added:
            aws_content.append(format_value(key, secrets[key]) + "\n")

        # Sort content lines by key (non-key lines like comments stay grouped)
        key_lines = [l for l in aws_content if "=" in l.strip() and not l.strip().startswith("#")]
        non_key_lines = [l for l in aws_content if not ("=" in l.strip() and not l.strip().startswith("#"))]
        key_lines.sort(key=lambda l: l.split("=", 1)[0].strip())

        lines = lines[:aws_content_start] + non_key_lines + key_lines + trailing

    path.write_text("".join(lines))
    return {"skipped": skipped, "updated": updated, "added": added}


def print_summary(env_name: str, result: dict):
    skipped = result["skipped"]
    updated = result["updated"]
    added = result["added"]

    print(f"\n{'=' * 50}")
    print(f"  {env_name.upper()}")
    print(f"{'=' * 50}")

    if skipped:
        print(f"\n  Skipped ({len(skipped)} — in OVERRIDES):")
        for k in skipped:
            print(f"    {k}")

    if updated:
        print(f"\n  Updated ({len(updated)}):")
        for item in updated:
            k, new_v, old_v = item[0], item[1], item[2]
            print(f"    {k}")
            print(f"      old: {old_v}")
            print(f"      new: {new_v}")

    if added:
        print(f"\n  Added ({len(added)}):")
        for k in added:
            print(f"    {k}")

    if not skipped and not updated and not added:
        print("\n  No changes.")

    total = len(skipped) + len(updated) + len(added)
    print(f"\n  Summary: {len(skipped)} skipped, {len(updated)} updated, {len(added)} added")


def main():
    parser = argparse.ArgumentParser(
        description="Sync local env files with AWS Secrets Manager.",
        epilog="Each env file has OVERRIDES (never touched) and AWS (synced) sections.",
    )
    parser.add_argument(
        "environments",
        nargs="*",
        default=list(ENVS.keys()),
        choices=[*ENVS.keys(), []],
        metavar="ENV",
        help=f"Environments to sync: {', '.join(ENVS.keys())} (default: all)",
    )
    parsed = parser.parse_args()
    targets = parsed.environments

    root = Path(__file__).resolve().parent.parent

    for env_name in targets:
        cfg = ENVS[env_name]
        env_path = root / cfg["file"]

        print(f"\nPulling secrets for {env_name}...")
        try:
            secrets = pull_secrets(cfg["profile"])
        except subprocess.CalledProcessError as e:
            print(f"  ERROR pulling secrets: {e.stderr}")
            continue

        if env_path.exists():
            result = sync_env_file(env_path, secrets)
        else:
            print(f"  {env_path.name} not found — creating from scratch.")
            result = create_env_file(env_path, cfg["label"], secrets)

        print_summary(env_name, result)


if __name__ == "__main__":
    main()
