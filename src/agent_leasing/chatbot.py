"""
Chatbot that runs inside the FastAPI server.
"""

import json
import uuid
from copy import deepcopy
from pathlib import Path

import httpx
import structlog
from fastapi import FastAPI
from httpx import AsyncHTTPTransport
from nicegui import app, ui

from agent_leasing.api.model import Product, examples
from agent_leasing.settings import settings

logger = structlog.getLogger()

transport = AsyncHTTPTransport(retries=3)


def _load_test_payload() -> tuple[dict, str]:
    """Load test payload from settings or use default.

    Returns:
        Tuple of (payload_dict, source_description)
    """
    candidate_path = None
    if settings.chatbot_test_payload:
        candidate_path = settings.chatbot_test_payload
    elif settings.example_payload_flavor:
        candidate_path = (
            Path(__file__).resolve().parent
            / "api"
            / "example_data"
            / "resident"
            / "chat"
            / (f"example_ask_request_ll.{settings.example_payload_flavor}.json")
        )

    if candidate_path:
        try:
            payload_text = Path(candidate_path).read_text(encoding="utf-8")
            payload_dict = json.loads(payload_text)
            if not isinstance(payload_dict, dict):
                raise ValueError("chatbot_test_payload must be a JSON object")
            logger.info(f"Using test payload from {candidate_path}")
            return payload_dict, f"Payload from {candidate_path}"
        except Exception as e:
            logger.error(f"Failed to load test payload from {candidate_path}: {e}. Using default.")
    return deepcopy(examples.ASK_REQUEST_RESIDENT_CHAT_LL), "Sample payload"


# Load default payload once at module level for reuse
_default_payload, _payload_source = _load_test_payload()

# Start with a default ask_request
ask_request = deepcopy(_default_payload)


