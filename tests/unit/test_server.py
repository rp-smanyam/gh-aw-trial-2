import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request, WebSocket

from agent_leasing.agent.tools.transfer_to_staff.handoff import get_handoff_key
from agent_leasing.agent.util import AgentArchitecture, SessionScope, UnsupportedAgentException
from agent_leasing.api.model import (
    AskChatPayload,
    AskContent,
    AskResponse,
    EmailChat,
    Flow,
)
from agent_leasing.server import (
    FALLBACK_RESPONSE,
    URL_HANDOFF_RESPONSE,
    UrlHandoffResult,
    _build_response_model,
    _count_tasks_by_name,
    _get_external_hostname,
    _get_langsmith_project_name,
    _handle_active_handoff,
    _handle_url_transfer,
    _publish_responder_output_activities,
    handle_incoming_call_realtime,
    main,
)
from agent_leasing.services.agent_service import save_previous_response_id
from agent_leasing.services.analytics_service import add_metadata_into_context
from agent_leasing.services.input_sanitizers import URL_REPLACEMENT
from agent_leasing.services.telemetry_service import emit_metrics
from agent_leasing.settings import settings
from agent_leasing.twilio_handler import TwilioWebSocketManager
from agent_leasing.util.twilio_util import validate_twilio_request


class TestSavePreviousResponseId:
    """Test cases for the save_previous_response_id function."""

    @pytest.mark.asyncio
    async def test_save_previous_response_id_with_valid_id(self, ask_request_simple):
        """Test save_previous_response_id with a valid previous_response_id."""
        headers = {}
        context = SessionScope(ask_request=ask_request_simple)
        previous_response_id = "test_response_id_123"

        await save_previous_response_id(headers, context, previous_response_id)

        # Check that headers were updated with the previous response ID
        assert "X-OpenAI-Previous-Response-Id" in headers
        assert headers["X-OpenAI-Previous-Response-Id"] == previous_response_id

        # Check that context was updated with the previous response ID
        assert context.previous_response_id == previous_response_id

    @pytest.mark.asyncio
    async def test_save_previous_response_id_with_none(self, ask_request_simple):
        """Test save_previous_response_id when previous_response_id is None."""
        headers = {"existing_header": "existing_value"}
        context = SessionScope(ask_request=ask_request_simple)
        context.previous_response_id = "old_response_id"

        await save_previous_response_id(headers, context, None)

        # Check that headers were not modified
        assert "X-OpenAI-Previous-Response-Id" not in headers
        assert headers == {"existing_header": "existing_value"}

        # Check that context previous_response_id was not changed
        assert context.previous_response_id == "old_response_id"

    @pytest.mark.asyncio
    async def test_save_previous_response_id_with_empty_string(self, ask_request_simple):
        """Test save_previous_response_id when previous_response_id is empty string."""
        headers = {}
        context = SessionScope(ask_request=ask_request_simple)

        await save_previous_response_id(headers, context, "")

        # Check that headers were not modified (empty string is falsy)
        assert "X-OpenAI-Previous-Response-Id" not in headers

        # Check that context previous_response_id was not changed
        assert context.previous_response_id is None

    @pytest.mark.asyncio
    async def test_save_previous_response_id_updates_existing_headers(self, ask_request_simple):
        """Test save_previous_response_id updates existing headers correctly."""
        headers = {
            "Content-Type": "application/json",
            "X-Custom-Header": "custom_value",
        }
        context = SessionScope(ask_request=ask_request_simple)
        previous_response_id = "new_response_id_456"

        await save_previous_response_id(headers, context, previous_response_id)

        # Check that existing headers are preserved
        assert headers["Content-Type"] == "application/json"
        assert headers["X-Custom-Header"] == "custom_value"

        # Check that new header was added
        assert headers["X-OpenAI-Previous-Response-Id"] == previous_response_id

        # Check that context was updated
        assert context.previous_response_id == previous_response_id

    @pytest.mark.asyncio
    async def test_save_previous_response_id_overwrites_existing_header(self, ask_request_simple):
        """Test save_previous_response_id overwrites existing previous response ID header."""
        headers = {"X-OpenAI-Previous-Response-Id": "old_response_id"}
        context = SessionScope(ask_request=ask_request_simple)
        context.previous_response_id = "old_context_id"
        new_response_id = "new_response_id_789"

        await save_previous_response_id(headers, context, new_response_id)

        # Check that header was overwritten
        assert headers["X-OpenAI-Previous-Response-Id"] == new_response_id

        # Check that context was overwritten
        assert context.previous_response_id == new_response_id


class TestBuildResponseModel:
    """Test cases for the _build_response_model function."""

    def test_build_response_model(self, ask_request_simple):
        chat_payload = AskChatPayload(response="Test response").model_dump()
        workflow_name = "simple"
        flows = [Flow(name=workflow_name)]
        langsmith_trace_url = "https://example.com/trace/123"
        result = _build_response_model(ask_request_simple, chat_payload, flows, langsmith_trace_url)
        assert isinstance(result, AskResponse)
        assert result.langsmith_trace_url == "https://example.com/trace/123"
        assert isinstance(result.content, AskContent)
        assert result.metadata == {"executed_flow_names": ["Simple"]}
        assert result.flow_name == "SIMPLE"

    def test_build_response_model_with_different_payload(self, ask_request_simple):
        """Test _build_response_model with different payload structure."""
        chat_payload = AskChatPayload(
            response="Different response",
        ).model_dump()  # Convert to dict for JSON serialization
        flows = [Flow(name="test_flow")]
        langsmith_trace_url = "https://test.com/trace/456"

        result = _build_response_model(ask_request_simple, chat_payload, flows, langsmith_trace_url)

        assert isinstance(result, AskResponse)
        assert result.langsmith_trace_url == "https://test.com/trace/456"
        assert isinstance(result.content, AskContent)
        # AskContent.chat expects stringified JSON
        chat_data = json.loads(result.content.chat)
        assert chat_data["response"] == "Different response"
        assert result.metadata == {"executed_flow_names": ["Test Flow"]}
        assert result.flow_name == "TEST_FLOW"


class TestEmitMetrics:
    """Test cases for the emit_metrics function."""

    @patch("agent_leasing.services.telemetry_service.input_token_counter")
    @patch("agent_leasing.services.telemetry_service.output_token_counter")
    @pytest.mark.asyncio
    async def test_emit_metrics_with_usage_data(self, mock_output_counter, mock_input_counter):
        """Test emit_metrics with usage data in result."""
        # Create mock response with usage data
        mock_response = MagicMock()
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        mock_result = MagicMock()
        mock_result.raw_responses = [mock_response]

        await emit_metrics(mock_result, "test_session_123")

        mock_input_counter.add.assert_called_once_with(100, {"agent_leasing.session_id": "test_session_123"})
        mock_output_counter.add.assert_called_once_with(50, {"agent_leasing.session_id": "test_session_123"})

    @patch("agent_leasing.services.telemetry_service.input_token_counter")
    @patch("agent_leasing.services.telemetry_service.output_token_counter")
    @pytest.mark.asyncio
    async def test_emit_metrics_without_usage_data(self, mock_output_counter, mock_input_counter):
        """Test emit_metrics when result has no raw_responses."""
        mock_result = MagicMock()
        mock_result.raw_responses = None

        await emit_metrics(mock_result, "test_session_456")

        # Should add 0 tokens when no raw_responses
        mock_input_counter.add.assert_called_once_with(0, {"agent_leasing.session_id": "test_session_456"})
        mock_output_counter.add.assert_called_once_with(0, {"agent_leasing.session_id": "test_session_456"})


class TestTwilioWebSocketManager:
    """Test cases for the TwilioWebSocketManager class."""

    def test_twilio_websocket_manager_init(self):
        """Test TwilioWebSocketManager initialization."""
        manager = TwilioWebSocketManager()
        assert manager.active_handlers == {}

    @patch("agent_leasing.twilio_handler.TwilioHandler")
    @pytest.mark.asyncio
    async def test_twilio_websocket_manager_new_session(self, mock_twilio_handler_class):
        """Test TwilioWebSocketManager new_session method."""
        manager = TwilioWebSocketManager()
        mock_websocket = AsyncMock(spec=WebSocket)
        mock_handler = AsyncMock()
        mock_twilio_handler_class.return_value = mock_handler

        result = await manager.new_session(mock_websocket)

        # Verify TwilioHandler was created
        mock_twilio_handler_class.assert_called_once_with(mock_websocket)

        # Verify the returned handler is correct
        assert result == mock_handler

        # Note: The actual implementation doesn't call start() method
        # It just creates and returns the handler


