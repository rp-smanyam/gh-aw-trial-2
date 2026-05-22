from __future__ import annotations

import asyncio
import base64
import json
from typing import Any, assert_never

import structlog
from agents import (
    gen_trace_id,
    trace,
)
from agents.realtime import (
    RealtimeInputAudioTranscriptionConfig,
    RealtimeModelConfig,
    RealtimeModelTracingConfig,
    RealtimeRunner,
    RealtimeSession,
    RealtimeSessionEvent,
    RealtimeSessionModelSettings,
    RealtimeTurnDetectionConfig,
)
from agents.tracing.util import gen_group_id
from fastapi import (
    WebSocket,
)
from websockets import ConnectionClosedOK

from agent_leasing import api
from agent_leasing.agent.resident_one_agent.realtime import ResidentRealtimeResponderAgent
from agent_leasing.agent.simple.agent import SimpleAgent
from agent_leasing.agent.util import (
    SessionScope,
)
from agent_leasing.api.model import (
    AskRequest,
    Product,
)
from agent_leasing.settings import settings
from agent_leasing.util.realtime_util import (
    log_data_curation_event_for_realtime_events,
    realtime_history_to_input_list,
)
from agent_leasing.util.tracing_utils import build_openai_group_url

logger = structlog.getLogger()


class RealtimeWebSocketManager:
    """This is used by the Voice UI for testing from a developer's desktop rather than a call through Twilio via TwilioHandler."""

    def __init__(self):
        self.active_sessions: dict[str, RealtimeSession] = {}
        self.session_contexts: dict[str, Any] = {}
        self.websockets: dict[str, WebSocket] = {}
        self.agents: dict[str, Any] = {}  # Store agents to keep their context open
        self.agent_name = None
        self.ctx = None

    async def connect(
        self,
        websocket: WebSocket,
        session_id: str,
        agent_name: str,
        ask_request_data: dict | None = None,
    ):
        self.websockets[session_id] = websocket
        self.agent_name = agent_name

        # Use provided ask_request_data or fall back to example for backward compatibility
        if ask_request_data:
            ask_request = AskRequest(**ask_request_data)
            logger.debug(f"Using provided AskRequest for property: {ask_request.property_id}")
        else:
            ask_request = AskRequest(**api.model.examples.ASK_REQUEST_RESIDENT_VOICE_KNCK)
            logger.debug("Using default example AskRequest (no payload provided)")

        self.group_id = gen_group_id()

        model_config = RealtimeModelConfig(
            api_key=settings.openai_api_key,
            initial_model_settings=RealtimeSessionModelSettings(
                voice="alloy",
                speed=1.0,
                input_audio_transcription=RealtimeInputAudioTranscriptionConfig(
                    model=settings.transcription_model,
                    language="en",
                ),
                turn_detection=RealtimeTurnDetectionConfig(
                    type="semantic_vad", interrupt_response=True, create_response=True
                ),
                tracing=RealtimeModelTracingConfig(
                    workflow_name="Resident One Voice",
                    group_id=self.group_id,
                ),
            ),
        )
        if settings.openai_base_wss_url:
            model_config["url"] = settings.openai_base_wss_url

        self.ctx = SessionScope(ask_request=ask_request)
        self.ctx.openai_group_id = self.group_id
        self.ctx.openai_group_url = build_openai_group_url(group_id=self.group_id)

        # Agent selection based on product name
        logger.info(f"Agent: {agent_name}")
        if agent_name == Product.RESIDENT_ONE_VOICE.value:
            self.ctx.ask_request.product = Product.RESIDENT_ONE_VOICE.value
            starting_agent = ResidentRealtimeResponderAgent(self.ctx)
        elif agent_name == Product.SIMPLE.value:
            self.ctx.ask_request.product = Product.SIMPLE.value
            starting_agent = SimpleAgent(self.ctx, real_time=True)
        else:
            self.ctx.ask_request.product = Product.RESIDENT_ONE_VOICE.value
            starting_agent = ResidentRealtimeResponderAgent(self.ctx)

        # Build metadata for OpenAI traces — used for both server-side and local traces
        tracing_metadata = {
            "product": self.ctx.ask_request.product,
            "property-id": str(self.ctx.ask_request.property_id),
            "property-name": self.ctx.ask_request.product_info.property_name,
            "openai-group-url": self.ctx.openai_group_url,
        }
        # Filter out None values — OpenAI metadata values must be strings
        tracing_metadata = {k: str(v) for k, v in tracing_metadata.items() if v is not None}
        model_config["initial_model_settings"]["tracing"]["metadata"] = tracing_metadata

        with trace(
            workflow_name="Resident One Voice",
            trace_id=gen_trace_id(),
            group_id=self.group_id,
            metadata=tracing_metadata,
        ):
            # Enter agent context manually (not with `async with`) to keep MCP servers
            # connected for the duration of the session. Cleanup happens in disconnect().
            await starting_agent.__aenter__()
            self.agents[session_id] = starting_agent

            agent = starting_agent.agent()
            runner = RealtimeRunner(agent)
            session_context = await runner.run(
                context=self.ctx,
                model_config=model_config,
            )
            session = await session_context.__aenter__()
            self.active_sessions[session_id] = session
            self.session_contexts[session_id] = session_context

            # Start event processing task
            asyncio.create_task(self._process_events(session_id))

    async def disconnect(self, session_id: str):
        session = self.active_sessions.get(session_id)
        if session:
            await log_data_curation_event_for_realtime_events(session)
        if session_id in self.session_contexts:
            await self.session_contexts[session_id].__aexit__(None, None, None)
            del self.session_contexts[session_id]
        if session_id in self.active_sessions:
            del self.active_sessions[session_id]
        if session_id in self.websockets:
            del self.websockets[session_id]
        # Clean up agent context (MCP servers) after session ends
        if session_id in self.agents:
            await self.agents[session_id].__aexit__(None, None, None)
            del self.agents[session_id]

    async def send_audio(self, session_id: str, audio_bytes: bytes):
        if session_id in self.active_sessions:
            try:
                await self.active_sessions[session_id].send_audio(audio_bytes)
            except ConnectionClosedOK:
                pass

    async def send_text(self, session_id: str, text: str):
        if session_id in self.active_sessions:
            try:
                await self.active_sessions[session_id].send_message(text)
            except ConnectionClosedOK:
                pass

    async def _process_events(self, session_id: str):
        try:
            session = self.active_sessions[session_id]
            websocket = self.websockets[session_id]

            async for event in session:
                event_data = await self._serialize_event(event)
                await websocket.send_text(json.dumps(event_data))
        except Exception as e:
            logger.error(f"Error processing events for session {session_id}: {e}")

    async def _serialize_event(self, event: RealtimeSessionEvent) -> dict[str, Any]:  # noqa
        base_event: dict[str, Any] = {
            "type": event.type,
        }

        if event.type == "agent_start":
            base_event["agent"] = event.agent.name
        elif event.type == "agent_end":
            base_event["agent"] = event.agent.name
        elif event.type == "handoff":
            base_event["from"] = event.from_agent.name
            base_event["to"] = event.to_agent.name
        elif event.type == "tool_start":
            base_event["tool"] = event.tool.name
        elif event.type == "tool_end":
            base_event["tool"] = event.tool.name
            base_event["output"] = str(event.output)
        elif event.type == "audio":
            base_event["audio"] = base64.b64encode(event.audio.data).decode("utf-8")
        elif event.type == "audio_interrupted":
            pass
        elif event.type == "audio_end":
            pass
        elif event.type == "history_updated":
            base_event["history"] = [item.model_dump(mode="json") for item in event.history]
            # Add history to context that is passed into the real-time runner
            self.ctx.history = realtime_history_to_input_list(event.history)
        elif event.type == "history_added":
            pass
        elif event.type == "guardrail_tripped":
            base_event["guardrail_results"] = [{"name": result.guardrail.name} for result in event.guardrail_results]
        elif event.type == "raw_model_event":
            base_event["raw_model_event"] = {
                "type": event.data.type,
            }
        elif event.type == "error":
            base_event["error"] = str(event.error) if hasattr(event, "error") else "Unknown error"
        else:
            assert_never(event)

        return base_event
