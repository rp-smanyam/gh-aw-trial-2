from cashews import cache

from agent_leasing.clients.mcp import CachingMCPServer
from agent_leasing.settings import settings


class TestCachedMCPTools:
    async def test_mcp_property_tool_caching(self, resident_context_chat_ll):
        """
        Verify that an empty cache results in a call to the MCP server via `call_tool`.
        A second call to `call_tool` should result in a cached response.
        """
        async with CachingMCPServer(
            name="Caching MCP Server",
            params={"url": settings.knock_mcp_server},
            cacheable_tools=["get_property_overview"],
            context=resident_context_chat_ll,
        ) as property_mcp_server:
            # Make first call
            result1 = await property_mcp_server.call_tool(
                "get_property_overview", {"property_id": 1, "renter_type": "resident"}
            )
            assert "Cassidy South Apartments" in result1.content[0].text

            # Verify the result was cached
            cached_tool_output = await cache.get('get_property_overview:{"property_id": 1, "renter_type": "resident"}')
            assert "Cassidy South Apartments" in cached_tool_output

            # Make second call with same parameters
            result2 = await property_mcp_server.call_tool(
                "get_property_overview", {"property_id": 1, "renter_type": "resident"}
            )

            # Verify second call returns same result (from cache)
            assert "Cassidy South Apartments" in result2.content[0].text
            assert result1.content[0].text == result2.content[0].text
