import json
from unittest.mock import MagicMock, patch

import pytest
import uvicorn
from cashews import cache
from fastapi import status as http_status

from agent_leasing.server import app, lifespan, main
from agent_leasing.settings import settings


class TestMain:
    @patch.object(uvicorn, "run")
    def test_main(self, mock_run):
        main()
        mock_run.assert_called_once_with(app, host="0.0.0.0", port=8000, loop="uvloop", http="httptools")


class TestLifespan:
    @patch("agent_leasing.server.kafka_application_context")
    async def test_lifespan(self, mock_kafka_context):
        # Create a mock FastAPI app
        mock_app = MagicMock()

        # Test the lifespan context manager
        async with lifespan(mock_app):
            # Verify that kafka_application_context.start was called
            mock_kafka_context.start.assert_called_once()

        # Verify that kafka_application_context.close was called after exiting the context
        mock_kafka_context.close.assert_called_once()


class TestHealthcheck:
    async def test_get(self, aclient):
        response = await aclient.get("/healthcheck")
        assert response.status_code == http_status.HTTP_200_OK


class TestStatus:
    async def test_get(self, aclient):
        response = await aclient.get("/status")
        assert response.status_code == http_status.HTTP_200_OK
        assert response.json()["status"] == "ok"


class TestAgentAsk:
    async def test_post_prospect_simple(self, ask_request_simple, aclient):
        ask_request_simple.prompt = "hello"
        response = await aclient.post("/v1/agent/ask", json=ask_request_simple.model_dump())
        assert response.status_code == http_status.HTTP_200_OK
        response_json = response.json()
        chat_json_str = response_json["content"]["chat"]
        json.loads(chat_json_str)  # this gives you {'response': "...", 'languageCode': ...}

        # Validate trace ID header
        trace_id = response.headers.get("X-OpenAPI-Trace-Id")
        if trace_id:
            assert trace_id.startswith("trace_")

        # Validate previous response ID header
        previous_response_id = response.headers.get("X-OpenAI-Previous-Response-Id")
        if previous_response_id:
            assert previous_response_id.startswith("resp_")

        # Validate process time
        process_time = response.headers.get("X-Process-Time")
        if process_time:
            assert float(process_time) < 30

    @patch("agent_leasing.server.log_internal_messages")
    async def test_post_prospect_simple_when_fallback_message_on_error(
        self, failing_mock_func, ask_request_simple, aclient
    ):
        failing_mock_func.side_effect = Exception("Test error")
        ask_request_simple.prompt = "hello"
        response = await aclient.post("/v1/agent/ask", json=ask_request_simple.model_dump())
        assert response.status_code == http_status.HTTP_200_OK
        fallback = "I'm unable to provide a response for that. Could you please adjust your request for me?"
        # Extract fallback response from nested JSON
        content_chat = response.json().get("content", {}).get("chat", "{}")
        actual_response = json.loads(content_chat).get("response", "")

        assert actual_response == fallback

    async def test_post_resident(self, ask_request_resident_chat_ll, aclient):
        ask_request_resident_chat_ll.prompt = "hello"
        response = await aclient.post("/v1/agent/ask", json=ask_request_resident_chat_ll.model_dump())
        assert response.status_code == http_status.HTTP_200_OK

        # Validate trace ID header
        trace_id = response.headers.get("X-OpenAPI-Trace-Id")
        if trace_id:
            assert trace_id.startswith("trace_")

        # Validate previous response ID header
        previous_response_id = response.headers.get("X-OpenAI-Previous-Response-Id")
        if previous_response_id:
            assert previous_response_id.startswith("resp_")

        # Validate process time
        process_time = response.headers.get("X-Process-Time")
        if process_time:
            assert float(process_time) < 30

    async def test_post_resident_validation_failure(self, ask_request_simple, aclient):
        """Test that resident product validation fails when required UC fields are missing."""
        # Use a simple request but change product to RESIDENT - this should fail validation
        # because ProductInfo doesn't have the required UC reference fields for ResidentProductInfo
        ask_request_simple.product = "resident"
        ask_request_simple.prompt = "hello"
        response = await aclient.post("/v1/agent/ask", json=ask_request_simple.model_dump())
        assert response.status_code == http_status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_post_resident_validation_success(self, ask_request_resident_chat_ll, aclient):
        """Test that resident product validation succeeds when required UC fields are present."""
        ask_request_resident_chat_ll.prompt = "hello"
        response = await aclient.post("/v1/agent/ask", json=ask_request_resident_chat_ll.model_dump())
        assert response.status_code == http_status.HTTP_200_OK
        response_json = response.json()
        chat_json_str = response_json["content"]["chat"]
        json.loads(chat_json_str)  # this gives you {'response': "...", 'languageCode': ...}

        # Validate trace ID header
        trace_id = response.headers.get("X-OpenAPI-Trace-Id")
        if trace_id:
            assert trace_id.startswith("trace_")

        # Validate previous response ID header
        previous_response_id = response.headers.get("X-OpenAI-Previous-Response-Id")
        if previous_response_id:
            assert previous_response_id.startswith("resp_")

        # Validate process time
        process_time = response.headers.get("X-Process-Time")
        if process_time:
            assert float(process_time) < 30


