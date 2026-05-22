import asyncio
import uuid

import structlog
from agents import Runner
from dotenv import load_dotenv

from agent_leasing.agent.simple.agent import SimpleAgent
from agent_leasing.agent.util import SessionScope
from agent_leasing.api.model import AskRequest, Product, ProductInfo

load_dotenv()

logger = structlog.getLogger()


# Build context from a payload
req = AskRequest(
    product=Product.SIMPLE.value,
    request_id=str(uuid.uuid4()),
    chat_session_id=str(uuid.uuid4()),
    prompt="hello",
    product_info=ProductInfo(knock_property_id="1"),
)
context = SessionScope(ask_request=req)


async def hello():
    """
    Say hello to a simple property agent.

    Requires a running property MCP server.
    """

    async with SimpleAgent(context) as agent_with_mcp:
        # Run the agent
        result = await Runner.run(
            agent_with_mcp.agent(),
            req.prompt,
            context=context,
        )

        logger.info(result.final_output)


def main():
    asyncio.run(hello())


if __name__ == "__main__":
    main()