class TestHandleIncomingCallRealtime:
    """Test cases for the handle_incoming_call_realtime function."""

    @patch("starlette.requests.Request.form", new_callable=AsyncMock)
    @patch("agent_leasing.server.validate_twilio_request", new_callable=AsyncMock)
    @patch("agent_leasing.server.settings")
    @pytest.mark.asyncio
    async def test_handle_incoming_call_realtime_success(
        self, mock_settings, mock_validate_twilio_request, mock_request_form
    ):
        """Test successful handling of incoming realtime call."""
        mock_settings.server_host = "test.example.com"
        mock_settings.server_port = 8080

        mock_request = MagicMock(spec=Request)

        result = await handle_incoming_call_realtime(mock_request)

        # Should return a PlainTextResponse with TwiML content
        from starlette.responses import PlainTextResponse

        assert isinstance(result, PlainTextResponse)

        # Check that it contains TwiML content
        assert result.media_type == "text/xml"
        # The content should contain TwiML elements
        twiml_content = result.body.decode()
        assert "<Response>" in twiml_content
        assert "<Connect>" in twiml_content
        assert "<Stream" in twiml_content
        # Note: The actual implementation uses a hardcoded host, not the mocked settings


class TestHealthEndpoints:
    """Test cases for health and status endpoints."""

    def test_health_endpoint(self, client):
        """Test the healthcheck endpoint."""
        response = client.get("/healthcheck")
        assert response.status_code == 200
        # The actual healthcheck returns mcp_servers and message
        response_data = response.json()
        assert "message" in response_data
        assert response_data["message"] == "OK"

    def test_status_endpoint(self, client):
        """Test the status endpoint."""
        response = client.get("/status")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"


class TestValidationExceptionHandler:
    """Test cases for the validation_exception_handler."""

    def test_missing_required_field_returns_422(self, client):
        """Test that missing required field returns 422 with RFC 9457 Problem Details."""
        response = client.post(
            "/v1/agent/ask",
            json={
                "prompt": "Hello",
                "property_id": "123",
                # Missing required 'product' field
            },
        )
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        data = response.json()
        # RFC 9457 required fields
        assert data["type"] == "about:blank"
        assert data["title"] == "Validation Error"
        assert data["status"] == 422
        assert data["detail"] == "Request validation failed"
        assert data["instance"] == "/v1/agent/ask"
        # Extension field with validation errors
        errors = data["errors"]
        assert any(error["loc"][-1] == "product" for error in errors)
        assert any(error["type"] == "missing" for error in errors)

    def test_invalid_enum_value_returns_422(self, client):
        """Test that invalid enum value returns 422 with error details."""
        response = client.post(
            "/v1/agent/ask",
            json={
                "prompt": "Hello",
                "property_id": "123",
                "product": "invalid_product_name",
            },
        )
        assert response.status_code == 422
        data = response.json()
        assert data["title"] == "Validation Error"
        errors = data["errors"]
        assert any("product" in str(error["loc"]) for error in errors)

    def test_invalid_json_returns_422(self, client):
        """Test that invalid JSON returns 422."""
        response = client.post(
            "/v1/agent/ask",
            content="not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422

    def test_empty_body_returns_422(self, client):
        """Test that empty request body returns 422."""
        response = client.post(
            "/v1/agent/ask",
            json={},
        )
        assert response.status_code == 422
        data = response.json()
        assert data["title"] == "Validation Error"
        assert "errors" in data

    @patch("agent_leasing.server.logger")
    def test_validation_error_is_logged(self, mock_logger, client):
        """Test that validation errors are logged with structlog."""
        response = client.post(
            "/v1/agent/ask",
            json={"prompt": "Hello"},  # Missing required fields
        )
        assert response.status_code == 422
        mock_logger.warning.assert_called_once()
        call_kwargs = mock_logger.warning.call_args
        assert call_kwargs[0][0] == "Request validation error"
        assert "path" in call_kwargs[1]
        assert "method" in call_kwargs[1]
        assert "errors" in call_kwargs[1]
        assert call_kwargs[1]["path"] == "/v1/agent/ask"
        assert call_kwargs[1]["method"] == "POST"

    def test_resident_persona_missing_fields_returns_422(self, client):
        """Test that resident product with missing required fields returns 422."""
        response = client.post(
            "/v1/agent/ask",
            json={
                "prompt": "Hello",
                "product": "resident_one_chat",
                "product_info": {
                    "knock_property_id": "123",
                    # Missing required resident fields
                },
            },
        )
        assert response.status_code == 422
        data = response.json()
        assert data["title"] == "Validation Error"
        # The error should mention missing resident fields
        error_str = str(data["errors"])
        assert "resident persona" in error_str.lower() or "value_error" in error_str.lower()


class TestHttpExceptionHandler:
    """Test cases for the http_exception_handler."""

    @pytest.mark.asyncio
    async def test_http_exception_returns_rfc9457_format(self):
        """Test that HTTPException returns RFC 9457 Problem Details format."""
        from agent_leasing.server import http_exception_handler

        mock_request = MagicMock()
        mock_request.url.path = "/v1/agent/ask"
        exc = HTTPException(status_code=404, detail="Not Found")

        response = await http_exception_handler(mock_request, exc)

        assert response.status_code == 404
        assert response.media_type == "application/problem+json"
        data = json.loads(response.body)
        assert data["type"] == "about:blank"
        assert data["title"] == "Error"
        assert data["status"] == 404
        assert data["detail"] == "Not Found"
        assert data["instance"] == "/v1/agent/ask"

    @pytest.mark.asyncio
    @patch("agent_leasing.server.logger")
    async def test_4xx_error_logs_warning(self, mock_logger):
        """Test that 4xx errors are logged as warnings."""
        from agent_leasing.server import http_exception_handler

        mock_request = MagicMock()
        mock_request.url.path = "/test"
        exc = HTTPException(status_code=404, detail="Not Found")

        await http_exception_handler(mock_request, exc)

        mock_logger.warning.assert_called_once()
        call_kwargs = mock_logger.warning.call_args
        assert call_kwargs[0][0] == "HTTP exception"
        assert call_kwargs[1]["status_code"] == 404

    @pytest.mark.asyncio
    @patch("agent_leasing.server.logger")
    async def test_5xx_error_logs_error(self, mock_logger):
        """Test that 5xx errors are logged as errors."""
        from agent_leasing.server import http_exception_handler

        mock_request = MagicMock()
        mock_request.url.path = "/test"
        exc = HTTPException(status_code=500, detail="Server error")

        await http_exception_handler(mock_request, exc)

        mock_logger.error.assert_called_once()


class TestSmsConsentGateServer:
    """Test cases for SMS consent behavior in the server endpoint."""

    @patch("agent_leasing.server.ls.trace")
    @patch("agent_leasing.server.memory.put_context", new_callable=AsyncMock)
    @patch("agent_leasing.server.save_previous_response_id")
    @patch("agent_leasing.server.log_conversation_exchange")
    @patch("agent_leasing.server.emit_metrics")
    @patch("agent_leasing.server.log_internal_messages")
    @patch("agent_leasing.server.add_metadata_into_context")
    @patch("agent_leasing.server.Runner.run", new_callable=AsyncMock)
    @patch("agent_leasing.server.handle_sms_consent_gate", new_callable=AsyncMock)
    @patch("agent_leasing.server.build_agent_request", new_callable=AsyncMock)
    def test_airr_source_skips_sms_consent_gate(
        self,
        mock_build_agent_request,
        mock_handle_sms_consent_gate,
        mock_runner_run,
        mock_add_metadata,
        mock_log_internal_messages,
        mock_emit_metrics,
        mock_log_conversation_exchange,
        mock_save_previous_response_id,
        mock_put_context,
        mock_ls_trace,
        client,
        ask_request_resident_sms_knck,
    ):
        req = ask_request_resident_sms_knck
        req.product_info.source = "AIRR"
        req.prompt = "Hello"

        context = SessionScope(ask_request=req)
        context.langsmith_run_tree = {}

        class DummyAgent:
            def __init__(self):
                self.agent_instance = object()
                self.mcp_servers = {}
                self.agent_architecture = AgentArchitecture.SINGLE_AGENT
                self.name = "dummy-agent"

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

        dummy_result = SimpleNamespace(
            final_output="OK",
            context_wrapper=SimpleNamespace(context=SimpleNamespace(logging_metadata={})),
            last_response_id=None,
        )

        mock_runner_run.return_value = dummy_result
        mock_build_agent_request.return_value = SimpleNamespace(
            trace_id="trace_test_123",
            language_code="en",
            workflow_name="workflow",
            flows=[Flow(name="test_flow")],
            logging_metadata=[],
            group_id="group",
            headers={},
            context=context,
            thread_id="thread",
            previous_response_id=None,
            agent=DummyAgent(),
            metadata={},
            root_run=MagicMock(),
            langsmith_trace_url=None,
            human_message=MagicMock(),
            llm_message=MagicMock(),
            start_time=0.0,
            expire="10m",
        )

        mock_run = MagicMock()
        mock_run.get_url.return_value = "https://smith.langchain.com/o/3f19e100/projects/p/5170510d/r/65b25b0f"
        mock_ls_trace.return_value.__enter__.return_value = mock_run

        response = client.post("/v1/agent/ask", json=req.model_dump())

        assert response.status_code == 200
        mock_handle_sms_consent_gate.assert_not_called()
        mock_runner_run.assert_called_once()

    @patch("agent_leasing.server.ls.trace")
    @patch("agent_leasing.server.memory.put_context", new_callable=AsyncMock)
    @patch("agent_leasing.server.save_previous_response_id")
    @patch("agent_leasing.server.log_conversation_exchange")
    @patch("agent_leasing.server.emit_metrics")
    @patch("agent_leasing.server.log_internal_messages")
    @patch("agent_leasing.server.add_metadata_into_context")
    @patch("agent_leasing.server.Runner.run", new_callable=AsyncMock)
    @patch("agent_leasing.server.handle_sms_consent_gate", new_callable=AsyncMock)
    @patch("agent_leasing.server.build_agent_request", new_callable=AsyncMock)
    def test_non_airr_calls_sms_consent_gate_and_runs_agent(
        self,
        mock_build_agent_request,
        mock_handle_sms_consent_gate,
        mock_runner_run,
        mock_add_metadata,
        mock_log_internal_messages,
        mock_emit_metrics,
        mock_log_conversation_exchange,
        mock_save_previous_response_id,
        mock_put_context,
        mock_ls_trace,
        client,
        ask_request_resident_sms_knck,
    ):
        from agent_leasing.util.sms_consent import GateResult

        req = ask_request_resident_sms_knck
        req.product_info.source = "KNCK"
        req.prompt = "STOP"
        mock_handle_sms_consent_gate.return_value = GateResult(action="proceed")

        context = SessionScope(ask_request=req)
        context.langsmith_run_tree = {}

        class DummyAgent:
            def __init__(self):
                self.agent_instance = object()
                self.mcp_servers = {"knock_mcp_server": AsyncMock()}
                self.agent_architecture = AgentArchitecture.SINGLE_AGENT
                self.name = "dummy-agent"

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

        dummy_result = SimpleNamespace(
            final_output="OK",
            context_wrapper=SimpleNamespace(context=SimpleNamespace(logging_metadata={})),
            last_response_id=None,
        )

        mock_runner_run.return_value = dummy_result
        mock_build_agent_request.return_value = SimpleNamespace(
            trace_id="trace_test_123",
            language_code="en",
            workflow_name="workflow",
            flows=[Flow(name="test_flow")],
            logging_metadata=[],
            group_id="group",
            headers={},
            context=context,
            thread_id="thread",
            previous_response_id=None,
            agent=DummyAgent(),
            metadata={},
            root_run=MagicMock(),
            langsmith_trace_url=None,
            human_message=MagicMock(),
            llm_message=MagicMock(),
            start_time=0.0,
            expire="10m",
        )

        mock_run = MagicMock()
        mock_run.get_url.return_value = "https://smith.langchain.com/o/3f19e100/projects/p/5170510d/r/65b25b0f"
        mock_ls_trace.return_value.__enter__.return_value = mock_run

        response = client.post("/v1/agent/ask", json=req.model_dump())

        assert response.status_code == 200
        mock_handle_sms_consent_gate.assert_called_once()
        mock_runner_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_http_exception_preserves_headers(self):
        """Test that custom headers from HTTPException are preserved."""
        from agent_leasing.server import http_exception_handler

        mock_request = MagicMock()
        mock_request.url.path = "/test"
        exc = HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )

        response = await http_exception_handler(mock_request, exc)

        assert response.headers.get("WWW-Authenticate") == "Bearer"


