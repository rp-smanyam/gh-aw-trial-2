import json
import os

from agents import Agent, Runner
from agents.mcp import MCPServerStreamableHttp
from dotenv import load_dotenv

load_dotenv("../../.env")

from openai_setup import setup_openai

setup_openai()


async def main():
    async with MCPServerStreamableHttp(
        name="MCP Server",
        params={"url": "http://0.0.0.0:8042/"},
    ) as mcp_server:
        agent = Agent(
            name="Agent",
            instructions="You are leasing assistant who can help answer questions about renting. The property ID is 123.",
            mcp_servers=[mcp_server],
        )
        result = await Runner.run(agent, input="Any 2 bedroom apartments available?")

        for message in result.to_input_list():
            print(json.dumps(message, indent=2))


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
