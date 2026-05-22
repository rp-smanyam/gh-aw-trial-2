import json

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
    return "Cats are allowed but not dogs"


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
    context = LocalContext(property_id="123")
    agent = Agent(name="Agent", instructions=agent_instructions, tools=[get_property_data])
    result = await Runner.run(agent, input="Any 2 bedroom apartments available?", context=context)

    for message in result.to_input_list():
        print(json.dumps(message, indent=2))


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
