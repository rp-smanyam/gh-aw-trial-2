import json

from agents import (
    Agent,
    ItemHelpers,
    RunContextWrapper,
    Runner,
    function_tool,
    gen_trace_id,
    trace,
)
from agents.tracing.util import gen_group_id
from dotenv import load_dotenv

load_dotenv("../../.env")

from openai_setup import setup_openai

setup_openai()


@function_tool(description_override="speak like a pirate")
async def gen_z_tool(wrapper: RunContextWrapper, input: str):
    tool_agent = Agent(
        name="Agent",
        instructions="speak like Gen-Z",
    )
    result = await Runner.run(tool_agent, input=input)
    return ItemHelpers.text_message_outputs(result.new_items)


async def main():
    agent = Agent(
        name="Agent",
        instructions="You are a helpful agent. If asked to speak like Gen Z use the `gen_z_tool` tool.",
        tools=[gen_z_tool],
    )
    trace_id = gen_trace_id()
    group_id = gen_group_id()
    with trace(trace_id=trace_id, group_id=group_id, workflow_name="Test workflow"):
        result = await Runner.run(agent, input="Speak like Gen Z")

    for message in result.to_input_list():
        print(json.dumps(message, indent=2))


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
