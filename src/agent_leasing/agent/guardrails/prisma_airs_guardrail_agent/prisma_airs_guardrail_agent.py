import asyncio  # noqa - to prevent linter from removing
import json
from typing import Any, Final

import aiohttp
import structlog
from agents import (
    GuardrailFunctionOutput,
    RunContextWrapper,
    TResponseInputItem,
    input_guardrail,
    output_guardrail,
)
from pydantic import BaseModel

from agent_leasing.agent.guardrails.text_utils import (
    extract_text_from_input,
    extract_text_from_output,
)
from agent_leasing.agent.util import AgentArchitecture, get_architecture_from_context, get_channel_from_context
from agent_leasing.models.context import SessionScope
from agent_leasing.settings import settings
from agent_leasing.util.language_utils import localize_guardrail_response

DEFAULT_TIMEOUT_SECONDS: Final[int] = 30

logger = structlog.getLogger()


_BLOCK_MESSAGE: Final[str] = (
    "I'm sorry, but I cannot provide information or advice about offensive, illegal, or harmful activities. How else can I assist you today?"
)


class PrismaAirsGuardrailOutput(BaseModel):
    """Output type for the Prisma AIRS guardrail."""

    reasoning: str | None = None
    flagged_categories: list[str]
    is_harmful: bool
    safe_response: str = _BLOCK_MESSAGE

    @property
    def labels(self) -> list[str]:
        return self.flagged_categories


async def _check_content_safety_prisma_airs(
    original_content: str | list[TResponseInputItem] | object,
    content_type: str,  # "input" or "output"
    context: SessionScope,
) -> GuardrailFunctionOutput:
    """Common helper that checks if content is safe using Prisma AIRS API.

    Args:
        original_content: The content to moderate (input or output)
        content_type: Either "input" or "output" for logging purposes
        context: Session context (used to get the prompt for output guardrails)
    """
    try:
        payload = _parse_inputs_to_payload(original_content, content_type, context)

        # Skip API call if content is empty - Prisma AIRS requires non-empty prompt or response
        if _is_payload_empty(payload):
            logger.info(f"Skipping Prisma AIRS {content_type} check: no content to evaluate. payload: {payload}")
            return GuardrailFunctionOutput(
                output_info=original_content,
                tripwire_triggered=False,
            )

        log_text = _extract_log_text_from_payload(payload)

        logger.debug(f"Moderation check for {content_type}: {log_text[:100]}...")

        # Call Prisma AIRS API
        prisma_response = await _prisma_airs_api_call(payload)

        # Get the recommended action from Prisma AIRS response
        is_harmful, recommended_action, flagged_categories = _parse_response_to_output(prisma_response)

        # Log the Prisma AIRS action
        logger.debug(f"Prisma AIRS action: {recommended_action}")

        # Create output
        if is_harmful:
            output = await _handle_harmful_case(
                is_harmful,
                recommended_action,
                flagged_categories,
                log_text,
                content_type,
                original_content,
                context.language_code,
            )
            return GuardrailFunctionOutput(
                output_info=output,
                tripwire_triggered=is_harmful
                and settings.prisma_airs_blocking_mode,  # only block if prisma_airs_blocking_mode is True
            )

        return GuardrailFunctionOutput(
            output_info=original_content,
            tripwire_triggered=False,
        )
    except asyncio.TimeoutError:  # noqa - prevent linting from removing
        logger.warning("Prisma AIRS guardrail timed out")
        return GuardrailFunctionOutput(
            output_info=original_content,
            tripwire_triggered=False,
        )
    except Exception as e:
        error_msg = str(e) if str(e) else f"{type(e).__name__}: {repr(e)}"
        logger.warning(f"Prisma AIRS guardrail failed: {error_msg}")
        return GuardrailFunctionOutput(
            output_info=original_content,
            tripwire_triggered=False,
        )


def _parse_inputs_to_payload(
    original_content: str | list[TResponseInputItem] | object,
    content_type: str,
    context: SessionScope,
):
    if content_type == "input":
        text = extract_text_from_input(original_content)
        # For input guardrails, only send the input (no response yet)
        payload = _build_request_payload(prompt=text, response=None, context=context)
    elif content_type == "output":
        output_text = extract_text_from_output(original_content)
        # For output guardrails, send BOTH input (from context) and output
        input_text = context.ask_request.prompt if context.ask_request else ""
        payload = _build_request_payload(prompt=input_text, response=output_text, context=context)
        text = output_text  # For logging
    else:
        raise ValueError(f"Invalid content type: {content_type}")

    return payload


def _is_payload_empty(payload: dict) -> bool:
    """Check if the payload has no meaningful content to evaluate.

    Prisma AIRS API requires at least one non-empty Prompt or Response.
    Returns True if both prompt and response are empty/missing.
    """
    contents = payload.get("contents")
    if not isinstance(contents, list) or not contents:
        return True
    first_entry = contents[0]
    if not isinstance(first_entry, dict):
        return True
    prompt = (first_entry.get("prompt") or "").strip()
    response = (first_entry.get("response") or "").strip()
    return not prompt and not response


