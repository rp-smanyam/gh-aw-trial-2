#!/usr/bin/env python3
"""
IE Sandbox Verification Script

Verifies that IE sandbox endpoints are correctly configured and accessible
for agent-leasing integration testing. Requires Aspire to be running.

Usage:
    uv run scripts/verify_sandbox.py SANDBOX_NAME [options]

Examples:
    uv run scripts/verify_sandbox.py gt6ym2bvx              # Verify sandbox (requires Aspire)
    uv run scripts/verify_sandbox.py gt6ym2bvx --verbose    # Show details
    uv run scripts/verify_sandbox.py gt6ym2bvx --warmup     # Also run warmup test
"""

import argparse
import glob
import os
import platform
import re
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from typing import Optional

# Try to import httpx, fall back to urllib if not available
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    import urllib.request
    import urllib.error
    import json

# Try to import rich for colored output, fall back to plain text
try:
    from rich.console import Console
    from rich.table import Table
    from rich import print as rprint
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None


class Status(Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    SKIP = "skip"


@dataclass
class CheckResult:
    name: str
    status: Status
    message: str
    details: Optional[str] = None


# ANSI color codes for fallback
COLORS = {
    Status.PASS: "\033[92m",  # Green
    Status.FAIL: "\033[91m",  # Red
    Status.WARN: "\033[93m",  # Yellow
    Status.SKIP: "\033[90m",  # Gray
}
RESET = "\033[0m"


def print_result(result: CheckResult, verbose: bool = False) -> None:
    """Print a check result with appropriate formatting."""
    status_symbols = {
        Status.PASS: "[PASS]" if not RICH_AVAILABLE else "[green]✓ PASS[/green]",
        Status.FAIL: "[FAIL]" if not RICH_AVAILABLE else "[red]✗ FAIL[/red]",
        Status.WARN: "[WARN]" if not RICH_AVAILABLE else "[yellow]⚠ WARN[/yellow]",
        Status.SKIP: "[SKIP]" if not RICH_AVAILABLE else "[dim]○ SKIP[/dim]",
    }

    if RICH_AVAILABLE:
        rprint(f"{status_symbols[result.status]} {result.name}: {result.message}")
        if verbose and result.details:
            rprint(f"    [dim]{result.details}[/dim]")
    else:
        color = COLORS.get(result.status, "")
        print(f"{color}{status_symbols[result.status]}{RESET} {result.name}: {result.message}")
        if verbose and result.details:
            print(f"    {result.details}")


def print_section(title: str) -> None:
    """Print a section header."""
    print()
    if RICH_AVAILABLE:
        rprint(f"[bold]=== {title} ===[/bold]")
    else:
        print(f"=== {title} ===")


def http_get(url: str, timeout: int = 10) -> tuple[int, Optional[dict], Optional[str]]:
    """Make an HTTP GET request and return (status_code, json_body, error)."""
    if HTTPX_AVAILABLE:
        try:
            with httpx.Client(timeout=timeout, verify=True) as client:
                response = client.get(url)
                try:
                    json_body = response.json()
                except Exception:
                    json_body = None
                return response.status_code, json_body, None
        except httpx.TimeoutException:
            return 0, None, "Request timed out"
        except httpx.RequestError as e:
            return 0, None, str(e)
    else:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "verify_sandbox/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                try:
                    json_body = json.loads(body)
                except Exception:
                    json_body = None
                return response.status, json_body, None
        except urllib.error.HTTPError as e:
            return e.code, None, None
        except urllib.error.URLError as e:
            return 0, None, str(e.reason)
        except Exception as e:
            return 0, None, str(e)


def http_post(url: str, data: dict, timeout: int = 10) -> tuple[int, Optional[dict], Optional[str]]:
    """Make an HTTP POST request and return (status_code, json_body, error)."""
    if HTTPX_AVAILABLE:
        try:
            with httpx.Client(timeout=timeout, verify=True) as client:
                response = client.post(url, data=data)
                try:
                    json_body = response.json()
                except Exception:
                    json_body = None
                return response.status_code, json_body, None
        except httpx.TimeoutException:
            return 0, None, "Request timed out"
        except httpx.RequestError as e:
            return 0, None, str(e)
    else:
        try:
            encoded_data = urllib.parse.urlencode(data).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=encoded_data,
                headers={
                    "User-Agent": "verify_sandbox/1.0",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                try:
                    json_body = json.loads(body)
                except Exception:
                    json_body = None
                return response.status, json_body, None
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8")
                json_body = json.loads(body)
            except Exception:
                json_body = None
            return e.code, json_body, None
        except urllib.error.URLError as e:
            return 0, None, str(e.reason)
        except Exception as e:
            return 0, None, str(e)


def http_post_json(url: str, json_data: dict, timeout: int = 10) -> tuple[int, Optional[dict], Optional[str]]:
    """Make an HTTP POST request with JSON body and return (status_code, json_body, error)."""
    if HTTPX_AVAILABLE:
        try:
            with httpx.Client(timeout=timeout, verify=True) as client:
                response = client.post(url, json=json_data)
                try:
                    json_body = response.json()
                except Exception:
                    json_body = None
                return response.status_code, json_body, None
        except httpx.TimeoutException:
            return 0, None, "Request timed out"
        except httpx.RequestError as e:
            return 0, None, str(e)
    else:
        try:
            import urllib.parse
            json_bytes = json.dumps(json_data).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=json_bytes,
                headers={
                    "User-Agent": "verify_sandbox/1.0",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                try:
                    json_body = json.loads(body)
                except Exception:
                    json_body = None
                return response.status, json_body, None
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8")
                json_body = json.loads(body)
            except Exception:
                json_body = None
            return e.code, json_body, None
        except urllib.error.URLError as e:
            return 0, None, str(e.reason)
        except Exception as e:
            return 0, None, str(e)


def find_aspire_temp_dir() -> Optional[str]:
    """
    Find the most recent Aspire temporary directory.
    Works cross-platform (macOS, Windows, Linux).
    """
    candidates = []

    # Get the system temp directory
    temp_base = tempfile.gettempdir()

    if platform.system() == "Darwin":
        # macOS: Aspire uses /var/folders/*/*/T/aspire.*
        # tempfile.gettempdir() returns the correct path like /var/folders/xx/xxx/T
        pattern = os.path.join(temp_base, "aspire.*")
        candidates.extend(glob.glob(pattern))
    elif platform.system() == "Windows":
        # Windows: Check %TEMP%/aspire.*
        pattern = os.path.join(temp_base, "aspire.*")
        candidates.extend(glob.glob(pattern))
    else:
        # Linux: Check /tmp/aspire.*
        pattern = os.path.join(temp_base, "aspire.*")
        candidates.extend(glob.glob(pattern))
        # Also check /tmp directly in case tempdir is different
        candidates.extend(glob.glob("/tmp/aspire.*"))

    if not candidates:
        return None

    # Return the most recently modified directory
    candidates.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    return candidates[0]


def detect_agent_url() -> Optional[str]:
    """
    Auto-detect the agent-leasing URL from Aspire's temporary files.

    Aspire stores resource startup logs in its temp directory. We parse these
    to find the port that agent-leasing was started on.

    Returns:
        The agent URL (e.g., "http://localhost:50548") or None if not found.
    """
    aspire_dir = find_aspire_temp_dir()
    if not aspire_dir:
        return None

    # Look for resource-executable logs that mention agent-leasing
    log_pattern = os.path.join(aspire_dir, "resource-executable-*.log")
    log_files = glob.glob(log_pattern)

    for log_file in log_files:
        try:
            with open(log_file, "r") as f:
                content = f.read()
                # Look for agent-leasing startup with port
                if "agent-leasing" in content:
                    # Extract port from Args like: "--port", "50548"
                    match = re.search(r'"--port",\s*"(\d+)"', content)
                    if match:
                        port = match.group(1)
                        return f"http://localhost:{port}"
        except Exception:
            continue

    return None


def check_kong_reachable(sandbox: str) -> CheckResult:
    """Check Kong gateway is reachable for this sandbox."""
    url = f"https://internalapi-sandbox.realpage.com/{sandbox}/os/"

    status_code, _, error = http_get(url)

    if error:
        return CheckResult(
            name="Kong gateway reachable",
            status=Status.FAIL,
            message=f"Connection failed: {error}",
            details=f"URL: {url}",
        )

    # Any HTTP response means Kong is reachable
    # Kong may return 404 without auth (security feature - doesn't reveal routes)
    # Actual routing is verified by the warmup test
    return CheckResult(
        name="Kong gateway reachable",
        status=Status.PASS,
        message=f"Kong responded (status {status_code})",
        details=f"URL: {url}",
    )


def check_oauth_discovery(sandbox: str) -> CheckResult:
    """Check OAuth OpenID discovery endpoint."""
    url = f"https://{sandbox}-upfm-ui.dev.sb.realpage.com/login/identity/.well-known/openid-configuration"

    status_code, json_body, error = http_get(url)

    if error:
        return CheckResult(
            name="OAuth discovery endpoint",
            status=Status.FAIL,
            message=f"Connection failed: {error}",
            details=f"URL: {url}",
        )

    if status_code == 404:
        return CheckResult(
            name="OAuth discovery endpoint",
            status=Status.FAIL,
            message="Sandbox not found (404) - may be expired",
            details=f"URL: {url}",
        )

    if status_code != 200:
        return CheckResult(
            name="OAuth discovery endpoint",
            status=Status.FAIL,
            message=f"Unexpected status code: {status_code}",
            details=f"URL: {url}",
        )

    if not json_body:
        return CheckResult(
            name="OAuth discovery endpoint",
            status=Status.FAIL,
            message="Response is not valid JSON",
            details=f"URL: {url}",
        )

    # Check for required fields
    required_fields = ["issuer", "token_endpoint", "scopes_supported"]
    missing = [f for f in required_fields if f not in json_body]

    if missing:
        return CheckResult(
            name="OAuth discovery endpoint",
            status=Status.WARN,
            message=f"Missing fields: {', '.join(missing)}",
            details=f"URL: {url}",
        )

    return CheckResult(
        name="OAuth discovery endpoint",
        status=Status.PASS,
        message="Discovery endpoint available",
        details=f"Issuer: {json_body.get('issuer')}",
    )


def check_oauth_scopes(sandbox: str, required_scopes: list[str]) -> CheckResult:
    """Check that required OAuth scopes are available for Facilities."""
    url = f"https://{sandbox}-upfm-ui.dev.sb.realpage.com/login/identity/.well-known/openid-configuration"

    status_code, json_body, error = http_get(url)

    if error or status_code != 200 or not json_body:
        return CheckResult(
            name="Required scopes available",
            status=Status.SKIP,
            message="Skipped (discovery failed)",
        )

    available_scopes = set(json_body.get("scopes_supported", []))
    required_set = set(required_scopes)
    missing = required_set - available_scopes

    if missing:
        return CheckResult(
            name="Required scopes available",
            status=Status.WARN,
            message=f"Missing scopes: {', '.join(sorted(missing))}",
            details=f"Available: {', '.join(sorted(available_scopes))}",
        )

    return CheckResult(
        name="Required scopes available",
        status=Status.PASS,
        message=f"All {len(required_scopes)} Facilities scopes available",
        details=f"Scopes: {', '.join(sorted(required_scopes))}",
    )


def check_oauth_token_endpoint(sandbox: str) -> CheckResult:
    """Check OAuth token endpoint is reachable."""
    url = f"https://{sandbox}-upfm-ui.dev.sb.realpage.com/login/identity/connect/token"

    # Send minimal request to check connectivity (will fail auth but confirms endpoint exists)
    status_code, json_body, error = http_post(url, {"grant_type": "client_credentials"})

    if error:
        return CheckResult(
            name="OAuth token endpoint",
            status=Status.FAIL,
            message=f"Connection failed: {error}",
            details=f"URL: {url}",
        )

    if status_code == 404:
        return CheckResult(
            name="OAuth token endpoint",
            status=Status.FAIL,
            message="Endpoint not found (404)",
            details=f"URL: {url}",
        )

    # 400 is expected (missing client_id), 401 also acceptable
    if status_code in [400, 401]:
        return CheckResult(
            name="OAuth token endpoint",
            status=Status.PASS,
            message=f"Endpoint reachable (status {status_code} expected without credentials)",
            details=f"URL: {url}",
        )

    return CheckResult(
        name="OAuth token endpoint",
        status=Status.WARN,
        message=f"Unexpected status code: {status_code}",
        details=f"URL: {url}",
    )


def check_token_acquisition(
    sandbox: str,
    client_id: str,
    client_secret: str,
    scopes: str,
) -> CheckResult:
    """Test actual OAuth token acquisition."""
    url = f"https://{sandbox}-upfm-ui.dev.sb.realpage.com/login/identity/connect/token"

    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": scopes,
    }

    status_code, json_body, error = http_post(url, data)

    if error:
        return CheckResult(
            name="Token acquisition",
            status=Status.FAIL,
            message=f"Connection failed: {error}",
        )

    if status_code == 200 and json_body and "access_token" in json_body:
        return CheckResult(
            name="Token acquisition",
            status=Status.PASS,
            message="Successfully acquired access token",
            details=f"Token type: {json_body.get('token_type')}, expires in: {json_body.get('expires_in')}s",
        )

    error_desc = json_body.get("error_description", json_body.get("error", "Unknown error")) if json_body else "Unknown error"
    return CheckResult(
        name="Token acquisition",
        status=Status.FAIL,
        message=f"Failed to acquire token: {error_desc}",
        details=f"Status: {status_code}",
    )


def check_warmup(agent_url: str, timeout: int = 90) -> CheckResult:
    """
    Warm up by sending a test lease question to the agent.

    This triggers the OneSite MCP server to make authenticated requests,
    verifying the full stack works end-to-end.
    """
    url = f"{agent_url.rstrip('/')}/v1/agent/ask"

    # Test payload using sandbox test data (RP NorthStar dataset)
    # These IDs correspond to a test resident "Amy Buck" in the default sandbox data
    payload = {
        "product": "renter_ai_resident_chat",
        "request_type": "standard",
        "prompt": "When does my lease end?",
        "product_info": {
            "source": "LL",
            "knock_property_id": "21521",
            "knock_resident_id": "137",
            "ai_config": {
                "is_chat_enabled": True,
                "is_gen_ai_chat_enabled": True,
                "resident_virtual_agent_chat": True,
                "resident_virtual_agent_chat_gen_ai": True,
            },
            "property_name": "Test Property",
            "uc_portal_base_url": "https://test.example.com",
            "uc_first_name": "Amy",
            "uc_last_name": "Buck",
            "uc_company_id": {"id": "4341841", "source": "OS"},
            "uc_property_id": {"id": "4341851", "source": "OS"},
            "uc_lease_id": {"id": "1101", "source": "OS"},
            "uc_resident_household_id": {"id": "12339", "source": "OS"},
            "uc_resident_member_id": {"id": "12393", "source": "OS"},
            "ab_resident_id": {"id": "4860883", "source": "AB"},
        },
    }

    if RICH_AVAILABLE:
        rprint(f"[dim]Sending warmup request (this may take up to 90 seconds)...[/dim]")
    else:
        print(f"Sending warmup request (this may take up to 90 seconds)...")

    status_code, json_body, error = http_post_json(url, payload, timeout=timeout)

    if error:
        if "timed out" in error.lower():
            return CheckResult(
                name="Warmup test",
                status=Status.FAIL,
                message=f"Request timed out after {timeout}s",
                details=f"URL: {url}",
            )
        return CheckResult(
            name="Warmup test",
            status=Status.FAIL,
            message=f"Connection failed: {error}",
            details=f"URL: {url}",
        )

    if status_code == 200:
        return CheckResult(
            name="Warmup test",
            status=Status.PASS,
            message="Lease question answered successfully",
            details="OneSite API responded via Kong",
        )

    error_msg = json_body.get("detail", json_body.get("error", "Unknown error")) if json_body else "Unknown error"
    return CheckResult(
        name="Warmup test",
        status=Status.FAIL,
        message=f"Agent returned error (status {status_code}): {error_msg}",
        details=f"URL: {url}",
    )


def print_summary(results: list[CheckResult]) -> int:
    """Print summary and return exit code."""
    passed = sum(1 for r in results if r.status == Status.PASS)
    failed = sum(1 for r in results if r.status == Status.FAIL)
    warned = sum(1 for r in results if r.status == Status.WARN)

    print()
    if RICH_AVAILABLE:
        rprint(f"[bold]Summary:[/bold] {passed} passed, {failed} failed, {warned} warnings")
    else:
        print(f"Summary: {passed} passed, {failed} failed, {warned} warnings")

    if failed > 0:
        if RICH_AVAILABLE:
            rprint("[red]Some checks failed. See details above.[/red]")
        else:
            print("Some checks failed. See details above.")
        return 1
    elif warned > 0:
        if RICH_AVAILABLE:
            rprint("[yellow]All critical checks passed with warnings.[/yellow]")
        else:
            print("All critical checks passed with warnings.")
        return 0
    else:
        if RICH_AVAILABLE:
            rprint("[green]All checks passed![/green]")
        else:
            print("All checks passed!")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify IE sandbox configuration for agent-leasing integration (requires Aspire)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run scripts/verify_sandbox.py gt6ym2bvx              # Verify sandbox (requires Aspire)
  uv run scripts/verify_sandbox.py gt6ym2bvx --verbose    # Show details
  uv run scripts/verify_sandbox.py gt6ym2bvx --warmup     # Also run warmup test (recommended)
        """,
    )
    parser.add_argument(
        "sandbox",
        help="Sandbox name (e.g., gt6ym2bvx)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output",
    )
    parser.add_argument(
        "--warmup",
        action="store_true",
        help="Run warmup test (sends a lease question to verify end-to-end)",
    )
    parser.add_argument(
        "--agent-url",
        help="Agent-leasing URL (auto-detected from Aspire if not specified)",
    )
    parser.add_argument(
        "--client-id",
        help="OAuth client ID for Facilities token acquisition test",
    )
    parser.add_argument(
        "--client-secret",
        help="OAuth client secret for Facilities token acquisition test",
    )
    parser.add_argument(
        "--scopes",
        default="facilitiescommonapi facilitiesinspectionsapi facilitiesservicerequestsapi",
        help="OAuth scopes to request for Facilities (space-separated)",
    )

    args = parser.parse_args()

    results: list[CheckResult] = []

    # === Prerequisites ===
    print_section("Prerequisites")

    # Detect Aspire - this is required
    agent_url = args.agent_url
    if not agent_url:
        agent_url = detect_agent_url()

    if agent_url:
        results.append(CheckResult(
            name="Aspire detected",
            status=Status.PASS,
            message=f"Agent-leasing at {agent_url}",
        ))
        print_result(results[-1], args.verbose)
    else:
        results.append(CheckResult(
            name="Aspire detected",
            status=Status.FAIL,
            message="Could not detect Aspire. Is it running?",
            details="Start Aspire with: dotnet run --project agent-leasing/src/AgentLeasing.AppHost",
        ))
        print_result(results[-1], args.verbose)
        return print_summary(results)

    if RICH_AVAILABLE:
        rprint(f"[dim]Verifying sandbox: {args.sandbox}[/dim]")
    else:
        print(f"Verifying sandbox: {args.sandbox}")

    # Check Kong is reachable (actual routing verified by warmup test)
    results.append(check_kong_reachable(args.sandbox))
    print_result(results[-1], args.verbose)

    # === OneSite (API Key Auth via Kong) ===
    print_section("OneSite (API Key Auth via Kong)")

    # Warmup test (OneSite-focused lease question)
    if args.warmup:
        results.append(check_warmup(agent_url))
        print_result(results[-1], args.verbose)
    else:
        if RICH_AVAILABLE:
            rprint("[dim]  (use --warmup to test end-to-end)[/dim]")
        else:
            print("  (use --warmup to test end-to-end)")

    # === Facilities (OAuth via Kong) ===
    print_section("Facilities (OAuth via Kong)")

    # OAuth checks (for Facilities only)
    results.append(check_oauth_discovery(args.sandbox))
    print_result(results[-1], args.verbose)

    results.append(check_oauth_token_endpoint(args.sandbox))
    print_result(results[-1], args.verbose)

    # Required scopes for Facilities only
    facilities_scopes = [
        "facilitiescommonapi",
        "facilitiesinspectionsapi",
        "facilitiesservicerequestsapi",
    ]
    results.append(check_oauth_scopes(args.sandbox, facilities_scopes))
    print_result(results[-1], args.verbose)

    # Optional token acquisition test (for Facilities)
    if args.client_id and args.client_secret:
        results.append(check_token_acquisition(
            args.sandbox,
            args.client_id,
            args.client_secret,
            args.scopes,
        ))
        print_result(results[-1], args.verbose)

    return print_summary(results)


if __name__ == "__main__":
    sys.exit(main())
