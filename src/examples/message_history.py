import json
import os

import httpx
from agents import Agent, RunContextWrapper, Runner, function_tool
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv("../../.env")

from openai_setup import setup_openai

setup_openai()


class LocalContext(BaseModel):
    property_id: str = "123"
    prospect_id: str = "456"


@function_tool
async def get_property_data(wrapper: RunContextWrapper[LocalContext], property_id: str) -> str:
    """Get general marketing information about a property."""
    return "The property name is Dog Free Paradise. Cats are allowed but not dogs."


def agent_instructions(run_context: RunContextWrapper[LocalContext], agent: Agent[LocalContext]) -> str:
    """
    Create a dynamic prompt for the renter agent and injects data from the context.
    """
    context = run_context.context
    prompt = f"""You are leasing assistant who can help answer questions about renting.

    # Renter Settings
    - property_id: {context.property_id} (Property ID) 
    - prospect_id: {context.prospect_id} (Prospect ID) 
    """
    return prompt


async def main():
    agent = Agent(name="Agent", instructions=agent_instructions, tools=[get_property_data])

    # context is normally pulled from memory (in-memory or could be redis) in server.py
    # anything can be in here
    context = LocalContext(property_id="123")

    result = await Runner.run(agent, input="Do you allow cats?", context=context)

    result = await Runner.run(
        agent, input="Do you allow giraffes?", context=context, previous_response_id=result.last_response_id
    )

    # Show history using response ID
    api_base = os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    async with httpx.AsyncClient(timeout=60.0) as http_client:
        response = await http_client.get(
            f"{api_base}/responses/{result.last_response_id}/input_items",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"},
        )
        input_items = response.json()["data"]
        print(f"input_items: {json.dumps(input_items, indent=2)}")

    print(result.final_output)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