def _extract_log_text_from_payload(payload: dict) -> str:
    contents = payload.get("contents")
    if isinstance(contents, list) and contents:
        last_entry = contents[-1]
        if isinstance(last_entry, dict):
            prompt = last_entry.get("prompt") or ""
            response = last_entry.get("response") or ""
            if not prompt and not response:
                return "no content to evaluate"
            elif prompt and not response:
                return prompt[:100].strip()
            elif not prompt and response:
                return response[:100].strip()
            elif prompt and response:
                return f"Prompt: {prompt[:50]} -> Response: {response[:50]}".strip()
        return str(last_entry)[:100].strip()
    return str(contents)[:100].strip()


def _build_request_payload(context: SessionScope, prompt: str, response: str | None = None):
    channel = get_channel_from_context(context)
    airs_thread_id = context.thread_id if channel != "VOICE" else context.ask_request.product_info.call_sid

    user_id = (
        context.ask_request.product_info.knock_resident_id
        if context.ask_request and context.ask_request.product_info
        else "n/a"
    )
    model_name = (
        settings.model
        if get_architecture_from_context(context) == AgentArchitecture.RESPONDER_THINKER
        else settings.resident_one_model
    )

    payload = {
        "metadata": {
            "ai_model": model_name,
            # "app_name": settings.app_name,         # this is how you would override the dfault value in paloalto
            "deployment_environment": settings.environment,  # there already is an environment variable in the metadata
            "app_user": user_id,
        },
        "contents": [{"prompt": prompt}],
        "tr_id": airs_thread_id,
        "ai_profile": {"profile_name": settings.prisma_airs_profile_name},
    }

    if response:
        payload["contents"][0]["response"] = response
    return payload


async def _prisma_airs_api_call(payload: dict):
    if not settings.prisma_airs_api_key:
        raise ValueError("Prisma AIRS API key is not configured")
    headers = {"x-pan-token": settings.prisma_airs_api_key}
    return await _make_api_call(settings.prisma_airs_api_url, payload, headers, "Prisma AIRS")


def _parse_response_to_output(prisma_response: dict):
    recommended_action = prisma_response.get("action", "allow")
    prompt_detections = prisma_response.get("prompt_detected") or {}
    response_detections = prisma_response.get("response_detected") or {}
    flagged = {
        category
        for detections in [prompt_detections, response_detections]
        for category, detected in (detections or {}).items()
        if detected
    }
    flagged_categories = list(flagged)

    is_harmful = recommended_action.lower() == "block"

    return is_harmful, recommended_action, flagged_categories


async def _handle_harmful_case(
    is_harmful: bool,
    recommended_action: str,
    flagged_categories: list[str],
    log_text: str,
    content_type: str,
    original_content: str | list[TResponseInputItem] | object,
    language_code: str,
):
    logger.warning(
        f"Prisma AIRS {content_type} guardrail triggered: action='{recommended_action}' "
        f"for {content_type}: {log_text[:80]}..."
    )

    reasoning = f"Prisma AIRS recommended action for : {recommended_action}"
    safe_response = await localize_guardrail_response(
        base_response=_BLOCK_MESSAGE,
        guardrail_name="prisma_airs_guardrail",
        original_content=original_content,
        content_type=content_type,
        language_code=language_code,
    )

    output = PrismaAirsGuardrailOutput(
        flagged_categories=flagged_categories,
        is_harmful=is_harmful,
        safe_response=safe_response,
        reasoning=reasoning,
    )

    return output


@input_guardrail
async def prisma_airs_input_guardrail(
    ctx: RunContextWrapper[SessionScope],
    agent,
    input: str | list[TResponseInputItem],
) -> GuardrailFunctionOutput:
    """Guardrail that checks if user input is safe using Prisma AIRS API."""
    return await _check_content_safety_prisma_airs(input, "input", ctx.context)


@output_guardrail
async def prisma_airs_output_guardrail(
    ctx: RunContextWrapper[SessionScope],
    agent,
    output: str | object,
) -> GuardrailFunctionOutput:
    """Guardrail that checks if agent output is safe using Prisma AIRS API."""
    return await _check_content_safety_prisma_airs(output, "output", ctx.context)


# TODO: move this to a shared location.  This exists in other tools as well, so replace this there.  DRY, bro
async def _make_api_call(
    url: str, payload: dict, headers: dict, api_name: str, method: str = "POST"
) -> dict[str, Any]:
    """
    Helper function to make API calls with consistent error handling.

    Args:
        url: The API endpoint URL
        payload: The JSON payload to send
            api_name: Name of the API for logging (e.g., "Property ID", "Community ID")

    Returns:
        Parsed JSON response as a dictionary

    Raises:
        RuntimeError: If the API call fails or returns non-JSON response
    """
    timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method=method, url=url, json=payload, headers=headers) as response:
            body_text = await response.text()

            if response.status in (401, 403):
                logger.error(
                    f"{api_name} API call failed due to invalid API key or OAuth token. status: {response.status}",
                )
                raise RuntimeError(f"{api_name} API returned status {response.status}: Invalid API key or OAuth token")

            if response.status >= 400:
                logger.error(
                    f"{api_name} API call failed. status: {response.status}, body: {body_text}",
                )
                raise RuntimeError(f"{api_name} API returned status {response.status}")

            try:
                parsed = await response.json(content_type=None)
            except aiohttp.ContentTypeError:
                logger.warning(f"{api_name} API response not JSON, attempting manual parse")
                try:
                    parsed = json.loads(body_text or "{}")
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"{api_name} API returned a non-JSON response") from exc

            return parsed
