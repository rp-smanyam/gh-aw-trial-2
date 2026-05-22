import pytest
from agents import TResponseInputItem

from agent_leasing.util import memory


async def test_memory_retain_tool_call_pairs():
    """
    Test to ensure that tool call pairs are retained in memory.

    If the pairs are split then OpenAI will get confused and throw an error.
    """
    input_items: list[TResponseInputItem] = [
        {"role": "user", "content": "hello"},
        {
            "arguments": '{"prospect_id":"95946","first_name":"","last_name":"","desired_move_in_date":"","bedrooms_number":0}',
            "call_id": "call_csrnMu280MilHAzWVj0O6lUE",
            "name": "update_prospect",
            "type": "function_call",
            "id": "fc_682e97c3f8dc819882f3b5d1d8e177b8093c55b70bdf7361",
            "status": "completed",
        },
        {
            "call_id": "call_csrnMu280MilHAzWVj0O6lUE",
            "output": '{"type":"text","text":"Prospect updated","annotations":null}',
            "type": "function_call_output",
        },
    ]
    await memory.put_input_items("1", input_items)

    # Request a history of 2
    filtered_input_items = await memory.get_input_items("1", 2)

    # Get a history of 2, because we encountered a tool pair
    assert len(filtered_input_items) == 2


async def test_memory_cull_old_tool_calls():
    input_items: list[TResponseInputItem] = [
        {"role": "user", "content": "hello"},
        {
            "arguments": '{"prospect_id":"95946","first_name":"","last_name":"","desired_move_in_date":"","bedrooms_number":0}',
            "call_id": "call_csrnMu280MilHAzWVj0O6lUE",
            "name": "update_prospect",
            "type": "function_call",
            "id": "fc_682e97c3f8dc819882f3b5d1d8e177b8093c55b70bdf7361",
            "status": "completed",
        },
        {
            "call_id": "call_csrnMu280MilHAzWVj0O6lUE",
            "output": '{"type":"text","text":"Prospect updated","annotations":null}',
            "type": "function_call_output",
        },
        {
            "arguments": '{"prospect_id":"95946","first_name":"","last_name":"","desired_move_in_date":"","bedrooms_number":0}',
            "call_id": "call_csrnMu280MilHAzWVj0O6lUD",
            "name": "update_prospect",
            "type": "function_call",
            "id": "fc_682e97c3f8dc819882f3b5d1d8e177b8093c55b70bdf7361",
            "status": "completed",
        },
        {
            "call_id": "call_csrnMu280MilHAzWVj0O6lUD",
            "output": '{"type":"text","text":"Prospect updated","annotations":null}',
            "type": "function_call_output",
        },
    ]
    await memory.put_input_items("1", input_items)

    # Request a history of 3
    filtered_input_items = await memory.get_input_items("1", 3)

    # Get a history of 2, because one tool pair was split
    assert len(filtered_input_items) == 2


@pytest.mark.asyncio
async def test_get_input_items_removes_orphan_tool_call():
    # Only one tool call, no mate
    input_items: list[TResponseInputItem] = [
        {"role": "user", "content": "hello"},
        {
            "call_id": "call_123",
            "type": "function_call",
            "arguments": "{}",
        },
    ]
    await memory.put_input_items("test_orphan", input_items)
    result = await memory.get_input_items("test_orphan", 2)
    # Should remove the orphan tool call, leaving only the user message
    assert result == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_get_input_items_retains_tool_call_pair():
    # Tool call and its mate present
    input_items: list[TResponseInputItem] = [
        {"role": "user", "content": "hello"},
        {
            "call_id": "call_abc",
            "type": "function_call",
            "arguments": "{}",
        },
        {
            "call_id": "call_abc",
            "type": "function_call_output",
            "output": "result",
        },
    ]
    await memory.put_input_items("test_pair", input_items)
    result = await memory.get_input_items("test_pair", 3)
    # Should retain all items since the pair is complete
    assert result == input_items


@pytest.mark.asyncio
async def test_get_input_items_history_limit():
    # More items than preferred_max_history
    input_items: list[TResponseInputItem] = [{"role": "user", "content": f"msg{i}"} for i in range(5)] + [
        {
            "call_id": "call_xyz",
            "type": "function_call",
            "arguments": "{}",
        },
        {
            "call_id": "call_xyz",
            "type": "function_call_output",
            "output": "result",
        },
    ]
    await memory.put_input_items("test_limit", input_items)
    result = await memory.get_input_items("test_limit", 4)
    # Should only return the last 4 items, and retain tool call pair if present
    assert len(result) == 4
    assert result[0]["content"] == "msg3"  # The oldest message in the returned history
    # Should contain the tool call pair if both are within the last 4
    call_ids = [item.get("call_id") for item in result if "call_id" in item]
    assert call_ids.count("call_xyz") in (0, 2)  # Either both or none


@pytest.mark.asyncio
async def test_get_input_items_empty():
    # No items in memory
    result = await memory.get_input_items("empty_key", 5)
    assert result == []


@pytest.mark.asyncio
async def test_get_input_items_multiple_orphans():
    # Multiple orphan tool calls
    input_items: list[TResponseInputItem] = [
        {"role": "user", "content": "hello"},
        {"call_id": "call_1", "type": "function_call"},
        {"call_id": "call_2", "type": "function_call"},
    ]
    await memory.put_input_items("test_multi_orphan", input_items)
    result = await memory.get_input_items("test_multi_orphan")
    # Should remove both orphan tool calls
    assert result == [{"role": "user", "content": "hello"}]