class TestPropertyCache:
    async def test_warm_property_cache(self, aclient):
        response = await aclient.get("/v1/cache/property/1")
        assert response.status_code == http_status.HTTP_200_OK

        # Verify the data is now cached by calling fetch_ldp_property_data directly
        # (cache.get doesn't work with @cache.early keys)
        from agent_leasing.clients.ldp import fetch_ldp_property_data

        cached = await fetch_ldp_property_data("1")
        assert cached is not None
        assert "enabled_modules" in cached
        assert "pte_setting" in cached
        assert "resident_summary" in cached

    async def test_invalidate_property_cache(self, aclient):
        await cache.set("ldp_property_data:21521", "data")

        # First assert that the cache is what we expect
        cached = await cache.get("ldp_property_data:21521")
        assert cached == "data"

        # Bust the cache
        response = await aclient.post("/v1/cache/property/21521")

        # Then assert that the cache is busted
        cached = await cache.get("ldp_property_data:21521")
        assert cached is None
        assert response.status_code == http_status.HTTP_200_OK

    class TestTwilioSecurity:
        @pytest.mark.parametrize(
            "auth_token,uri,signature,expected_response",
            [
                (
                    "test",
                    "/realtime-incoming-call",
                    "mZs56fvC8CysX7RCe3OzMhEEx2o=",
                    http_status.HTTP_200_OK,
                ),
                (
                    "test",
                    "/realtime-incoming-call?1=2",
                    "mZs56fvC8CysX7RCe3OzMhEEx2o=",
                    http_status.HTTP_200_OK,
                ),
                (
                    "test",
                    "/realtime-incoming-call?",
                    "wrong",
                    http_status.HTTP_403_FORBIDDEN,
                ),
                (
                    "test",
                    "https://testserver/realtime-incoming-call",
                    "mZs56fvC8CysX7RCe3OzMhEEx2o=",
                    http_status.HTTP_200_OK,
                ),
                (
                    "test",
                    "https://testserver/realtime-incoming-call?1=2",
                    "mZs56fvC8CysX7RCe3OzMhEEx2o=",
                    http_status.HTTP_200_OK,
                ),
                (
                    "test",
                    "https://testserver/realtime-incoming-call?",
                    "wrong",
                    http_status.HTTP_403_FORBIDDEN,
                ),
            ],
        )
        def test_http_validator(self, client, auth_token, uri, signature, expected_response):
            # Ensure validator runs (not local/dev) and URL host matches signature expectations
            with (
                patch.object(settings, "environment", "prod"),
                patch("agent_leasing.server._get_external_hostname", return_value="testserver"),
            ):
                settings.twilio_auth_token = auth_token
                response = client.post(uri, headers={"X-Twilio-Signature": signature})
                assert response.status_code == expected_response


class TestAgentStreaming:
    """
    There is no straightforward way to test streaming with
    """

    async def test_post_agent_streaming(self, ask_request_resident, aclient):
        ask_request_resident.prompt = "hello"
        response = await aclient.post("/v1/agent/stream", json=ask_request_resident.model_dump())
        assert response.status_code == http_status.HTTP_200_OK
        lines = [line for line in response.iter_lines() if line != ""]
        assert 'data: {"content": "", "phase": "thinking", "elapsed":' in lines[0]
        assert 'data: {"content": "", "status": "done", "done": true, "elapsed":' in lines[-2]
        assert "data: [DONE]" == lines[-1]

    async def test_post_agent_streaming_guardrail(self, ask_request_resident, aclient):
        ask_request_resident.prompt = "hello"
        response = await aclient.post("/v1/agent/stream", json=ask_request_resident.model_dump())
        assert response.status_code == http_status.HTTP_200_OK
        lines = [line for line in response.iter_lines() if line != ""]
        assert 'data: {"content": "", "phase": "thinking", "elapsed":' in lines[0]
        assert 'data: {"content": "", "status": "done", "done": true, "elapsed":' in lines[-2]
        assert "data: [DONE]" == lines[-1]

    async def test_post_agent_streaming_unsupported_agent(self, ask_request_resident, aclient):
        ask_request_resident.product = "invalid"
        response = await aclient.post("/v1/agent/stream", json=ask_request_resident.model_dump())
        assert response.status_code == http_status.HTTP_422_UNPROCESSABLE_ENTITY

    @patch("agent_leasing.server.memory.get_context")
    async def test_human_handoff(self, mock_get_context, ask_request_resident, aclient):
        """
        If there is a handoff, there should be an event that has a human_handoff attribute that
        is true and a response section that is the AskResponse model.
        """
        from agent_leasing.agent.util import SessionScope

        # Second request - simulate handoff by returning a context with handoff=True
        mock_context = SessionScope(
            ask_request=ask_request_resident,
            handoff=True,
            handoff_message="(AI Summary) I need to speak with a staff member immediately. Please connect me now.",
        )
        mock_get_context.return_value = mock_context

        ask_request_resident.prompt = "hand off"
        response = await aclient.post("/v1/agent/stream", json=ask_request_resident.model_dump())
        handoff_events = [line for line in response.iter_lines() if "human_handoff" in line]
        assert len(handoff_events) > 0
        handoff_event = json.loads(handoff_events[0].replace("data: ", ""))

        assert handoff_event["content"] == ""
        assert handoff_event["status"] == "active"
        assert handoff_event["phase"] == "thinking"
        assert handoff_event["metadata"]["human_handoff"]
        assert handoff_event["metadata"]["human_hand_off_message"]
