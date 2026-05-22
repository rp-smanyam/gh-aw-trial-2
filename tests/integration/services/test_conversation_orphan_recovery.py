"""Integration tests for OpenAI Conversations API orphan-function-call recovery.

Issue #1569: when an input guardrail trips while the model task is concurrently
running with `conversation_id` set, OpenAI persists the model's emitted
`function_call` to the server-side conversation but no `function_call_output`
ever follows. Subsequent turns chained on that conversation 400 with
"No tool output found for function call <id>".

These tests construct the orphan state directly by issuing a Responses API
call with tools, observing a `function_call` in the response, and deliberately
not posting its `function_call_output`. That reproduces the same broken state
the SDK race produces, without needing to reproduce the race itself.

Test 1 pins the bug: a follow-up call on a conversation with an orphan
function_call raises `BadRequestError` with the expected message.
Test 2 validates the fix: after `clean_orphan_function_calls` is run, the
follow-up call succeeds.
"""

from __future__ import annotations

import pytest
from openai import AsyncOpenAI, BadRequestError

from agent_leasing.clients.openai import get_openai_client
from agent_leasing.services.agent_service import clean_orphan_function_calls

# Use a small, fast model so the test is cheap. The bug is server-side
# conversation state, not model-specific.
MODEL = "gpt-5.4-nano"

WEATHER_TOOL = {
    "type": "function",
    "name": "get_weather",
    "description": "Get the current weather for a city.",
    "parameters": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name"},
        },
        "required": ["city"],
        "additionalProperties": False,
    },
    "strict": True,
}


async def _produce_orphan_function_call(client: AsyncOpenAI, conversation_id: str) -> str:
    """Drive a Responses API turn that emits a function_call, then return its
    call_id without posting a function_call_output. Leaves the conversation
    in the corrupted state Issue #1569 describes."""
    response = await client.responses.create(
        model=MODEL,
        conversation=conversation_id,
        input=[{"role": "user", "content": "What's the weather in Boston?"}],
        tools=[WEATHER_TOOL],
    )
    function_calls = [item for item in response.output if item.type == "function_call"]
    assert function_calls, (
        f"Expected the model to emit a function_call for the weather prompt; got "
        f"{[item.type for item in response.output]}"
    )
    return function_calls[0].call_id


@pytest.fixture
async def conversation():
    """Create a fresh OpenAI conversation for the test and delete it after."""
    client = get_openai_client()
    conv = await client.conversations.create()
    try:
        yield conv.id
    finally:
        try:
            await client.conversations.delete(conv.id)
        except Exception:
            pass  # cleanup-only; don't mask real failures


async def test_orphan_function_call_breaks_subsequent_turns(conversation):
    """Pin the bug from #1569: an orphaned function_call in a server-side
    conversation causes every subsequent Responses API call on that
    conversation to 400 with "No tool output found for function call <id>".
    """
    client = get_openai_client()

    orphan_call_id = await _produce_orphan_function_call(client, conversation)

    with pytest.raises(BadRequestError) as exc_info:
        await client.responses.create(
            model=MODEL,
            conversation=conversation,
            input=[{"role": "user", "content": "Never mind, just say hi."}],
            tools=[WEATHER_TOOL],
        )

    err = exc_info.value
    assert err.status_code == 400
    assert err.type == "invalid_request_error"
    assert "No tool output found" in str(err)
    assert orphan_call_id in str(err)


async def test_clean_orphan_function_calls_restores_conversation(conversation):
    """Validate the fix: clean_orphan_function_calls deletes the orphan, then
    a follow-up Responses API call on the same conversation succeeds."""
    client = get_openai_client()

    orphan_call_id = await _produce_orphan_function_call(client, conversation)

    deleted = await clean_orphan_function_calls(conversation)
    assert deleted == 1, f"Expected to delete 1 orphan, deleted {deleted}"

    # Conversation should no longer contain the orphan call_id
    remaining_call_ids = [
        getattr(item, "call_id", None)
        async for item in client.conversations.items.list(conversation, limit=20, order="desc")
        if item.type == "function_call"
    ]
    assert orphan_call_id not in remaining_call_ids

    # Follow-up turn now succeeds
    response = await client.responses.create(
        model=MODEL,
        conversation=conversation,
        input=[{"role": "user", "content": "Never mind, just say hi."}],
        tools=[WEATHER_TOOL],
    )
    assert response.output, "Follow-up turn returned empty output"


async def test_clean_orphan_function_calls_noop_on_clean_conversation(conversation):
    """A clean conversation (no orphan) should result in zero deletions and
    no errors. This covers the call site invoking cleanup defensively after
    every guardrail trip even when no corruption occurred."""
    client = get_openai_client()

    # Send a turn with a tool, then post the function_call_output so the
    # conversation is in a healthy state.
    response = await client.responses.create(
        model=MODEL,
        conversation=conversation,
        input=[{"role": "user", "content": "What's the weather in Boston?"}],
        tools=[WEATHER_TOOL],
    )
    function_call = next(item for item in response.output if item.type == "function_call")
    await client.responses.create(
        model=MODEL,
        conversation=conversation,
        input=[
            {
                "type": "function_call_output",
                "call_id": function_call.call_id,
                "output": '{"temperature": "72F", "conditions": "sunny"}',
            }
        ],
        tools=[WEATHER_TOOL],
    )

    deleted = await clean_orphan_function_calls(conversation)
    assert deleted == 0


async def test_clean_orphan_function_calls_empty_conversation_id():
    """Defensive: cleanup should be a no-op when called with empty conversation_id."""
    assert await clean_orphan_function_calls("") == 0
    assert await clean_orphan_function_calls(None) == 0  # type: ignore[arg-type]
