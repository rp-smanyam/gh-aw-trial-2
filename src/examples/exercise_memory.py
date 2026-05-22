import asyncio

from agent_leasing.settings import settings
from agent_leasing.util import memory


async def main():
    # settings.redis_enabled = False

    # Example of local redis started with `docker run -d --name redis -p 6379:6379 redis`
    # REDIS_ENABLED=False
    # REDIS=redis://0.0.0.0
    # Local redis-cli: redis-cli -h 127.0.0.1 -p 6379
    settings.redis_enabled = True
    await memory.put("A", 1)
    value = await memory.get("A", 1)
    print(value)


if __name__ == "__main__":
    asyncio.run(main())