class TestTwilioSecurity:
    @pytest.mark.parametrize(
        "req,environment,external_hostname",
        [
            (
                Request({"type": "http", "headers": {}, "path": "/"}),
                "local",
                "localhost",
            ),
            (
                Request({"type": "http", "headers": {}, "path": "http://localhost/test"}),
                "dev",
                "localhost",
            ),
            (
                Request({"type": "http", "headers": {}, "path": "http://localhost/test"}),
                "prod",
                "agent-leasing-voice.knockcrm.com",
            ),
            (
                Request({"type": "http", "headers": {}, "path": "http://localhost/test"}),
                "alpha",
                "alpha-agent-leasing-voice.knocktest.com",
            ),
            (
                Request({"type": "http", "headers": {}, "path": "http://localhost/test"}),
                "beta",
                "beta-agent-leasing-voice.knocktest.com",
            ),
        ],
    )
    @pytest.mark.asyncio
    def test_get_external_hostname_http(self, req, environment, external_hostname):
        settings.environment = environment
        assert external_hostname == _get_external_hostname(request=req)

    @pytest.mark.parametrize(
        "req,environment,external_hostname",
        [
            (
                WebSocket(
                    {"type": "websocket", "headers": {}, "path": "/ws"},
                    receive=MagicMock(),
                    send=MagicMock(),
                ),
                "local",
                "localhost",
            ),
            (
                WebSocket(
                    {"type": "websocket", "headers": {}, "path": "ws://whatever/test"},
                    receive=MagicMock(),
                    send=MagicMock(),
                ),
                "prod",
                "agent-leasing-voice.knockcrm.com",
            ),
            (
                WebSocket(
                    {"type": "websocket", "headers": {}, "path": "ws://whatever/test"},
                    receive=MagicMock(),
                    send=MagicMock(),
                ),
                "alpha",
                "alpha-agent-leasing-voice.knocktest.com",
            ),
            (
                WebSocket(
                    {"type": "websocket", "headers": {}, "path": "ws://whatever/test"},
                    receive=MagicMock(),
                    send=MagicMock(),
                ),
                "beta",
                "beta-agent-leasing-voice.knocktest.com",
            ),
        ],
    )
    @pytest.mark.asyncio
    def test_get_external_hostname_websocket(self, req, environment, external_hostname):
        settings.environment = environment
        assert external_hostname == _get_external_hostname(request=req)

    async def test_validate_twilio_request(self):
        """Test that RequestValidator is called properly."""
        settings.twilio_auth_token = "12345"
        params = {
            "CallSid": "CA1234567890ABCDE",
            "Digits": "1234",
            "From": "+14158675309",
            "To": "+18005551212",
            "Caller": "+14158675309",
        }
        await validate_twilio_request(
            "https://mycompany.com/myapp.php?foo=1&bar=2",
            params,
            "RSOYDt4T1cUTdK1PDd93/VVr8B8=",
        )

    async def test_validate_twilio_request_invalid(self):
        """Test that validate_twilio_request throws exception when signature is invalid."""
        settings.environment = "prod"  # Set to prod so validation isn't bypassed
        settings.twilio_auth_token = "12345"
        params = {
            "CallSid": "CA1234567890ABCDE",
            "Digits": "1234",
            "From": "+14158675309",
            "To": "+18005551212",
            "Caller": "+14158675309",
        }
        with pytest.raises(HTTPException) as exc_info:
            await validate_twilio_request("https://mycompany.com/myapp.php?foo=1&bar=2", params, "wrong")
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Invalid Twilio Signature"


