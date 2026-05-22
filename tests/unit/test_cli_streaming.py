"""Unit tests for cli_streaming.py functionality."""

import json
import tempfile
from pathlib import Path

import httpx
import pytest

from agent_leasing.cli_streaming import (
    create_default_payload,
    load_payload,
    stream_agent_response,
)


def test_load_payload_success():
    """Test loading a valid JSON file for streaming CLI."""
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
    """Test that FileNotFoundError is handled correctly in streaming CLI."""
    with pytest.raises(SystemExit):
        load_payload("nonexistent_streaming_file.json")

    captured = capsys.readouterr()
    assert "nonexistent_streaming_file.json" in captured.err


def test_load_payload_invalid_json(capsys: pytest.CaptureFixture[str]):
    """Test that invalid JSON is handled correctly in streaming CLI."""
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
    """Test that empty JSON file is handled correctly in streaming CLI."""
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
    """create_default_payload should build a valid minimal streaming payload."""
    message = "Hi there"
    payload = create_default_payload(message)

    assert payload["product"] == "resident_one_chat"
    assert payload["prompt"] == message
    assert payload["chat_session_id"]
    assert payload["request_id"]
    assert payload["product_info"]["knock_property_id"] == "21521"
    assert payload["product_info"]["knock_prospect_id"] == "95946"


class DummySSE:
    def __init__(self, data: str) -> None:
        self.data = data


class DummyEventSource:
    def __init__(self, events: list[DummySSE]) -> None:
        self._events = events

    def __enter__(self):  # noqa: D401 - simple context manager
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: D401 - simple context manager
        return False

    def iter_sse(self):
        yield from self._events


class DummyClient:
    """Minimal stand-in for httpx.Client used by stream_agent_response."""

    def __init__(self, *args, **kwargs) -> None:  # noqa: D401 - simple init
        pass

    def __enter__(self):  # noqa: D401 - simple context manager
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: D401 - simple context manager
        return False


def test_stream_agent_response_success(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    """Successful streaming should print content and return True."""

    events = [
        DummySSE(
            json.dumps(
                {
                    "content": "Hello, world!",
                    "status": "ok",
                    "phase": "generating",
                    "done": True,
                }
            )
        ),
        DummySSE("[DONE]"),
    ]

    def fake_connect_sse(client, method, url, json):  # noqa: D401 - simple factory
        return DummyEventSource(events)

    monkeypatch.setattr("agent_leasing.cli_streaming.httpx.Client", DummyClient)
    monkeypatch.setattr("agent_leasing.cli_streaming.connect_sse", fake_connect_sse)

    payload = {"prompt": "Hi"}
    ok = stream_agent_response("http://example.com", payload, timeout=5, show_header=True)

    assert ok is True

    captured = capsys.readouterr()
    assert "Connecting to: http://example.com/v1/agent/stream" in captured.out
    assert "Hello, world!" in captured.out
    assert "Stream complete." in captured.out


def test_stream_agent_response_error_status(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    """If the SSE payload indicates an error status, function should return False."""

    events = [
        DummySSE(
            json.dumps(
                {
                    "content": "Something went wrong",
                    "status": "error",
                    "phase": "completed",
                    "done": True,
                }
            )
        )
    ]

    def fake_connect_sse(client, method, url, json):  # noqa: D401 - simple factory
        return DummyEventSource(events)

    monkeypatch.setattr("agent_leasing.cli_streaming.httpx.Client", DummyClient)
    monkeypatch.setattr("agent_leasing.cli_streaming.connect_sse", fake_connect_sse)

    ok = stream_agent_response("http://example.com", {"prompt": "Hi"}, timeout=5, show_header=False)

    assert ok is False
    captured = capsys.readouterr()
    assert "Error: Something went wrong" in captured.err


def test_stream_agent_response_timeout(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    """Timeouts from httpx should be handled and return False."""

    class TimeoutClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: D401 - simple init
            pass

        def __enter__(self):  # noqa: D401 - simple context manager
            raise httpx.TimeoutException("Request timed out")

        def __exit__(self, exc_type, exc, tb):  # noqa: D401 - simple context manager
            return False

    monkeypatch.setattr("agent_leasing.cli_streaming.httpx.Client", TimeoutClient)

    ok = stream_agent_response("http://example.com", {"prompt": "Hi"}, timeout=1, show_header=False)

    assert ok is False
    captured = capsys.readouterr()
    assert "Request timed out" in captured.err