def init(fastapi_app: FastAPI) -> None:
    """Chatbot backed by REST API on the same server"""

    @ui.page("/")
    async def show():
        # Clear nicegui cache
        app.storage.user["previous_response_id"] = None

        ask_request["product"] = Product.RESIDENT_ONE_CHAT.value  # Make this the default for the chatbot
        ask_request["chat_session_id"] = uuid.uuid4().hex

        # Streaming state
        streaming_enabled = {"value": False}

        async def send():
            """React to prospect_simple input"""
            with chat_container:
                ui.chat_message(text.value, name="You", stamp="now").props("bg-color=blue-2")

            ask_request["prompt"] = text.value
            # Update the JSON editor to show the prompt
            update_editor(json_editor, ask_request, {})

            try:
                if streaming_enabled["value"]:
                    # Streaming mode
                    await handle_streaming_request()
                else:
                    # Non-streaming mode (existing behavior)
                    await handle_normal_request()
            except Exception:
                with chat_container:
                    ui.chat_message(
                        f"Encountered an error calling {settings.ask_endpoint} Try your question again."
                    ).props("bg-color=red-2")
                logger.exception(f"Error calling {settings.ask_endpoint}")

            text.value = ""

        async def handle_normal_request():
            """Handle non-streaming request"""
            async with httpx.AsyncClient(transport=transport, timeout=180.0) as client:
                # Allows us to maintain message history
                # previous_response_id = app.storage.user.get("previous_response_id")
                headers = {}
                # if previous_response_id:
                #    headers.update(
                #        {"X-OpenAI-Previous-Response-Id": previous_response_id}
                #    )
                result = await client.post(
                    settings.ask_endpoint,
                    json=ask_request,
                    headers=headers,
                    timeout=180.0,
                )
                previous_response_id = result.headers.get("X-OpenAI-Previous-Response-Id")
                # app.storage.user["previous_response_id"] = previous_response_id

                try:
                    result.raise_for_status()
                    response_json = result.json()
                    chat_json = (response_json.get("content") or {}).get("chat", "{}")
                    response_text = json.loads(chat_json).get("response", "No response found.")
                except Exception:
                    logger.exception("Failed to parse response content")
                    response_text = result.text  # fallback

                with chat_container:
                    # Group message and trace info together so they maintain order
                    with ui.column():
                        # Check if product is email to render as HTML
                        is_email = ask_request.get("product", "").endswith("_email")
                        if is_email:
                            # For email, use HTML rendering in a custom container
                            with ui.element("div").classes("bg-green-2 p-3 rounded"):
                                ui.html(response_text, sanitize=False)
                        else:
                            ui.chat_message(response_text, name="Agent", stamp="now").props("bg-color=green-2").style(
                                "white-space: pre-wrap"
                            )  # Fix to show the indenting in the chatbot

                        trace_id = result.headers.get("X-OpenAPI-Trace-Id")
                        langsmith_trace_url = result.headers.get("X-LangSmith-Trace-Url")
                        process_time = result.headers.get("X-Process-Time")
                        product = result.headers.get("X-RealPage-Product")
                        agent = result.headers.get("X-RealPage-Agent")
                        flows = result.headers.get("X-RealPage-Flows")
                        language = result.headers.get("X-RealPage-Language")
                        if trace_id:
                            with (
                                ui.element()
                                .classes("inline")
                                .style("font-style: italic; color: #666967; font-size: 75%")
                            ):
                                ui.label("OpenAI Trace Id: ").classes("inline")
                                ui.link(
                                    trace_id,
                                    f"https://platform.openai.com/traces/trace?trace_id={trace_id}",
                                    new_tab=True,
                                )
                                if langsmith_trace_url:
                                    ui.space()
                                    ui.label("LangSmith Trace: ").classes("inline")
                                    ui.link(
                                        langsmith_trace_url,
                                        langsmith_trace_url,
                                        new_tab=True,
                                    )
                                if process_time:
                                    ui.space()
                                    ui.label(f" Elapsed: {process_time}").classes("inline")
                                if previous_response_id:
                                    ui.space()
                                    ui.label(" Response ID: ").classes("inline")
                                    ui.link(
                                        previous_response_id,
                                        f"https://platform.openai.com/logs/{previous_response_id}",
                                        new_tab=True,
                                    )
                                conversation_id = result.headers.get("X-OpenAI-Conversation-Id")
                                if conversation_id:
                                    ui.space()
                                    ui.label(" Conversation ID: ").classes("inline")
                                    ui.link(
                                        conversation_id,
                                        f"https://platform.openai.com/logs/{conversation_id}",
                                        new_tab=True,
                                    )
                                if product:
                                    ui.space()
                                    ui.label(f" Product: {product}").classes("inline")
                                if agent:
                                    ui.space()
                                    ui.label(f" Agent: {agent}").classes("inline")
                                if flows:
                                    ui.space()
                                    ui.label(f" Flows: {flows}").classes("inline")
                                if language:
                                    ui.space()
                                    ui.label(f" Language: {language}").classes("inline")

        async def handle_streaming_request():
            """Handle streaming request with SSE"""
            # Create streaming request payload
            streaming_request = deepcopy(ask_request)
            streaming_request["message"] = {"content": ask_request["prompt"], "message_id": ""}

            # Build streaming endpoint URL
            stream_endpoint = settings.ask_endpoint.replace("/v1/agent/ask", "/v1/agent/stream")

            # Check if product is email to render as HTML
            is_email = ask_request.get("product", "").endswith("_email")

            response_text = ""
            message_element = None
            elapsed_ms = None
            msg_column = None

            async with httpx.AsyncClient(transport=transport, timeout=180.0) as client:
                async with client.stream(
                    "POST",
                    stream_endpoint,
                    json=streaming_request,
                    headers={},
                    timeout=180.0,
                ) as response:
                    response.raise_for_status()

                    # Create message container once with loading indicator
                    with chat_container:
                        msg_column = ui.column()
                        with msg_column:
                            # Use HTML for email products, markdown for others
                            if is_email:
                                message_element = ui.html("", sanitize=False)
                            else:
                                message_element = ui.markdown("")
                            message_element.classes("bg-green-2 p-3 rounded thinking")

                    # Add CSS for pulsating animation
                    ui.add_head_html("""
                    <style>
                        @keyframes pulse {
                            0%, 100% { opacity: 1; }
                            50% { opacity: 0.5; }
                        }
                        .thinking {
                            animation: pulse 1.5s ease-in-out infinite;
                        }
                    </style>
                    """)

                    first_content_received = False

                    # Process SSE stream
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue

                        data_str = line[6:]  # Remove "data: " prefix

                        if data_str == "[DONE]":
                            break

                        try:
                            event_data = json.loads(data_str)
                            content = event_data.get("content", "")
                            status = event_data.get("status", "")
                            phase = event_data.get("phase", "")
                            elapsed = event_data.get("elapsed")

                            # Track elapsed time
                            if elapsed is not None:
                                elapsed_ms = elapsed

                            # Show phase if we haven't received content yet
                            if not first_content_received and phase and not content:
                                phase_text = f"*{phase}...*" if phase else ""
                                if message_element and phase_text:
                                    message_element.set_content(phase_text)

                            if content and phase == "generating":
                                response_text += content
                                # Update the message in place
                                if message_element:
                                    # Remove pulsating class on first content
                                    if not first_content_received:
                                        message_element.classes(remove="thinking")
                                        first_content_received = True
                                    # First remove SSE chunk terminators (\n\n), then convert \n to line breaks
                                    clean_text = response_text.replace("\n\n", "").replace("\\n", "\n")
                                    message_element.set_content(clean_text)

                            if status == "done":
                                break

                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse SSE data: {data_str}")
                            continue

                    # After streaming completes, show elapsed time
                    if elapsed_ms is not None and msg_column is not None:
                        with msg_column:
                            with (
                                ui.element()
                                .classes("inline")
                                .style("font-style: italic; color: #666967; font-size: 75%")
                            ):
                                ui.label(f" Elapsed: {elapsed_ms}ms").classes("inline")

        ui.page_title("Chatbot")

        with ui.header(elevated=True).style("background-color: #adadad; padding: 0.5rem 1rem; min-height: 32px;"):
            with ui.row().classes("w-full justify-between items-center"):
                ui.label("Agent Leasing Chatbot").style("font-size: 1em; font-weight: bold;")
                ui.html(
                    '<a href="/voice-ui" style="color: #0066cc; text-decoration: none; font-weight: 500; '
                    'border: 1px solid #0066cc; border-radius: 6px; padding: 0.25rem 0.75rem;">Voice UI</a>',
                    sanitize=False,
                )

        with ui.splitter() as splitter:
            with splitter.before:
                with ui.card():
                    text = (
                        ui.input(label="Chat", placeholder="Start typing").props("size=80").on("keydown.enter", send)
                    )
                    streaming_checkbox = (
                        ui.checkbox(
                            "Streaming", value=False, on_change=lambda e: streaming_enabled.update({"value": e.value})
                        )
                        .props("dense")
                        .style("font-size: 0.85em; padding: 0;")
                    )
                    # Container for chat messages with reversed order (latest first)
                    chat_container = ui.column().style("display: flex; flex-direction: column-reverse;")
            with splitter.after:
                with ui.card():

                    def update_editor(editor, backing_dict, changed_dict):
                        """Update JSON in editor"""
                        backing_dict.update(changed_dict)
                        app.storage.user["ask_request"] = backing_dict
                        # Use run_editor_method to update the json_editor's content
                        # editor.update() only re-renders but doesn't change content
                        editor.run_editor_method("update", {"json": backing_dict})

                    def reset_session():
                        """Reset only the chat_session_id"""
                        ask_request["chat_session_id"] = uuid.uuid4().hex
                        update_editor(json_editor, ask_request, {})

                    product_examples = {
                        Product.RESIDENT_ONE_CHAT.value: _default_payload,
                        # Add other products here when examples are available
                    }

                    def handle_product_change(e):
                        """Update the ask_request when the product changes."""
                        new_product = e.value
                        # Default to loaded payload if the selected product doesn't have a specific example
                        example_request = product_examples.get(new_product, _default_payload)
                        ask_request.clear()
                        ask_request.update(deepcopy(example_request))
                        update_dict = {
                            "product": new_product,
                            "chat_session_id": uuid.uuid4().hex,
                        }
                        update_editor(json_editor, ask_request, update_dict)

                        # Enable/disable streaming based on product
                        if new_product == Product.RESIDENT_ONE_CHAT.value:
                            streaming_checkbox.enable()
                        else:
                            streaming_checkbox.disable()
                            streaming_checkbox.value = False
                            streaming_enabled["value"] = False

                    def update_knock_property_id(e):
                        """Update knock_property_id in the request"""
                        if "product_info" not in ask_request:
                            ask_request["product_info"] = {}
                        ask_request["product_info"]["knock_property_id"] = e.value
                        update_editor(json_editor, ask_request, {})

                    def update_knock_resident_id(e):
                        """Update knock_resident_id in the request"""
                        if "product_info" not in ask_request:
                            ask_request["product_info"] = {}
                        ask_request["product_info"]["knock_resident_id"] = e.value
                        update_editor(json_editor, ask_request, {})

                    def update_uc_company_id(e):
                        """Update uc_company_id in the request"""
                        if "product_info" not in ask_request:
                            ask_request["product_info"] = {}
                        if "uc_company_id" not in ask_request["product_info"]:
                            ask_request["product_info"]["uc_company_id"] = {"source": "OS"}
                        ask_request["product_info"]["uc_company_id"]["id"] = e.value
                        update_editor(json_editor, ask_request, {})

                    # First row: Agent selector and knock_property_id
                    with ui.row().classes("w-full gap-2 items-center"):
                        ui.select(
                            [
                                Product.RESIDENT_ONE_CHAT.value,
                                Product.RESIDENT_ONE_EMAIL.value,
                                Product.RESIDENT_ONE_SMS.value,
                                Product.SIMPLE.value,
                            ],
                            value=Product.RESIDENT_ONE_CHAT.value,
                            label="Agent (aka product)",
                            on_change=handle_product_change,
                        ).props("outlined dense")

                        ui.input(
                            label="knock_property_id",
                            value=ask_request.get("product_info", {}).get("knock_property_id", ""),
                            on_change=update_knock_property_id,
                        ).props("outlined dense")

                    # Second row: knock_resident_id, uc_company_id, and Reset button
                    with ui.row().classes("w-full gap-2 items-center"):
                        ui.input(
                            label="knock_resident_id",
                            value=ask_request.get("product_info", {}).get("knock_resident_id", ""),
                            on_change=update_knock_resident_id,
                        ).props("outlined dense")

                        ui.input(
                            label="uc_company_id",
                            value=ask_request.get("product_info", {}).get("uc_company_id", {}).get("id", ""),
                            on_change=update_uc_company_id,
                        ).props("outlined dense")

                        ui.button("Reset Session", on_click=reset_session).props("outline color=primary")

                    ui.label(_payload_source).style("font-size: 0.8em; color: gray;")
                    json_editor = ui.json_editor(
                        {"content": {"json": ask_request}},
                        on_change=lambda e: update_editor(json_editor, ask_request, e.content["json"]),
                    )

    ui.run_with(
        fastapi_app,
        mount_path="/chatbot",
        storage_secret="123",
        reconnect_timeout=60.0,
    )
