from contextlib import asynccontextmanager
from typing import AsyncGenerator

import pytest
from cashews import cache
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from agent_leasing.server import app
from agent_leasing.util.memory import setup_cache

# Initialize cache once for all tests
setup_cache()


@pytest.fixture(autouse=True)
async def clear_cache():
    yield
    await cache.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
async def aclient() -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as aclient:
        yield aclient


@asynccontextmanager
async def create_test_client():
    """Create an AsyncClient for testing. Use this inside mock contexts."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client