class Test_AddMetadataIntoContext:
    def test_add_metadata_into_context_with_valid_input(self, resident_context_unified_chat_ll):
        result = MagicMock()
        result.to_input_list.return_value = [
            {
                "content": "can you create me a service request for my faucet in my kitchen, it's leaking from the base. no other info",
                "role": "user",
            },
            {"id": "rs_1", "summary": [], "type": "reasoning"},
            {
                "arguments": '{"pmc_id":123,"site_id":456,"resident_household_id":789,"resident_member_id":1,"emergency":false,"chat_summary":"Resident reports that the kitchen faucet is leaking from the base. Please inspect and repair the faucet to stop the leak."}',
                "call_id": "call_1",
                "name": "create_service_request",
                "type": "function_call",
                "id": "fc_1234",
                "status": "completed",
            },
            {
                "arguments": '{"link_type":"single_service_request"}',
                "call_id": "call_2",
                "name": "create_link",
                "type": "function_call",
                "id": "fc_12345",
                "status": "completed",
            },
            {
                "call_id": "call_1",
                "output": '{"type":"text","text":"{\\"service_request_id\\":123,\\"service_request_created\\":true,\\"priority_number\\":\\"3\\",\\"priority_name\\":\\"Standard\\",\\"agent_response\\":\\"Service request created successfully.\\"}","annotations":null,"meta":null}',
                "type": "function_call_output",
            },
            {
                "call_id": "call_2",
                "output": "https://cassidysouth.qa1.loftliving.com/portal/mr/detail/mrId",
                "type": "function_call_output",
            },
            {"id": "rs_2", "summary": [], "type": "reasoning"},
            {
                "id": "msg_1",
                "content": [
                    {
                        "annotations": [],
                        "text": '{"response":"I’ve created a maintenance request for your leaking kitchen faucet.\\n\\n- Service Request ID: 123  \\n- Priority: 3 – Standard  \\n- Details: Kitchen faucet leaking from the base; technician will inspect and repair.\\n\\nIt may take a short time for this to appear in your online portal.\\n\\nYou can view the request here:\\n[View service request](https://cassidysouth.qa1.loftliving.com/portal/mr/detail/mrId)","language_code":"en","workflow_codes":["facilities_flow"]}',
                        "type": "output_text",
                        "logprobs": [],
                    }
                ],
                "role": "assistant",
                "status": "completed",
                "type": "message",
            },
        ]

        add_metadata_into_context(resident_context_unified_chat_ll, result)

        expected_metadata = {
            "call_1": {"service_request": ["create_service_request", {"created": True, "sr_id": 123}]}
        }
        resident_context_unified_chat_ll.logging_metadata == expected_metadata

    def test_add_metadata_into_context_with_valid_input_with_user_input(self, resident_context_unified_chat_ll):
        result = MagicMock()
        result.to_input_list.return_value = [
            {
                "content": "can you create me a service request for my faucet in my kitchen, it's leaking from the base. no other info",
                "role": "user",
            },
            {"id": "rs_1", "summary": [], "type": "reasoning"},
            {
                "arguments": '{"pmc_id":123,"site_id":456,"resident_household_id":789,"resident_member_id":1,"emergency":false,"chat_summary":"Resident reports that the kitchen faucet is leaking from the base. Please inspect and repair the faucet to stop the leak."}',
                "call_id": "call_1",
                "name": "create_service_request",
                "type": "function_call",
                "id": "fc_1234",
                "status": "completed",
            },
            {
                "arguments": '{"link_type":"single_service_request"}',
                "call_id": "call_2",
                "name": "create_link",
                "type": "function_call",
                "id": "fc_12345",
                "status": "completed",
            },
            {
                "call_id": "call_1",
                "output": '{"type":"text","text":"{\\"service_request_id\\":123,\\"service_request_created\\":true,\\"priority_number\\":\\"3\\",\\"priority_name\\":\\"Standard\\",\\"agent_response\\":\\"Service request created successfully.\\"}","annotations":null,"meta":null}',
                "type": "function_call_output",
            },
            {
                "call_id": "call_2",
                "output": "https://cassidysouth.qa1.loftliving.com/portal/mr/detail/mrId",
                "type": "function_call_output",
            },
            {"id": "rs_2", "summary": [], "type": "reasoning"},
            {
                "id": "msg_1",
                "content": [
                    {
                        "annotations": [],
                        "text": '{"response":"I’ve created a maintenance request for your leaking kitchen faucet.\\n\\n- Service Request ID: 123  \\n- Priority: 3 – Standard  \\n- Details: Kitchen faucet leaking from the base; technician will inspect and repair.\\n\\nIt may take a short time for this to appear in your online portal.\\n\\nYou can view the request here:\\n[View service request](https://cassidysouth.qa1.loftliving.com/portal/mr/detail/mrId)","language_code":"en","workflow_codes":["facilities_flow"]}',
                        "type": "output_text",
                        "logprobs": [],
                    }
                ],
                "role": "assistant",
                "status": "completed",
                "type": "message",
            },
        ]

        add_metadata_into_context(resident_context_unified_chat_ll, result, user_input="create service request")

        expected_metadata = {
            "create service request": {"service_request": ["create_service_request", {"created": True, "sr_id": 123}]}
        }
        resident_context_unified_chat_ll.logging_metadata == expected_metadata

    def test_add_metadata_into_context_with_valid_input_multiple_creation(self, resident_context_unified_chat_ll):
        result = MagicMock()
        result.to_input_list.return_value = [
            {
                "content": "can you create me a service request for my faucet in my kitchen, it's leaking from the base. no other info",
                "role": "user",
            },
            {"id": "rs_1", "summary": [], "type": "reasoning"},
            {
                "arguments": '{"pmc_id":123,"site_id":456,"resident_household_id":789,"resident_member_id":1,"emergency":false,"chat_summary":"Resident reports that the kitchen faucet is leaking from the base. Please inspect and repair the faucet to stop the leak."}',
                "call_id": "call_1",
                "name": "create_service_request",
                "type": "function_call",
                "id": "fc_1234",
                "status": "completed",
            },
            {
                "arguments": '{"link_type":"single_service_request"}',
                "call_id": "call_2",
                "name": "create_link",
                "type": "function_call",
                "id": "fc_12345",
                "status": "completed",
            },
            {
                "arguments": '{"pmc_id":123,"site_id":456,"resident_household_id":789,"resident_member_id":1,"emergency":false,"chat_summary":"Resident reports that the kitchen faucet is leaking from the base. Please inspect and repair the faucet to stop the leak."}',
                "call_id": "call_3",
                "name": "create_service_request",
                "type": "function_call",
                "id": "fc_12345",
                "status": "completed",
            },
            {
                "call_id": "call_1",
                "output": '{"type":"text","text":"{\\"service_request_id\\":123,\\"service_request_created\\":true,\\"priority_number\\":\\"3\\",\\"priority_name\\":\\"Standard\\",\\"agent_response\\":\\"Service request created successfully.\\"}","annotations":null,"meta":null}',
                "type": "function_call_output",
            },
            {
                "call_id": "call_2",
                "output": "https://cassidysouth.qa1.loftliving.com/portal/mr/detail/mrId",
                "type": "function_call_output",
            },
            {
                "call_id": "call_3",
                "output": '{"type":"text","text":"{\\"service_request_id\\":1234,\\"service_request_created\\":true,\\"priority_number\\":\\"3\\",\\"priority_name\\":\\"Standard\\",\\"agent_response\\":\\"Service request created successfully.\\"}","annotations":null,"meta":null}',
                "type": "function_call_output",
            },
            {"id": "rs_2", "summary": [], "type": "reasoning"},
            {
                "id": "msg_1",
                "content": [
                    {
                        "annotations": [],
                        "text": '{"response":"I’ve created a maintenance request for your leaking kitchen faucet.\\n\\n- Service Request ID: 123  \\n- Priority: 3 – Standard  \\n- Details: Kitchen faucet leaking from the base; technician will inspect and repair.\\n\\nIt may take a short time for this to appear in your online portal.\\n\\nYou can view the request here:\\n[View service request](https://cassidysouth.qa1.loftliving.com/portal/mr/detail/mrId)","language_code":"en","workflow_codes":["facilities_flow"]}',
                        "type": "output_text",
                        "logprobs": [],
                    }
                ],
                "role": "assistant",
                "status": "completed",
                "type": "message",
            },
        ]

        add_metadata_into_context(resident_context_unified_chat_ll, result)

        expected_metadata = {
            "call_1": {"service_request": ["create_service_request", {"created": True, "sr_id": 123}]},
            "call_3": {"service_request": ["create_service_request", {"created": True, "sr_id": 1234}]},
        }
        resident_context_unified_chat_ll.logging_metadata == expected_metadata

    def test_add_metadata_into_context_with_invalid_input(self, resident_context_unified_chat_ll):
        result = MagicMock()
        result.to_input_list.return_value = [
            {
                "content": "can you create me a service request for my faucet in my kitchen, it's leaking from the base. no other info",
                "role": "user",
            },
            {"id": "rs_1", "summary": [], "type": "reasoning"},
            {
                "arguments": '{"pmc_id":123,"site_id":456,"resident_household_id":789,"resident_member_id":1,"emergency":false,"chat_summary":"Resident reports that the kitchen faucet is leaking from the base. Please inspect and repair the faucet to stop the leak."}',
                "call_id": "call_1",
                "name": "create_service_request",
                "type": "function_call",
                "id": "fc_1234",
                "status": "completed",
            },
            {
                "arguments": '{"link_type":"single_service_request"}',
                "call_id": "call_2",
                "name": "create_link",
                "type": "function_call",
                "id": "fc_12345",
                "status": "completed",
            },
            {"type": "function_call_output", "call_id": "call_1", "output": '{"text": "invalid_json"'},
            {
                "call_id": "call_1",
                "output": '{"type":"text","text":"{\\"service_request_id\\":123,\\"service_request_created\\":true,\\"priority_number\\":\\"3\\",\\"priority_name\\":\\"Standard\\",\\"agent_response\\":\\"Service request created successfully.\\"}","annotations":null,"meta":null}',
                "type": "function_call_output",
            },
            {
                "call_id": "call_2",
                "output": "https://cassidysouth.qa1.loftliving.com/portal/mr/detail/mrId",
                "type": "function_call_output",
            },
            {"id": "rs_2", "summary": [], "type": "reasoning"},
            {
                "id": "msg_1",
                "content": [
                    {
                        "annotations": [],
                        "text": '{"response":"I’ve created a maintenance request for your leaking kitchen faucet.\\n\\n- Service Request ID: 123  \\n- Priority: 3 – Standard  \\n- Details: Kitchen faucet leaking from the base; technician will inspect and repair.\\n\\nIt may take a short time for this to appear in your online portal.\\n\\nYou can view the request here:\\n[View service request](https://cassidysouth.qa1.loftliving.com/portal/mr/detail/mrId)","language_code":"en","workflow_codes":["facilities_flow"]}',
                        "type": "output_text",
                        "logprobs": [],
                    }
                ],
                "role": "assistant",
                "status": "completed",
                "type": "message",
            },
        ]

        add_metadata_into_context(resident_context_unified_chat_ll, result)

        expected_metadata = {
            "call_1": {"service_request": ["create_service_request", {"created": True, "sr_id": 123}]}
        }
        resident_context_unified_chat_ll.logging_metadata == expected_metadata

    def test_add_metadata_into_context_with_service_request_created_false(self, resident_context_unified_chat_ll):
        result = MagicMock()
        result.to_input_list.return_value = [
            {
                "content": "can you create me a service request for my faucet in my kitchen, it's leaking from the base. no other info",
                "role": "user",
            },
            {"id": "rs_1", "summary": [], "type": "reasoning"},
            {
                "arguments": '{"pmc_id":123,"site_id":456,"resident_household_id":789,"resident_member_id":1,"emergency":false,"chat_summary":"Resident reports that the kitchen faucet is leaking from the base. Please inspect and repair the faucet to stop the leak."}',
                "call_id": "call_1",
                "name": "create_service_request",
                "type": "function_call",
                "id": "fc_1234",
                "status": "completed",
            },
            {
                "arguments": '{"link_type":"single_service_request"}',
                "call_id": "call_2",
                "name": "create_link",
                "type": "function_call",
                "id": "fc_12345",
                "status": "completed",
            },
            {
                "call_id": "call_1",
                "output": '{"type":"text","text":"{\\"service_request_created\\":false,\\"priority_number\\":\\"3\\",\\"priority_name\\":\\"Standard\\",\\"agent_response\\":\\"Service request created successfully.\\"}","annotations":null,"meta":null}',
                "type": "function_call_output",
            },
            {
                "call_id": "call_2",
                "output": "https://cassidysouth.qa1.loftliving.com/portal/mr/detail/mrId",
                "type": "function_call_output",
            },
            {"id": "rs_2", "summary": [], "type": "reasoning"},
            {
                "id": "msg_1",
                "content": [
                    {
                        "annotations": [],
                        "text": '{"response":"I’ve created a maintenance request for your leaking kitchen faucet.\\n\\n- Service Request ID: 123  \\n- Priority: 3 – Standard  \\n- Details: Kitchen faucet leaking from the base; technician will inspect and repair.\\n\\nIt may take a short time for this to appear in your online portal.\\n\\nYou can view the request here:\\n[View service request](https://cassidysouth.qa1.loftliving.com/portal/mr/detail/mrId)","language_code":"en","workflow_codes":["facilities_flow"]}',
                        "type": "output_text",
                        "logprobs": [],
                    }
                ],
                "role": "assistant",
                "status": "completed",
                "type": "message",
            },
        ]

        add_metadata_into_context(resident_context_unified_chat_ll, result)

        expected_metadata = {"service_request": ["create_service_request", {"created": False}]}
        resident_context_unified_chat_ll.logging_metadata == expected_metadata

    def test_add_metadata_into_context_with_missing_output(self, resident_context_unified_chat_ll):
        result = MagicMock()
        result.to_input_list.return_value = [
            {
                "content": "can you create me a service request for my faucet in my kitchen, it's leaking from the base. no other info",
                "role": "user",
            },
            {"id": "rs_1", "summary": [], "type": "reasoning"},
            {
                "arguments": '{"pmc_id":123,"site_id":456,"resident_household_id":789,"resident_member_id":1,"emergency":false,"chat_summary":"Resident reports that the kitchen faucet is leaking from the base. Please inspect and repair the faucet to stop the leak."}',
                "call_id": "call_1",
                "name": "create_service_request",
                "type": "function_call",
                "id": "fc_1234",
                "status": "completed",
            },
        ]

        add_metadata_into_context(resident_context_unified_chat_ll, result)

        expected_metadata = {"service_request": ["create_service_request", {"created": False}]}
        resident_context_unified_chat_ll.logging_metadata == expected_metadata


