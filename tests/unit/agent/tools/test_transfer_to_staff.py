import asyncio
import importlib
from types import SimpleNamespace

import pytest

from agent_leasing.agent.tools.transfer_to_staff.handoff import get_handoff_key
from agent_leasing.api.model import AskContent, AskResponse, Channel
from agent_leasing.settings import settings
from agent_leasing.util.twilio_util import get_twilio_credentials

tts = importlib.import_module("agent_leasing.agent.tools.transfer_to_staff.transfer_to_staff_voice")
ttt = importlib.import_module("agent_leasing.agent.tools.transfer_to_staff.transfer_to_staff_text")


class DummyTwilioCall:
    def __init__(self, status="in-progress") -> None:
        self.status = status


class DummyTwilioCallUpdater:
    def __init__(self, sid_captured: list[str], args_captured: list[dict]) -> None:
        self.sid_captured = sid_captured
        self.args_captured = args_captured

    def update(self, **kwargs):
        self.args_captured.append(kwargs)
        return DummyTwilioCall(status="in-progress")


class DummyTwilioClient:
    def __init__(self, sid_captured: list[str], args_captured: list[dict]):
        self.sid_captured = sid_captured
        self.args_captured = args_captured

    def calls(self, sid: str):
        # record the sid used
        self.sid_captured.append(sid)
        return DummyTwilioCallUpdater(self.sid_captured, self.args_captured)


@pytest.fixture(autouse=True)
def restore_settings():
    # Save original values and restore after
    original = {
        "account_sid": settings.knock_twilio_account_sid,
        "api_key": settings.knock_twilio_api_key,
        "api_secret": settings.knock_twilio_api_secret,
        "internal_api_url": settings.knock_internal_api_url,
    }
    yield
    settings.knock_twilio_account_sid = original["account_sid"]
    settings.knock_twilio_api_key = original["api_key"]
    settings.knock_twilio_api_secret = original["api_secret"]
    settings.knock_internal_api_url = original["internal_api_url"]


@pytest.mark.asyncio
async def test_build_transfer_twiml_contains_redirect_and_say():
    base_url = "https://example.internal"
    twiml = tts._build_transfer_twiml(base_url)
    assert "<Response>" in twiml
    assert '<Pause length="1"/>' in twiml
    assert f'<Redirect method="POST">{base_url}/v1/relay/voice/clay/callback</Redirect>' in twiml


def test_build_url_replaces_path_and_adds_query():
    base = "https://api.internal"
    endpoint = "/v1/internal/residents/{resident_id}/activity"
    out = tts._build_url(base, endpoint, {"resident_id": 123}, {"a": 1, "b": "x"})
    assert out == "https://api.internal/v1/internal/residents/123/activity?a=1&b=x"


def test_build_transfer_payload_uses_manager_and_message():
    ctx = SimpleNamespace(
        context=SimpleNamespace(ask_request=SimpleNamespace(product_info=SimpleNamespace(resident_manager_id="mgr-9")))
    )
    payload = tts._build_transfer_payload(ctx, "Please transfer me")
    assert payload["type"] == "note"
    assert payload["manager_id"] == "mgr-9"
    assert "Transfer to human agent - reason: Please transfer me" == payload["message"]


@pytest.mark.parametrize(
    "account_sid,api_key,api_secret",
    [
        # Test all 7 combinations where at least one credential is missing
        ("", "SKxxxx", "shhhhh"),  # Only account_sid missing
        ("ACxxxx", "", "shhhhh"),  # Only api_key missing
        ("ACxxxx", "SKxxxx", ""),  # Only api_secret missing
        ("", "", "shhhhh"),  # account_sid and api_key missing
        ("", "SKxxxx", ""),  # account_sid and api_secret missing
        ("ACxxxx", "", ""),  # api_key and api_secret missing
        ("", "", ""),  # All three missing
    ],
)
def test_get_twilio_credentials_missing_raises(account_sid, api_key, api_secret):
    settings.knock_twilio_account_sid = account_sid
    settings.knock_twilio_api_key = api_key
    settings.knock_twilio_api_secret = api_secret
    with pytest.raises(ValueError):
        get_twilio_credentials()


