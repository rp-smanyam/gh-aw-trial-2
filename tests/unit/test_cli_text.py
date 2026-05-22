"""Unit tests for cli_text.py functionality."""

import json
import tempfile
from pathlib import Path

import httpx
import pytest

from agent_leasing.cli_text import (
    create_default_payload,
    load_payload,
    send_agent_request,
)


def test_load_payload_success():
    """Test loading a valid JSON file for text CLI."""
    test_data = {
        "product": "resident_one_chat",
        "prompt": "Hello",
        "product_info": {
            "knock_property_id": "12345",
            "knock_prospect_id": "67890",
        },
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(test_data, f)
        temp_path = f.name

    try:
        result = load_payload(temp_path)
        assert result == test_data
    finally:
        Path(temp_path).unlink()


def test_load_payload_file_not_found(capsys: pytest.CaptureFixture[str]):
    """Test that FileNotFoundError is handled correctly in text CLI."""
    with pytest.raises(SystemExit):
        load_payload("nonexistent_text_file.json")

    captured = capsys.readouterr()
    assert "nonexistent_text_file.json" in captured.err


def test_load_payload_invalid_json(capsys: pytest.CaptureFixture[str]):
    """Test that invalid JSON is handled correctly in text CLI."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{ invalid json content")
        temp_path = f.name

    try:
        with pytest.raises(SystemExit):
            load_payload(temp_path)
    finally:
        Path(temp_path).unlink()

    captured = capsys.readouterr()
    assert "Invalid JSON" in captured.err


def test_load_payload_empty_file(capsys: pytest.CaptureFixture[str]):
    """Test that empty JSON file is handled correctly in text CLI."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("")
        temp_path = f.name

    try:
        with pytest.raises(SystemExit):
            load_payload(temp_path)
    finally:
        Path(temp_path).unlink()

    captured = capsys.readouterr()
    # Any JSON decode error should be surfaced
    assert "Invalid JSON" in captured.err


def test_create_default_payload_structure():
    """create_default_payload should build a valid minimal text payload."""
    message = "Hi there"
    payload = create_default_payload(message)

    assert payload["product"] == "resident_one_chat"
    assert payload["prompt"] == message
    assert payload["chat_session_id"] == "cli-text-session"
    assert payload["request_id"] == "cli-text-request"
    assert payload["product_info"]["knock_property_id"] == "21521"
    assert payload["product_info"]["knock_prospect_id"] == "95946"


class DummyResponse:
    """Minimal stand-in for httpx.Response."""

    def __init__(self, json_data: dict, status_code: int = 200, headers: dict | None = None) -> None:
        self._json_data = json_data
        self.status_code = status_code
        self.headers = headers or {"content-type": "application/json"}

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=None,
                response=self,
            )


class DummyClient:
    """Minimal stand-in for httpx.Client used by send_agent_request."""

    def __init__(self, response: DummyResponse) -> None:
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, json=None):
        return self._response


def test_send_agent_request_success(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    """Successful request should print response and return True."""
    # Response is nested: content.chat is a stringified JSON with "response" field
    response_data = {"content": {"chat": json.dumps({"response": "Hello, world!", "languageCode": "en"})}}
    dummy_response = DummyResponse(response_data)

    def fake_client(*args, **kwargs):
        return DummyClient(dummy_response)

    monkeypatch.setattr("agent_leasing.cli_text.httpx.Client", fake_client)

    payload = {"prompt": "Hi"}
    ok = send_agent_request("http://example.com", payload, timeout=5, show_header=True)

    assert ok is True

    captured = capsys.readouterr()
    assert "Connecting to: http://example.com/v1/agent/ask" in captured.out
    assert "Hello, world!" in captured.out


def test_send_agent_request_empty_response(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    """Empty response should show placeholder message."""
    response_data = {"content": {"chat": json.dumps({"response": "", "languageCode": "en"})}}
    dummy_response = DummyResponse(response_data)

    def fake_client(*args, **kwargs):
        return DummyClient(dummy_response)

    monkeypatch.setattr("agent_leasing.cli_text.httpx.Client", fake_client)

    payload = {"prompt": "Hi"}
    ok = send_agent_request("http://example.com", payload, timeout=5, show_header=False)

    assert ok is True

    captured = capsys.readouterr()
    assert "[No response content]" in captured.out


def test_send_agent_request_timeout(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    """Timeouts from httpx should be handled and return False."""

    class TimeoutClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            raise httpx.TimeoutException("Request timed out")

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("agent_leasing.cli_text.httpx.Client", TimeoutClient)

    ok = send_agent_request("http://example.com", {"prompt": "Hi"}, timeout=1, show_header=False)

    assert ok is False
    captured = capsys.readouterr()
    assert "Request timed out" in captured.err


def test_send_agent_request_connect_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    """Connection errors should be handled and return False."""

    class ConnectErrorClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            raise httpx.ConnectError("Connection refused")

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("agent_leasing.cli_text.httpx.Client", ConnectErrorClient)

    ok = send_agent_request("http://example.com", {"prompt": "Hi"}, timeout=1, show_header=False)

    assert ok is False
    captured = capsys.readouterr()
    assert "Could not connect" in captured.err