class TestHandleActiveHandoff:
    """Test cases for the _handle_active_handoff function."""

    @patch("agent_leasing.server.is_handoff_active", new_callable=AsyncMock, return_value=True)
    @pytest.mark.asyncio
    async def test_sms_channel_active_handoff_returns_response(self, mock_is_handoff, ask_request_resident_sms_knck):
        req = ask_request_resident_sms_knck
        result = await _handle_active_handoff(req)

        assert result is not None
        assert isinstance(result, AskResponse)
        assert result.content is not None
        assert result.metadata["human_handoff"] is True
        assert "email_route_back" not in result.metadata

    @patch("agent_leasing.server.is_handoff_active", new_callable=AsyncMock, return_value=True)
    @pytest.mark.asyncio
    async def test_email_channel_returns_metadata_only(self, mock_is_handoff, ask_request_resident_email_knck):
        req = ask_request_resident_email_knck
        result = await _handle_active_handoff(req)

        assert result is not None
        assert result.content is None
        assert result.metadata["human_handoff"] is True
        assert result.metadata["email_route_back"] is True

    @pytest.mark.asyncio
    async def test_voice_channel_returns_none(self, ask_request_resident_voice_knck):
        result = await _handle_active_handoff(ask_request_resident_voice_knck)
        assert result is None

    @patch("agent_leasing.server.is_handoff_active", new_callable=AsyncMock, return_value=False)
    @pytest.mark.asyncio
    async def test_handoff_not_active_returns_none(self, mock_is_handoff, ask_request_resident_sms_knck):
        result = await _handle_active_handoff(ask_request_resident_sms_knck)
        assert result is None

    @patch("agent_leasing.server.is_handoff_active", new_callable=AsyncMock, return_value=True)
    @pytest.mark.asyncio
    async def test_missing_resident_id_falls_back_to_ab_resident_id(
        self, mock_is_handoff, ask_request_resident_sms_ll
    ):
        req = ask_request_resident_sms_ll
        result = await _handle_active_handoff(req)

        assert result is not None
        mock_is_handoff.assert_awaited_once_with(
            req.product,
            req.product_info.knock_property_id,
            req.product_info.knock_resident_id,
            getattr(req.product_info.ab_resident_id, "id", None),
        )