def test_get_twilio_credentials_returns_tuple():
    settings.knock_twilio_account_sid = "ACxxxx"
    settings.knock_twilio_api_key = "SKxxxx"
    settings.knock_twilio_api_secret = "shhhhh"
    api_key, api_secret, account_sid = get_twilio_credentials()
    assert api_key == "SKxxxx"
    assert api_secret == "shhhhh"
    assert account_sid == "ACxxxx"


@pytest.mark.asyncio
async def test_post_to_knock_posts_with_bearer_header(monkeypatch):
    # Arrange
    async def fake_get_token():
        return "tok-123"

    # capture the last request
    captured = {"headers": None, "json": None, "url": None}

    class DummyResponse:
        def __init__(self):
            self._json = {"ok": True}

        async def json(self):
            return self._json

    class DummySession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json

            class _Ctx:
                async def __aenter__(self_inner):
                    return DummyResponse()

                async def __aexit__(self_inner, exc_type, exc, tb):
                    return False

            return _Ctx()

    # Patch aiohttp and token
    monkeypatch.setattr(tts, "get_knock_mcp_auth_token", fake_get_token)
    monkeypatch.setattr(tts.aiohttp, "ClientSession", DummySession)

    # Act
    result = await tts._post_to_knock(url="https://internal/v1/path", data={"x": 1})

    # Assert
    assert result == {"ok": True}
    assert captured["url"] == "https://internal/v1/path"
    assert captured["json"] == {"x": 1}
    assert captured["headers"]["Internal-Authorization"] == "Bearer tok-123"


@pytest.mark.asyncio
async def test_transfer_twilio_call_updates_call(monkeypatch):
    # Arrange: fake context and Twilio client
    settings.knock_twilio_account_sid = "ACxxxx"
    settings.knock_twilio_api_key = "SKxxxx"
    settings.knock_twilio_api_secret = "shhhhh"

    ctx = SimpleNamespace(
        context=SimpleNamespace(
            ask_request=SimpleNamespace(product_info=SimpleNamespace(call_sid="CA123", knock_resident_id="R1"))
        )
    )

    sid_used: list[str] = []
    args_used: list[dict] = []

    # Patch TwilioClient constructor used in module to our dummy implementation
    def fake_twilio_client(api_key: str, api_secret: str, account_sid: str):
        return DummyTwilioClient(sid_used, args_used)

    monkeypatch.setattr(tts, "TwilioClient", fake_twilio_client)

    # Do not actually sleep during tests
    async def _noop_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    # Act
    await tts._transfer_twilio_call(ctx, base_url="https://example.internal")

    # Assert: call SID used and proper args passed to update
    assert sid_used == ["CA123"]
    assert len(args_used) == 1
    kwargs = args_used[0]
    assert "twiml" in kwargs and "<Response>" in kwargs["twiml"]
    assert kwargs["status_callback"] == "https://example.internal/v1/relay/voice/clay/callback"


@pytest.mark.asyncio
@pytest.mark.parametrize("call_sid", [None, ""])
async def test_transfer_twilio_call_raises_when_call_sid_missing(call_sid):
    """Test that _transfer_twilio_call raises ValueError when call_sid is missing."""
    ctx = SimpleNamespace(
        context=SimpleNamespace(ask_request=SimpleNamespace(product_info=SimpleNamespace(call_sid=call_sid)))
    )

    with pytest.raises(ValueError, match="Cannot transfer call: call_sid is not available"):
        await tts._transfer_twilio_call(ctx, base_url="https://example.internal")


@pytest.mark.asyncio
async def test_post_to_knock_network_error(monkeypatch):
    """Test that network errors in API calls are properly propagated."""

    async def fake_get_token():
        return "tok-123"

    class FailingSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, headers=None, json=None):
            raise Exception("Network error")

    monkeypatch.setattr(tts, "get_knock_mcp_auth_token", fake_get_token)
    monkeypatch.setattr(tts.aiohttp, "ClientSession", FailingSession)

    # Act & Assert
    with pytest.raises(Exception, match="Network error"):
        await tts._post_to_knock(url="https://internal/v1/path", data={"x": 1})


