"""Example: Conversations API for message history.

Similar to message_history.py but uses the Conversations API instead of
previous_response_id.  The conversation_id is a shareable identifier that
external services can use to access the full conversation history.
"""

import json
import os

import httpx
from agents import Agent, RunContextWrapper, Runner, function_tool
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv("../../.env")

from openai_setup import get_async_client, setup_openai

setup_openai()


class LocalContext(BaseModel):
    property_id: str = "123"
    prospect_id: str = "456"


@function_tool
async def get_property_data(wrapper: RunContextWrapper[LocalContext], property_id: str) -> str:
    """Get general marketing information about a property."""
    return "The property name is Dog Free Paradise. Cats are allowed but not dogs."


def agent_instructions(run_context: RunContextWrapper[LocalContext], agent: Agent[LocalContext]) -> str:
    context = run_context.context
    prompt = f"""You are leasing assistant who can help answer questions about renting.

    # Renter Settings
    - property_id: {context.property_id} (Property ID)
    - prospect_id: {context.prospect_id} (Prospect ID)
    """
    return prompt


async def main():
    client = get_async_client()

    # Create a conversation — this ID can be shared with other services
    conversation = await client.conversations.create()
    print(f"Conversation ID: {conversation.id}")

    agent = Agent(name="Agent", instructions=agent_instructions, tools=[get_property_data])
    context = LocalContext(property_id="123")

    # First turn — pass conversation_id instead of previous_response_id
    result = await Runner.run(agent, input="Do you allow cats?", context=context, conversation_id=conversation.id)
    print(f"Turn 1: {result.final_output}")

    # Second turn — same conversation_id, history is maintained automatically
    result = await Runner.run(agent, input="Do you allow giraffes?", context=context, conversation_id=conversation.id)
    print(f"Turn 2: {result.final_output}")

    # Retrieve the conversation history using the conversation_id
    # Use the same base URL so regional routing is respected
    api_base = os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    items_url = f"{api_base}/conversations/{conversation.id}/items"
    async with httpx.AsyncClient(timeout=60.0) as http_client:
        response = await http_client.get(
            items_url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
            },
        )
        items = response.json().get("data", [])
        print(f"\nConversation history ({len(items)} items):")
        print(json.dumps(items, indent=2))


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
