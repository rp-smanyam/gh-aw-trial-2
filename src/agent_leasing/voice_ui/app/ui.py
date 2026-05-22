"""
This server is only used as a backend for a developer UI to test real-time voice and does not get deployed.
See the README in src/agent_leasing/realtime for information about how to use this server. Voice calls
will come in from Twilio or elsewhere to the /realtime-incoming-call endpoint, after which a websocket connection
will be established with Twilio.
"""

import json
import os
import struct
from contextlib import asynccontextmanager

import structlog
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agent_leasing.settings import settings
from agent_leasing.voice_ui.voice_ui_handler import RealtimeWebSocketManager

load_dotenv()

logger = structlog.get_logger(__name__)


manager = RealtimeWebSocketManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


voice_ui_app = FastAPI(lifespan=lifespan)


@voice_ui_app.websocket("/websocket/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    agent_name: str | None = None,
):
    await websocket.accept()
    logger.info(f"Connected to {websocket.url}")
    logger.info(f"Agent name: {agent_name}")

    # Wait for the first message containing the AskRequest payload
    try:
        first_message = await websocket.receive_text()
        first_data = json.loads(first_message)

        if first_data.get("type") != "ask_request":
            logger.error(f"Expected 'ask_request' as first message, got: {first_data.get('type')}")
            await websocket.close(code=1008, reason="First message must be ask_request")
            return

        ask_request_data = first_data.get("data")
        if not ask_request_data:
            logger.error("Missing 'data' field in ask_request message")
            await websocket.close(code=1008, reason="Missing ask_request data")
            return

        logger.info(
            f"Received AskRequest payload for property: {ask_request_data.get('product_info', {}).get('knock_property_id')}"
        )

    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in first message: {e}")
        await websocket.close(code=1008, reason="Invalid JSON")
        return
    except Exception as e:
        logger.error(f"Error receiving ask_request: {e}")
        await websocket.close(code=1008, reason="Error processing ask_request")
        return

    # Now connect with the ask_request data
    await manager.connect(websocket, session_id, agent_name, ask_request_data)

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            if message["type"] == "audio":
                # Convert int16 array to bytes
                int16_data = message["data"]
                audio_bytes = struct.pack(f"{len(int16_data)}h", *int16_data)
                await manager.send_audio(session_id, audio_bytes)
            elif message["type"] == "text":
                # Send text message to the realtime session
                text = message["data"]
                await manager.send_text(session_id, text)

    except WebSocketDisconnect:
        await manager.disconnect(session_id)


STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static")
EXAMPLE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "api",
    "example_data",
    "resident",
    "chat",
)

try:
    # Serve static assets (app.js, css) from /static to avoid shadowing API routes.
    voice_ui_app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
except RuntimeError as e:
    logger.error(f"Error mounting static files: {e}")


@voice_ui_app.get("/")
async def read_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@voice_ui_app.get("/example_payload", response_class=JSONResponse)
async def read_example_payload(request: Request):
    """Serve the sample ask_request payload with product set for voice.

    Optional query param: ?flavor=alpha|beta to override the default.
    """
    chosen_flavor = (request.query_params.get("flavor") or settings.example_payload_flavor or "alpha").lower()
    filename = f"example_ask_request_ll.{chosen_flavor}.json"
    candidate = os.path.join(EXAMPLE_DIR, filename)

    try:
        with open(candidate, encoding="utf-8") as f:
            payload = json.load(f)
        payload["product"] = "resident_one_voice"
        return payload
    except Exception as e:
        logger.error(f"Error loading example payload ({candidate}): {e}")
        return JSONResponse({"error": "Unable to load example payload"}, status_code=500)