@pytest.mark.asyncio
async def test_make_transfer_to_staff_api_call_builds_correct_payload(monkeypatch):
    """Test that the API call is made with correct payload and URL."""
    # Setup
    settings.knock_internal_api_url = "https://internal.api"

    ctx = SimpleNamespace(
        context=SimpleNamespace(
            ask_request=SimpleNamespace(
                product_info=SimpleNamespace(knock_resident_id="R999", resident_manager_id="MGR456")
            )
        )
    )

    # Capture the API call parameters
    captured_call = None

    async def fake_post_to_knock(url, data):
        nonlocal captured_call
        captured_call = {"url": url, "data": data}
        return {"success": True}

    monkeypatch.setattr(tts, "_post_to_knock", fake_post_to_knock)

    # Act
    await tts._make_transfer_to_staff_api_call(ctx, "https://internal.api", "Test message")

    # Assert that the API call was made with correct parameters
    assert captured_call is not None
    assert captured_call["url"] == "https://internal.api/v1/internal/residents/R999/activity"
    assert captured_call["data"]["type"] == "note"
    assert captured_call["data"]["manager_id"] == "MGR456"
    assert "Transfer to human agent - reason: Test message" == captured_call["data"]["message"]


def test_build_url_no_query_params():
    """Test URL building without query parameters."""
    result = tts._build_url(
        base_url="https://api.test",
        endpoint="/users/{user_id}/posts",
        path_params={"user_id": "123"},
    )
    assert result == "https://api.test/users/123/posts"


def test_build_url_empty_path_params():
    """Test URL building with empty path parameters."""
    result = tts._build_url(
        base_url="https://api.test",
        endpoint="/static/path",
        path_params={},
        query_params={"filter": "active"},
    )
    assert result == "https://api.test/static/path?filter=active"


# ============================================================================
# TEXT TRANSFER TESTS
# ============================================================================


def test_execute_handoff_sms_returns_correct_response():
    """Test that _execute_handoff_sms returns the correct response model with proper metadata."""
    # Arrange
    transfer_message = "User needs help with lease application"
    resp_model = AskResponse(content=AskContent(chat="Test response"), metadata={})

    # Act
    result_model = ttt._execute_handoff_sms(transfer_message, resp_model)

    # Assert
    assert result_model is resp_model  # Should return the same model object
    assert result_model.metadata["human_handoff"] is True
    assert result_model.metadata["human_hand_off_message"] == transfer_message


def test_execute_handoff_sms_preserves_existing_metadata():
    """Test that _execute_handoff_sms preserves existing metadata while adding handoff info."""
    # Arrange
    original_content = AskContent(chat="Original content")
    original_metadata = {"existing_key": "existing_value"}
    transfer_message = "Transfer reason"

    resp_model = AskResponse(content=original_content, metadata=original_metadata.copy())

    # Act
    result_model = ttt._execute_handoff_sms(transfer_message, resp_model)

    # Assert
    assert result_model.content == original_content
    assert result_model.metadata["existing_key"] == "existing_value"
    assert result_model.metadata["human_handoff"] is True
    assert result_model.metadata["human_hand_off_message"] == transfer_message


def test_execute_handoff_email_adds_proper_metadata():
    """Test that _execute_handoff_email adds proper handoff metadata including email_route_back."""
    # Arrange
    transfer_message = "Email transfer test"
    resp_model = AskResponse(content=AskContent(chat="Test"), metadata={})

    # Act
    result_model = ttt._execute_handoff_email(transfer_message, resp_model)

    # Assert
    assert result_model is resp_model
    assert result_model.metadata["email_route_back"] is True
    assert result_model.metadata["human_handoff"] is True
    assert result_model.metadata["human_hand_off_message"] == transfer_message


def test_execute_handoff_chat_adds_metadata():
    """Test that _execute_handoff_chat adds handoff metadata to response."""
    # Arrange
    transfer_message = "Chat transfer test message"
    resp_model = AskResponse(content=AskContent(chat="Test"), metadata={})

    # Act
    result_model = ttt._execute_handoff_chat(transfer_message, resp_model)

    # Assert
    assert result_model is resp_model
    assert result_model.metadata["human_handoff"] is True
    assert result_model.metadata["human_hand_off_message"] == transfer_message


