#!/usr/bin/env bash
# Run Locust load tests with pip_system_certs disabled.
#
# pip_system_certs patches ssl.SSLContext at startup via a .pth file.
# This is incompatible with gevent's monkey-patched SSL (used by Locust),
# causing CERTIFICATE_VERIFY_FAILED on all HTTPS requests.
#
# This wrapper sets PYTHONDONTWRITEBYTECODE and uses a sitecustomize.py
# approach — but the simplest fix is to just rename the .pth file
# temporarily. The trap restores it on exit.
#
# Usage:
#   ./scripts/run_locust.sh [locust args...]
#
# Example:
#   LOAD_TEST_PAYLOAD=data/payloads/beta-chat.json ./scripts/run_locust.sh \
#       --host https://beta-agent-leasing.knocktest.com --headless -u 10 -r 2 -t 2m AgentAskUser

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PTH="$(find "$PROJECT_DIR/.venv" -name 'pip_system_certs.pth' 2>/dev/null | head -1)"

if [[ -n "$PTH" && -f "$PTH" ]]; then
    mv "$PTH" "$PTH.bak"
    trap 'mv "$PTH.bak" "$PTH" 2>/dev/null' EXIT
fi

export SSL_CERT_FILE="${SSL_CERT_FILE:-$(cd "$PROJECT_DIR" && uv run python -c 'import certifi; print(certifi.where())')}"

cd "$PROJECT_DIR"
uv run locust -f tests/load/locustfile.py "$@"