class TestHandleUrlTransfer:
    """Test cases for the _handle_url_transfer function."""

    @pytest.fixture(autouse=True)
    def mock_translate_text_default(self):
        with patch("agent_leasing.server.translate_text", new_callable=AsyncMock, return_value=URL_HANDOFF_RESPONSE):
            yield

    @pytest.fixture
    def default_context(self):
        return SessionScope()

    @pytest.mark.asyncio
    async def test_voice_channel_returns_none(self, ask_request_resident_voice_knck, default_context):
        ask_request_resident_voice_knck.prompt = f"check this out {URL_REPLACEMENT}"
        result = await _handle_url_transfer(ask_request_resident_voice_knck, default_context)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_url_in_prompt_returns_none(self, ask_request_resident_sms_knck, default_context):
        ask_request_resident_sms_knck.prompt = "hello there"
        result = await _handle_url_transfer(ask_request_resident_sms_knck, default_context)
        assert result is None

    @pytest.mark.asyncio
    async def test_chat_channel_with_url_returns_handoff_response(self, ask_request_resident, default_context):
        ask_request_resident.prompt = f"click this {URL_REPLACEMENT}"
        result = await _handle_url_transfer(ask_request_resident, default_context)

        assert result is not None
        assert isinstance(result, UrlHandoffResult)
        assert result.metadata["human_handoff"] is True
        assert result.metadata["human_hand_off_message"] == "Resident submitted a url for review"
        assert result.metadata["email_route_back"] is False
        assert result.response_text == URL_HANDOFF_RESPONSE

    @patch("agent_leasing.server.memory.put", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_sms_channel_with_url_writes_redis_and_returns_response(
        self, mock_memory_put, ask_request_resident_sms_knck, default_context
    ):
        ask_request_resident_sms_knck.prompt = f"see {URL_REPLACEMENT}"
        result = await _handle_url_transfer(ask_request_resident_sms_knck, default_context)

        assert result is not None
        assert result.metadata["human_handoff"] is True
        assert result.metadata["email_route_back"] is False
        mock_memory_put.assert_awaited_once()
        call_args = mock_memory_put.call_args.args
        assert call_args[0] == get_handoff_key(
            ask_request_resident_sms_knck.product,
            ask_request_resident_sms_knck.product_info.knock_property_id,
            ask_request_resident_sms_knck.product_info.knock_resident_id,
            getattr(ask_request_resident_sms_knck.product_info.ab_resident_id, "id", None),
        )
        handoff_data = call_args[1]
        assert handoff_data["transferred"] is True
        assert "handoff_time" in handoff_data

    @patch("agent_leasing.server.memory.put", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_email_channel_with_url_writes_redis_and_sets_email_route_back(
        self, mock_memory_put, ask_request_resident_email_knck, default_context
    ):
        ask_request_resident_email_knck.prompt = f"here is a link {URL_REPLACEMENT}"
        result = await _handle_url_transfer(ask_request_resident_email_knck, default_context)

        assert result is not None
        assert result.metadata["human_handoff"] is True
        assert result.metadata["email_route_back"] is True
        mock_memory_put.assert_awaited_once()

    @patch("agent_leasing.server.memory.put", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_sms_channel_with_url_and_missing_resident_id_uses_ab_resident_key(
        self, mock_memory_put, ask_request_resident_sms_ll, default_context
    ):
        ask_request_resident_sms_ll.prompt = f"see {URL_REPLACEMENT}"
        result = await _handle_url_transfer(ask_request_resident_sms_ll, default_context)

        assert result is not None
        mock_memory_put.assert_awaited_once()
        call_args = mock_memory_put.call_args.args
        assert call_args[0] == get_handoff_key(
            ask_request_resident_sms_ll.product,
            ask_request_resident_sms_ll.product_info.knock_property_id,
            ask_request_resident_sms_ll.product_info.knock_resident_id,
            getattr(ask_request_resident_sms_ll.product_info.ab_resident_id, "id", None),
        )

    @patch("agent_leasing.server.memory.put", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_sms_channel_missing_property_id_skips_redis(
        self, mock_memory_put, ask_request_resident_sms_knck, default_context
    ):
        ask_request_resident_sms_knck.prompt = f"link {URL_REPLACEMENT}"
        ask_request_resident_sms_knck.product_info.knock_property_id = None
        result = await _handle_url_transfer(ask_request_resident_sms_knck, default_context)

        assert result is not None
        mock_memory_put.assert_not_awaited()

    @patch("agent_leasing.server.translate_text", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_non_english_session_uses_translated_response(self, mock_translate_text, ask_request_resident):
        translated_response = "¡Gracias! He enviado esto a un miembro del personal para su revisión."
        mock_translate_text.return_value = translated_response
        ask_request_resident.prompt = f"mira esto {URL_REPLACEMENT}"
        context = SessionScope(language_code="es")
        result = await _handle_url_transfer(ask_request_resident, context)

        assert result is not None
        assert result.response_text == translated_response
        assert result.language_code == "es"

    @patch("agent_leasing.server.ls.trace")
    @patch("agent_leasing.server._handle_url_transfer", new_callable=AsyncMock)
    @patch("agent_leasing.server._handle_active_handoff", new_callable=AsyncMock, return_value=None)
    @patch("agent_leasing.server.build_agent_request", new_callable=AsyncMock)
    def test_url_handoff_early_return_in_agent_ask(
        self,
        mock_build,
        mock_handle_handoff,
        mock_handle_url,
        mock_ls_trace,
        client,
        ask_request_resident_sms_knck,
    ):
        req = ask_request_resident_sms_knck
        handoff_resp = UrlHandoffResult(
            response_text=URL_HANDOFF_RESPONSE,
            language_code="en",
            metadata={
                "human_handoff": True,
                "human_hand_off_message": "Resident submitted url for review",
                "email_route_back": False,
            },
        )
        mock_handle_url.return_value = handoff_resp
        mock_build.return_value = SimpleNamespace(
            trace_id="trace_test",
            language_code="en",
            workflow_name="workflow",
            flows=[],
            logging_metadata=[],
            group_id="group",
            headers={},
            context=SessionScope(ask_request=req),
            previous_response_id=None,
            agent=MagicMock(),
            metadata={},
            expire="10m",
            start_time=0.0,
        )

        mock_run = MagicMock()
        mock_run.add_inputs = MagicMock()
        mock_ls_trace.return_value.__enter__ = MagicMock(return_value=mock_run)
        mock_ls_trace.return_value.__exit__ = MagicMock(return_value=False)

        response = client.post("/v1/agent/ask", json=req.model_dump())
        assert response.status_code == 200
        data = response.json()
        assert data["metadata"]["human_handoff"] is True
        assert data["flow_name"] == "HANDOFF_TO_HUMAN_FLOW"
        mock_build.assert_called_once()


class TestAgentAskPaths:
    """Test cases for agent_ask error and early-return paths."""

    @patch("agent_leasing.server.ls.trace")
    @patch("agent_leasing.server._handle_active_handoff", new_callable=AsyncMock)
    @patch("agent_leasing.server.build_agent_request", new_callable=AsyncMock)
    def test_handoff_early_return(
        self,
        mock_build,
        mock_handle_handoff,
        mock_ls_trace,
        client,
        ask_request_resident_sms_knck,
    ):
        handoff_resp = AskResponse(
            request_id=ask_request_resident_sms_knck.request_id,
            metadata={"human_handoff": True},
            content=AskContent(chat='{"response":"handoff"}'),
            flow_id="flow1",
            flow_name="HANDOFF",
            chat_session_id=ask_request_resident_sms_knck.chat_session_id,
        )
        mock_handle_handoff.return_value = handoff_resp

        mock_run = MagicMock()
        mock_run.add_inputs = MagicMock()
        mock_ls_trace.return_value.__enter__ = MagicMock(return_value=mock_run)
        mock_ls_trace.return_value.__exit__ = MagicMock(return_value=False)

        response = client.post("/v1/agent/ask", json=ask_request_resident_sms_knck.model_dump())
        assert response.status_code == 200
        data = response.json()
        assert data["metadata"]["human_handoff"] is True
        mock_build.assert_called_once()

    @patch("agent_leasing.server.ls.trace")
    @patch("agent_leasing.server._handle_active_handoff", new_callable=AsyncMock, return_value=None)
    @patch("agent_leasing.server.build_agent_request", new_callable=AsyncMock)
    def test_unsupported_agent_raises_422(
        self,
        mock_build,
        mock_handle_handoff,
        mock_ls_trace,
        client,
        ask_request_resident_sms_knck,
    ):
        mock_build.side_effect = UnsupportedAgentException("bad agent")

        mock_run = MagicMock()
        mock_run.add_inputs = MagicMock()
        mock_ls_trace.return_value.__enter__ = MagicMock(return_value=mock_run)
        mock_ls_trace.return_value.__exit__ = MagicMock(return_value=False)

        response = client.post("/v1/agent/ask", json=ask_request_resident_sms_knck.model_dump())
        assert response.status_code == 422

    @patch("agent_leasing.server.ls.trace")
    @patch("agent_leasing.server.memory.put_context", new_callable=AsyncMock)
    @patch("agent_leasing.server.save_previous_response_id")
    @patch("agent_leasing.server.log_conversation_exchange")
    @patch("agent_leasing.server.emit_metrics")
    @patch("agent_leasing.server.log_internal_messages")
    @patch("agent_leasing.server.add_metadata_into_context")
    @patch("agent_leasing.server.Runner.run", new_callable=AsyncMock)
    @patch("agent_leasing.server._handle_active_handoff", new_callable=AsyncMock, return_value=None)
    @patch("agent_leasing.server.build_agent_request", new_callable=AsyncMock)
    def test_fallback_response_on_exception(
        self,
        mock_build_agent_request,
        mock_handle_handoff,
        mock_runner_run,
        mock_add_metadata,
        mock_log_internal_messages,
        mock_emit_metrics,
        mock_log_conversation_exchange,
        mock_save_previous_response_id,
        mock_put_context,
        mock_ls_trace,
        client,
        ask_request_resident_sms_knck,
    ):
        req = ask_request_resident_sms_knck
        req.prompt = "Hello"

        context = SessionScope(ask_request=req)
        context.langsmith_run_tree = {}

        class DummyAgent:
            def __init__(self):
                self.agent_instance = object()
                self.mcp_servers = {}
                self.agent_architecture = AgentArchitecture.SINGLE_AGENT
                self.name = "dummy-agent"

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

        mock_runner_run.side_effect = RuntimeError("boom")
        mock_build_agent_request.return_value = SimpleNamespace(
            trace_id="trace_test",
            language_code="en",
            workflow_name="workflow",
            flows=[Flow(name="test_flow")],
            logging_metadata=[],
            group_id="group",
            headers={},
            context=context,
            thread_id="thread",
            previous_response_id=None,
            agent=DummyAgent(),
            metadata={},
            root_run=MagicMock(),
            langsmith_trace_url=None,
            human_message=MagicMock(),
            llm_message=MagicMock(),
            start_time=0.0,
            expire="10m",
        )

        mock_run = MagicMock()
        mock_run.get_url.return_value = "https://smith.langchain.com/o/3f19e100/projects/p/5170510d/r/65b25b0f"
        mock_ls_trace.return_value.__enter__ = MagicMock(return_value=mock_run)
        mock_ls_trace.return_value.__exit__ = MagicMock(return_value=False)

        response = client.post("/v1/agent/ask", json=req.model_dump())
        assert response.status_code == 200
        data = response.json()
        chat = json.loads(data["content"]["chat"])
        assert chat["response"] == FALLBACK_RESPONSE

    @patch("agent_leasing.server.ls.trace")
    @patch("agent_leasing.server.memory.put_context", new_callable=AsyncMock)
    @patch("agent_leasing.server.save_previous_response_id")
    @patch("agent_leasing.server.log_conversation_exchange")
    @patch("agent_leasing.server.emit_metrics")
    @patch("agent_leasing.server.log_internal_messages")
    @patch("agent_leasing.server.add_metadata_into_context")
    @patch("agent_leasing.server.execute_handoff")
    @patch("agent_leasing.server.Runner.run", new_callable=AsyncMock)
    @patch("agent_leasing.server.handle_sms_consent_gate", new_callable=AsyncMock)
    @patch("agent_leasing.server._handle_active_handoff", new_callable=AsyncMock, return_value=None)
    @patch("agent_leasing.server.build_agent_request", new_callable=AsyncMock)
    def test_handoff_execution_in_normal_flow(
        self,
        mock_build_agent_request,
        mock_handle_handoff,
        mock_handle_sms_consent_gate,
        mock_runner_run,
        mock_execute_handoff,
        mock_add_metadata,
        mock_log_internal_messages,
        mock_emit_metrics,
        mock_log_conversation_exchange,
        mock_save_previous_response_id,
        mock_put_context,
        mock_ls_trace,
        client,
        ask_request_resident_sms_knck,
    ):
        req = ask_request_resident_sms_knck
        req.product_info.source = "AIRR"
        req.prompt = "Hello"

        context = SessionScope(ask_request=req)
        context.langsmith_run_tree = {}
        context.handoff = True
        context.handoff_message = "transferring"

        class DummyAgent:
            def __init__(self):
                self.agent_instance = object()
                self.mcp_servers = {}
                self.agent_architecture = AgentArchitecture.SINGLE_AGENT
                self.name = "dummy-agent"

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

        dummy_result = SimpleNamespace(
            final_output="OK",
            context_wrapper=SimpleNamespace(context=SimpleNamespace(logging_metadata={})),
            last_response_id=None,
        )
        mock_runner_run.return_value = dummy_result

        handoff_resp = AskResponse(
            request_id=req.request_id,
            metadata={"human_handoff": True},
            content=AskContent(chat='{"response":"handoff done"}'),
            flow_id="flow1",
            flow_name="HANDOFF",
            chat_session_id=req.chat_session_id,
        )
        mock_execute_handoff.return_value = handoff_resp

        mock_build_agent_request.return_value = SimpleNamespace(
            trace_id="trace_test",
            language_code="en",
            workflow_name="workflow",
            flows=[Flow(name="test_flow")],
            logging_metadata=[],
            group_id="group",
            headers={},
            context=context,
            thread_id="thread",
            previous_response_id=None,
            agent=DummyAgent(),
            metadata={},
            root_run=MagicMock(),
            langsmith_trace_url=None,
            human_message=MagicMock(),
            llm_message=MagicMock(),
            start_time=0.0,
            expire="10m",
        )

        mock_run = MagicMock()
        mock_run.get_url.return_value = "https://smith.langchain.com/o/3f19e100/projects/p/5170510d/r/65b25b0f"
        mock_ls_trace.return_value.__enter__ = MagicMock(return_value=mock_run)
        mock_ls_trace.return_value.__exit__ = MagicMock(return_value=False)

        response = client.post("/v1/agent/ask", json=req.model_dump())
        assert response.status_code == 200
        mock_execute_handoff.assert_called_once()

    @patch("agent_leasing.server.ls.trace")
    @patch("agent_leasing.server.memory.put_context", new_callable=AsyncMock)
    @patch("agent_leasing.server.save_previous_response_id")
    @patch("agent_leasing.server.log_conversation_exchange")
    @patch("agent_leasing.server.emit_metrics")
    @patch("agent_leasing.server.log_internal_messages")
    @patch("agent_leasing.server.add_metadata_into_context")
    @patch("agent_leasing.server.execute_handoff")
    @patch("agent_leasing.server.Runner.run", new_callable=AsyncMock)
    @patch("agent_leasing.server._handle_active_handoff", new_callable=AsyncMock, return_value=None)
    @patch("agent_leasing.server.build_agent_request", new_callable=AsyncMock)
    def test_email_handoff_suppresses_kafka_bot_message(
        self,
        mock_build_agent_request,
        mock_handle_handoff,
        mock_runner_run,
        mock_execute_handoff,
        mock_add_metadata,
        mock_log_internal_messages,
        mock_emit_metrics,
        mock_log_conversation_exchange,
        mock_save_previous_response_id,
        mock_put_context,
        mock_ls_trace,
        client,
        ask_request_resident_email_knck,
    ):
        req = ask_request_resident_email_knck
        req.prompt = "I need to talk to someone"

        context = SessionScope(ask_request=req)
        context.langsmith_run_tree = {}
        context.handoff = True
        context.handoff_message = "transferring to staff"

        class DummyAgent:
            def __init__(self):
                self.agent_instance = object()
                self.mcp_servers = {}
                self.agent_architecture = AgentArchitecture.SINGLE_AGENT
                self.name = "dummy-agent"

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

        dummy_result = SimpleNamespace(
            final_output="Here is a composed email response",
            context_wrapper=SimpleNamespace(context=SimpleNamespace(logging_metadata={})),
            last_response_id=None,
        )
        mock_runner_run.return_value = dummy_result

        # execute_handoff sets email_route_back=True for EMAIL channel
        handoff_resp = AskResponse(
            request_id=req.request_id,
            metadata={"human_handoff": True, "email_route_back": True},
            content=AskContent(chat='{"response":"handoff done"}'),
            flow_id="flow1",
            flow_name="HANDOFF",
            chat_session_id=req.chat_session_id,
        )
        mock_execute_handoff.return_value = handoff_resp

        mock_build_agent_request.return_value = SimpleNamespace(
            trace_id="trace_test",
            language_code="en",
            workflow_name="workflow",
            flows=[Flow(name="test_flow")],
            logging_metadata=[],
            group_id="group",
            headers={},
            context=context,
            thread_id="thread",
            previous_response_id=None,
            agent=DummyAgent(),
            metadata={},
            root_run=MagicMock(),
            langsmith_trace_url=None,
            human_message=MagicMock(),
            llm_message=MagicMock(),
            start_time=0.0,
            expire="10m",
        )

        mock_run = MagicMock()
        mock_run.get_url.return_value = "https://smith.langchain.com/o/3f19e100/projects/p/5170510d/r/65b25b0f"
        mock_ls_trace.return_value.__enter__ = MagicMock(return_value=mock_run)
        mock_ls_trace.return_value.__exit__ = MagicMock(return_value=False)

        response = client.post("/v1/agent/ask", json=req.model_dump())
        assert response.status_code == 200

        # Kafka event should contain the suppressed message, not the composed email
        mock_log_conversation_exchange.assert_called_once()
        kafka_call_kwargs = mock_log_conversation_exchange.call_args.kwargs
        assert kafka_call_kwargs["bot_message"] == "User requested handoff. No message was sent to the user."


class TestAgentStreamErrors:
    """Test cases for agent_stream error paths."""

    @patch("agent_leasing.server.build_agent_request", new_callable=AsyncMock)
    def test_unsupported_agent_raises_422(self, mock_build, client, ask_request_resident_sms_knck):
        mock_build.side_effect = UnsupportedAgentException("bad agent")

        response = client.post("/v1/agent/stream", json=ask_request_resident_sms_knck.model_dump())
        assert response.status_code == 422

    @patch("agent_leasing.server.build_agent_request", new_callable=AsyncMock)
    def test_generic_exception_from_build_returns_500(self, mock_build, client, ask_request_resident_sms_knck):
        mock_build.side_effect = RuntimeError("unexpected")

        response = client.post("/v1/agent/stream", json=ask_request_resident_sms_knck.model_dump())
        assert response.status_code == 500


class TestUtilityFunctions:
    """Test cases for utility functions in server.py."""

    def test_count_tasks_by_name(self):
        task1 = MagicMock()
        task1.get_name.return_value = "Task-1"
        task2 = MagicMock()
        task2.get_name.return_value = "Task-1"
        task3 = MagicMock()
        task3.get_name.return_value = "Task-2"

        result = _count_tasks_by_name({task1, task2, task3})
        assert result["Task-1"] == 2
        assert result["Task-2"] == 1


class TestMain:
    """Test that main() passes explicit uvloop and httptools to uvicorn."""

    @patch("uvicorn.run")
    def test_uvicorn_run_uses_uvloop_and_httptools(self, mock_uvicorn_run):
        main()
        mock_uvicorn_run.assert_called_once()
        kwargs = mock_uvicorn_run.call_args
        assert kwargs.kwargs["loop"] == "uvloop"
        assert kwargs.kwargs["http"] == "httptools"


class TestCacheEndpoints:
    """Test cases for cache warming and invalidation endpoints."""

    @patch("agent_leasing.server.fetch_ldp_property_data", new_callable=AsyncMock)
    def test_warm_property_cache(self, mock_fetch, client):
        response = client.get("/v1/cache/property/12345")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        mock_fetch.assert_called_once_with("12345")

    @patch("agent_leasing.server.CachingMCPServer.invalidate_property_caches", new_callable=AsyncMock)
    def test_invalidate_property_cache(self, mock_invalidate, client):
        mock_invalidate.return_value = {"ldp": True, "mcp": False}
        response = client.post("/v1/cache/property/12345")
        assert response.status_code == 200
        assert response.json()["status"] == {"ldp": True, "mcp": False}
        mock_invalidate.assert_called_once_with(12345)


class TestGetLangsmithProjectName:
    @pytest.mark.parametrize(
        "product,expected_suffix",
        [
            ("resident_one_chat", "chat"),
            ("resident_one_sms", "sms"),
            ("resident_one_email", "email"),
            ("resident_one_voice", "voice"),
        ],
    )
    def test_returns_base_project_name(self, product, expected_suffix):
        result = _get_langsmith_project_name(product)
        assert result == f"{settings.environment}_renter_ai_resident_{expected_suffix}"

    def test_no_load_test_suffix(self):
        result = _get_langsmith_project_name("resident_one_chat")
        assert "_load_test" not in result


class TestLoadTestTracingSuppression:
    """Verify LangSmith tracing is disabled for load test traffic."""

    @pytest.fixture(autouse=True)
    def _patch_tracing(self):
        with (
            patch("agent_leasing.server.ls.traceable") as traceable,
            patch("agent_leasing.server.ls.tracing_context") as tracing_ctx,
            patch("agent_leasing.server.build_agent_request", new_callable=AsyncMock) as build,
        ):
            tracing_ctx.return_value.__enter__ = MagicMock(return_value=None)
            tracing_ctx.return_value.__exit__ = MagicMock(return_value=False)
            self.mock_traceable = traceable
            self.mock_tracing_ctx = tracing_ctx
            self.mock_build = build
            yield

    def test_ask_load_test_disables_tracing(self, client, ask_request_resident_sms_knck):
        """Non-streaming: tracing_context(enabled=False) and traceable skipped."""
        self.mock_build.side_effect = UnsupportedAgentException("irrelevant")

        payload = ask_request_resident_sms_knck.model_dump()
        payload["is_load_test"] = True
        client.post("/v1/agent/ask", json=payload)

        self.mock_tracing_ctx.assert_called_once_with(enabled=False)
        self.mock_traceable.assert_not_called()

    def test_ask_normal_request_enables_tracing(self, client, ask_request_resident_sms_knck):
        """Non-streaming: tracing_context(enabled=True) and traceable applied."""
        self.mock_build.side_effect = UnsupportedAgentException("irrelevant")
        self.mock_traceable.return_value = lambda fn: fn

        payload = ask_request_resident_sms_knck.model_dump()
        payload["is_load_test"] = False
        client.post("/v1/agent/ask", json=payload)

        self.mock_tracing_ctx.assert_called_once_with(enabled=True)
        self.mock_traceable.assert_called_once()

    def test_stream_load_test_skips_traceable(self, client, ask_request_resident_sms_knck):
        """Streaming: traceable not applied for load test requests."""
        self.mock_build.return_value = MagicMock()

        payload = ask_request_resident_sms_knck.model_dump()
        payload["is_load_test"] = True
        client.post("/v1/agent/stream", json=payload)

        self.mock_traceable.assert_not_called()

    def test_stream_normal_request_applies_traceable(self, client, ask_request_resident_sms_knck):
        """Streaming: traceable applied for normal requests."""
        self.mock_build.return_value = MagicMock()
        self.mock_traceable.return_value = lambda fn: fn

        payload = ask_request_resident_sms_knck.model_dump()
        payload["is_load_test"] = False
        client.post("/v1/agent/stream", json=payload)

        self.mock_traceable.assert_called_once()


class TestBuildAgentInput:
    """_build_agent_input injects the email Subject: line for EMAIL channel requests."""

    def test_email_with_subject_prepends_subject_line(self, ask_request_resident_email_ll):
        from agent_leasing.server import _build_agent_input

        ask_request_resident_email_ll.prompt = "All moved out, keys on the counter."
        ask_request_resident_email_ll.product_info.email_chat.email_subject = "Re: Move Out Reminder"
        context = SessionScope(ask_request=ask_request_resident_email_ll)

        assert (
            _build_agent_input(ask_request_resident_email_ll, context)
            == "Subject: Re: Move Out Reminder\n\nAll moved out, keys on the counter."
        )

    def test_email_without_email_chat_returns_prompt_only(self, ask_request_resident_email_ll):
        from agent_leasing.server import _build_agent_input

        ask_request_resident_email_ll.prompt = "Body only."
        ask_request_resident_email_ll.product_info.email_chat = None
        context = SessionScope(ask_request=ask_request_resident_email_ll)

        assert _build_agent_input(ask_request_resident_email_ll, context) == "Body only."

    def test_email_with_empty_subject_returns_prompt_only(self, ask_request_resident_email_ll):
        from agent_leasing.server import _build_agent_input

        ask_request_resident_email_ll.prompt = "Body."
        ask_request_resident_email_ll.product_info.email_chat.email_subject = ""
        context = SessionScope(ask_request=ask_request_resident_email_ll)

        assert _build_agent_input(ask_request_resident_email_ll, context) == "Body."

    def test_chat_channel_does_not_prepend_subject(self, ask_request_resident_chat_ll):
        from agent_leasing.server import _build_agent_input

        ask_request_resident_chat_ll.prompt = "Hello"
        ask_request_resident_chat_ll.product_info.email_chat = EmailChat(
            email_subject="Re: Move Out Reminder",
            knock_property_id="1",
            knock_resident_id="1",
            knock_resident_email="resident@example.com",
            resident_assigned_manager_id="1",
            original_text="",
            html="",
            email_source="email",
            thread_id="t",
            thread_message_chat_id="tmc",
        )
        context = SessionScope(ask_request=ask_request_resident_chat_ll)

        assert _build_agent_input(ask_request_resident_chat_ll, context) == "Hello"

    def test_sms_channel_returns_prompt_only(self, ask_request_resident_sms_ll):
        from agent_leasing.server import _build_agent_input

        ask_request_resident_sms_ll.prompt = "Yo"
        context = SessionScope(ask_request=ask_request_resident_sms_ll)

        assert _build_agent_input(ask_request_resident_sms_ll, context) == "Yo"

    def test_voice_channel_returns_prompt_only(self, ask_request_resident_voice_knck):
        from agent_leasing.server import _build_agent_input

        ask_request_resident_voice_knck.prompt = "spoken words"
        context = SessionScope(ask_request=ask_request_resident_voice_knck)

        assert _build_agent_input(ask_request_resident_voice_knck, context) == "spoken words"


class TestPublishResponderOutputActivities:
    """Cover the fan-out at the shared seam (called from both /v1/agent/ask
    and /v1/agent/stream) so a regression in either branch is caught here."""

    @staticmethod
    def _call(mock_publish, ask_request, *, workflow_codes, qna_topics, user_frustrated, message):
        final_output = SimpleNamespace(
            workflow_codes=workflow_codes,
            qna_topics=qna_topics,
            user_frustrated=user_frustrated,
        )
        context = SessionScope(ask_request=ask_request)
        _publish_responder_output_activities(final_output, context, message)
        return context

    @patch("agent_leasing.server.publish_task_activity")
    def test_qna_and_frustrated_user_both_dispatched(self, mock_publish, ask_request_resident_chat_ll):
        from agent_leasing.kafka.task_activity.extractors import (
            extract_frustrated_user_events,
            extract_qna_events,
        )
        from agent_leasing.kafka.task_activity.extractors.qna import QNA_FLOW_CODE

        context = self._call(
            mock_publish,
            ask_request_resident_chat_ll,
            workflow_codes=[QNA_FLOW_CODE],
            qna_topics=["AMENITIES_AND_FACILITIES.POOL"],
            user_frustrated=False,
            message="is the pool open?",
        )

        assert mock_publish.call_count == 2
        qna_args, qna_kwargs = mock_publish.call_args_list[0]
        assert qna_args == (extract_qna_events, [QNA_FLOW_CODE], context)
        assert qna_kwargs == {
            "qna_topics": ["AMENITIES_AND_FACILITIES.POOL"],
            "user_message": "is the pool open?",
        }
        frust_args, frust_kwargs = mock_publish.call_args_list[1]
        assert frust_args == (extract_frustrated_user_events, False, context)
        # frustrated_user gets a delivery-time dedup callback alongside
        # the user_message kwarg (the callback flips
        # context.frustrated_user_emitted only after a confirmed publish).
        assert frust_kwargs["user_message"] == "is the pool open?"
        assert callable(frust_kwargs["on_success"])

    @patch("agent_leasing.server.publish_task_activity")
    def test_user_frustrated_true_is_passed_through(self, mock_publish, ask_request_resident_chat_ll):
        self._call(
            mock_publish,
            ask_request_resident_chat_ll,
            workflow_codes=[],
            qna_topics=[],
            user_frustrated=True,
            message="get me a manager",
        )

        assert mock_publish.call_count == 2
        frustration_call = mock_publish.call_args_list[1]
        assert frustration_call.args[1] is True
        assert frustration_call.kwargs["user_message"] == "get me a manager"