def test_execute_handoff_chat_preserves_existing_metadata():
    """Test that _execute_handoff_chat preserves existing metadata while adding handoff info."""
    # Arrange
    transfer_message = "Chat transfer"
    existing_metadata = {"existing_key": "existing_value", "another_key": 123}
    resp_model = AskResponse(content=AskContent(chat="Test"), metadata=existing_metadata.copy())

    # Act
    result_model = ttt._execute_handoff_chat(transfer_message, resp_model)

    # Assert
    assert result_model.metadata["existing_key"] == "existing_value"
    assert result_model.metadata["another_key"] == 123
    assert result_model.metadata["human_handoff"] is True
    assert result_model.metadata["human_hand_off_message"] == transfer_message


@pytest.mark.parametrize(
    "channel,expected_metadata",
    [
        (
            Channel.SMS,
            {"human_handoff": True, "human_hand_off_message": "Test transfer message"},
        ),
        (
            Channel.EMAIL,
            {
                "email_route_back": True,
                "human_handoff": True,
                "human_hand_off_message": "Test transfer message",
            },
        ),
        (
            Channel.CHAT,
            {"human_handoff": True, "human_hand_off_message": "Test transfer message"},
        ),
    ],
)
def test_execute_handoff_channel_routing(channel, expected_metadata):
    """Test that execute_handoff routes to correct handler based on channel."""
    # Arrange
    transfer_message = "Test transfer message"
    resp_model = AskResponse(content=AskContent(chat="Test"), metadata={})

    # Act
    result_model = ttt.execute_handoff(channel, transfer_message, resp_model)

    # Assert
    assert result_model is resp_model
    assert result_model.metadata == expected_metadata


def test_execute_handoff_unsupported_channel_raises_error():
    """Test that execute_handoff raises ValueError for unsupported channels."""
    # Arrange
    transfer_message = "Test message"
    resp_model = AskResponse(content=AskContent(chat="Test"), metadata={})

    # Act & Assert
    with pytest.raises(ValueError, match="Unsupported channel"):
        ttt.execute_handoff("UNSUPPORTED_CHANNEL", transfer_message, resp_model)


def test_execute_handoff_different_transfer_messages():
    """Test that all handoff functions properly handle different transfer messages."""
    # Arrange
    test_cases = [
        ("", Channel.SMS),
        ("Simple message", Channel.EMAIL),
        ("Complex message with special chars: !@#$%", Channel.CHAT),
        ("Very long message " * 20, Channel.SMS),
    ]

    for transfer_message, channel in test_cases:
        resp_model = AskResponse(content=AskContent(chat="Test"), metadata={})

        # Act
        result_model = ttt.execute_handoff(channel, transfer_message, resp_model)

        # Assert
        assert result_model.metadata["human_handoff"] is True
        assert result_model.metadata["human_hand_off_message"] == transfer_message
        if channel == Channel.EMAIL:
            assert result_model.metadata["email_route_back"] is True


# ============================================================================
# AI PAUSE TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_transfer_to_staff_text_writes_handoff_to_redis_for_email(ask_request_resident_email_ll, monkeypatch):
    """Test that transfer_to_staff_text writes handoff state to Redis for EMAIL channel."""
    import json

    from agents import RunContextWrapper
    from agents.tool_context import ToolContext
    from openai.types.responses import ResponseFunctionToolCall

    from agent_leasing.models.context import SessionScope

    # Arrange
    context = SessionScope(
        ask_request=ask_request_resident_email_ll,
        thread_id="test-thread-123",
    )

    tool_call = ResponseFunctionToolCall(
        arguments="{}",
        call_id="test-call-id",
        name="transfer_to_staff_text",
        type="function_call",
    )
    mock_tool_ctx = ToolContext.from_agent_context(
        RunContextWrapper(context=context),
        tool_call_id="test-call-id",
        tool_call=tool_call,
    )

    # Capture Redis put call
    put_calls = []

    async def mock_put(key, value, expire=None):
        put_calls.append({"key": key, "value": value, "expire": expire})

    monkeypatch.setattr(ttt, "put", mock_put)

    json_input = json.dumps(
        {
            "repeated_handoff_attempt": False,
            "sufficient_summary_information": True,
            "user_refused_to_provide_summary": False,
            "transfer_message": "Test transfer message",
            "user_confirmation": True,
        }
    )

    # Act
    await ttt.transfer_to_staff_text.on_invoke_tool(mock_tool_ctx, json_input)

    # Assert
    assert context.handoff is True
    assert len(put_calls) == 1
    assert "agent-leasing:" in put_calls[0]["key"]
    assert put_calls[0]["value"]["transferred"] is True
    assert "handoff_time" in put_calls[0]["value"]
    assert put_calls[0]["expire"] == settings.handoff_inactivity_ttl


@pytest.mark.asyncio
async def test_transfer_to_staff_text_writes_handoff_to_redis_for_sms(ask_request_resident_sms_ll, monkeypatch):
    """KNCK-39301: Missing Knock IDs fall back to ab_resident_id in Redis.

    The SMS LL fixture has knock_resident_id=null but includes ab_resident_id,
    so the key should stay resident-scoped instead of falling back to session state.
    """
    import json

    from agents import RunContextWrapper
    from agents.tool_context import ToolContext
    from openai.types.responses import ResponseFunctionToolCall

    from agent_leasing.models.context import SessionScope

    # Arrange
    context = SessionScope(
        ask_request=ask_request_resident_sms_ll,
        thread_id="test-thread-123",
    )

    tool_call = ResponseFunctionToolCall(
        arguments="{}",
        call_id="test-call-id",
        name="transfer_to_staff_text",
        type="function_call",
    )
    mock_tool_ctx = ToolContext.from_agent_context(
        RunContextWrapper(context=context),
        tool_call_id="test-call-id",
        tool_call=tool_call,
    )

    # Capture Redis put call
    put_calls = []

    async def mock_put(key, value, expire=None):
        put_calls.append({"key": key, "value": value, "expire": expire})

    monkeypatch.setattr(ttt, "put", mock_put)

    json_input = json.dumps(
        {
            "repeated_handoff_attempt": False,
            "sufficient_summary_information": True,
            "user_refused_to_provide_summary": False,
            "transfer_message": "Test transfer message",
            "user_confirmation": True,
        }
    )

    # Act
    await ttt.transfer_to_staff_text.on_invoke_tool(mock_tool_ctx, json_input)

    # Assert
    assert context.handoff is True
    assert len(put_calls) == 1
    assert put_calls[0]["key"] == get_handoff_key(
        ask_request_resident_sms_ll.product,
        ask_request_resident_sms_ll.product_info.knock_property_id,
        ask_request_resident_sms_ll.product_info.knock_resident_id,
        getattr(ask_request_resident_sms_ll.product_info.ab_resident_id, "id", None),
    )
    assert put_calls[0]["value"]["transferred"] is True
    assert "handoff_time" in put_calls[0]["value"]
    assert put_calls[0]["expire"] == settings.handoff_inactivity_ttl


@pytest.mark.asyncio
async def test_transfer_to_staff_text_does_not_write_handoff_for_chat(ask_request_resident_chat_ll, monkeypatch):
    """Test that transfer_to_staff_text does NOT write handoff state to Redis for CHAT channel."""
    import json

    from agents import RunContextWrapper
    from agents.tool_context import ToolContext
    from openai.types.responses import ResponseFunctionToolCall

    from agent_leasing.models.context import SessionScope

    # Arrange
    context = SessionScope(
        ask_request=ask_request_resident_chat_ll,
        thread_id="test-thread-123",
    )

    tool_call = ResponseFunctionToolCall(
        arguments="{}",
        call_id="test-call-id",
        name="transfer_to_staff_text",
        type="function_call",
    )
    mock_tool_ctx = ToolContext.from_agent_context(
        RunContextWrapper(context=context),
        tool_call_id="test-call-id",
        tool_call=tool_call,
    )

    # Capture Redis put call
    put_calls = []

    async def mock_put(key, value, expire=None):
        put_calls.append({"key": key, "value": value, "expire": expire})

    monkeypatch.setattr(ttt, "put", mock_put)

    json_input = json.dumps(
        {
            "repeated_handoff_attempt": False,
            "sufficient_summary_information": True,
            "user_refused_to_provide_summary": False,
            "transfer_message": "Test transfer message",
            "user_confirmation": True,
        }
    )

    # Act
    await ttt.transfer_to_staff_text.on_invoke_tool(mock_tool_ctx, json_input)

    # Assert
    assert context.handoff is True
    assert len(put_calls) == 0  # Should NOT write to Redis for CHAT
