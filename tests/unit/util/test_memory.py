"""Tests for agent_leasing.util.memory module."""

import pytest

from agent_leasing.models.context import SessionScope
from agent_leasing.util import memory
from agent_leasing.util.memory import setup_cache

# Setup cache once for all tests in this module
setup_cache()


class TestGet:
    """Tests for the get function."""

    @pytest.mark.asyncio
    async def test_get_returns_default_when_key_not_found(self):
        """Test that get returns default value when key doesn't exist."""
        result = await memory.get("nonexistent_key_12345", default="default_value")
        assert result == "default_value"

    @pytest.mark.asyncio
    async def test_get_returns_none_when_no_default(self):
        """Test that get returns None when key doesn't exist and no default."""
        result = await memory.get("nonexistent_key_67890")
        assert result is None


class TestPut:
    """Tests for the put function."""

    @pytest.mark.asyncio
    async def test_put_and_get_value(self):
        """Test that put stores a value that can be retrieved."""
        key = "test_put_key_abc"
        value = {"data": "test_value"}
        await memory.put(key, value)
        result = await memory.get(key)
        assert result == value

    @pytest.mark.asyncio
    async def test_put_with_custom_expire(self):
        """Test that put accepts custom expiration."""
        key = "test_put_expire_key"
        value = "expire_test"
        await memory.put(key, value, expire="1h")
        result = await memory.get(key)
        assert result == value


class TestGetInputItems:
    """Tests for the get_input_items function."""

    @pytest.mark.asyncio
    async def test_get_input_items_returns_empty_list_when_not_found(self):
        """Test that get_input_items returns empty list for nonexistent key."""
        result = await memory.get_input_items("nonexistent_input_items_key")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_input_items_returns_stored_items(self):
        """Test that get_input_items returns previously stored items."""
        key = "test_input_items_key"
        items = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        await memory.put(key, items)
        result = await memory.get_input_items(key)
        assert result == items

    @pytest.mark.asyncio
    async def test_get_input_items_respects_max_history(self):
        """Test that get_input_items limits results to preferred_max_history."""
        key = "test_max_history_key"
        items = [{"role": "user", "content": f"Message {i}"} for i in range(30)]
        await memory.put(key, items)
        result = await memory.get_input_items(key, preferred_max_history=10)
        assert len(result) == 10
        # Should return the last 10 items
        assert result[0]["content"] == "Message 20"
        assert result[-1]["content"] == "Message 29"

    @pytest.mark.asyncio
    async def test_get_input_items_removes_orphan_tool_calls(self):
        """Test that orphan tool calls (unpaired) are removed."""
        key = "test_orphan_tool_calls_key"
        items = [
            {"role": "user", "content": "Hello"},
            {"call_id": "orphan_call_1", "type": "function_call", "name": "tool1"},
            {"role": "assistant", "content": "Response"},
        ]
        await memory.put(key, items)
        result = await memory.get_input_items(key)
        # Orphan tool call should be removed
        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "Hello"}
        assert result[1] == {"role": "assistant", "content": "Response"}

    @pytest.mark.asyncio
    async def test_get_input_items_keeps_paired_tool_calls(self):
        """Test that paired tool calls are kept."""
        key = "test_paired_tool_calls_key"
        items = [
            {"role": "user", "content": "Hello"},
            {"call_id": "paired_call_1", "type": "function_call", "name": "tool1"},
            {"call_id": "paired_call_1", "type": "function_call_output", "output": "result"},
            {"role": "assistant", "content": "Response"},
        ]
        await memory.put(key, items)
        result = await memory.get_input_items(key)
        # Paired tool calls should be kept
        assert len(result) == 4
        assert result[1]["call_id"] == "paired_call_1"
        assert result[2]["call_id"] == "paired_call_1"

    @pytest.mark.asyncio
    async def test_get_input_items_handles_mixed_paired_and_orphan(self):
        """Test handling of mixed paired and orphan tool calls."""
        key = "test_mixed_tool_calls_key"
        items = [
            {"role": "user", "content": "Hello"},
            {"call_id": "paired_call", "type": "function_call", "name": "tool1"},
            {"call_id": "paired_call", "type": "function_call_output", "output": "result"},
            {"call_id": "orphan_call", "type": "function_call", "name": "tool2"},
            {"role": "assistant", "content": "Response"},
        ]
        await memory.put(key, items)
        result = await memory.get_input_items(key)
        # Orphan should be removed, paired should be kept
        assert len(result) == 4
        call_ids = [item.get("call_id") for item in result if item.get("call_id")]
        assert "paired_call" in call_ids
        assert "orphan_call" not in call_ids


class TestPutInputItems:
    """Tests for the put_input_items function."""

    @pytest.mark.asyncio
    async def test_put_input_items_stores_items(self):
        """Test that put_input_items stores items correctly."""
        key = "test_put_input_items_key"
        items = [
            {"role": "user", "content": "Test message"},
            {"role": "assistant", "content": "Test response"},
        ]
        await memory.put_input_items(key, items)
        result = await memory.get(key)
        assert result == items


class TestGetContext:
    """Tests for the get_context function."""

    @pytest.mark.asyncio
    async def test_get_context_returns_none_when_not_found(self):
        """Test that get_context returns None for nonexistent key."""
        result = await memory.get_context("nonexistent_context_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_context_returns_stored_context(self, ask_request_resident, current_time):
        """Test that get_context returns previously stored context (excluding transient fields)."""
        key = "test_context_key"
        context = SessionScope(current_time=current_time, ask_request=ask_request_resident)
        context.frustrated_user_emitted = True
        await memory.put_context(key, context)
        result = await memory.get_context(key)
        # ask_request and current_time are excluded from cache serialization
        assert result.ask_request is None
        assert result.current_time != context.current_time
        assert result.thread_id == context.thread_id
        assert result.frustrated_user_emitted is True


class TestPutContext:
    """Tests for the put_context function."""

    @pytest.mark.asyncio
    async def test_put_context_stores_context(self, ask_request_resident, current_time):
        """Test that put_context stores context correctly (excluding transient fields)."""
        key = "test_put_context_key"
        context = SessionScope(current_time=current_time, ask_request=ask_request_resident)
        await memory.put_context(key, context)
        result = await memory.get(key + "_context")
        # Result is a dict (serialized), transient fields should be excluded
        assert isinstance(result, dict)
        assert "ask_request" not in result
        assert "current_time" not in result

    @pytest.mark.asyncio
    async def test_put_context_with_custom_expire(self, ask_request_resident, current_time):
        """Test that put_context accepts custom expiration."""
        key = "test_put_context_expire_key"
        context = SessionScope(current_time=current_time, ask_request=ask_request_resident)
        await memory.put_context(key, context, expire="2h")
        result = await memory.get_context(key)
        # ask_request and current_time are excluded from cache serialization
        assert result.ask_request is None
        assert result.current_time != context.current_time


class TestContextCacheKey:
    """Tests for the context_cache_key helper."""

    def test_sms_and_email_get_distinct_keys_for_same_session(
        self, ask_request_resident_sms_knck, ask_request_resident_email_knck
    ):
        """SMS and EMAIL with the same chat_session_id must produce distinct cache keys."""
        shared_session_id = "shared-stream-id-from-upstream"
        ask_request_resident_sms_knck.chat_session_id = shared_session_id
        ask_request_resident_email_knck.chat_session_id = shared_session_id

        sms_key = memory.context_cache_key(ask_request_resident_sms_knck)
        email_key = memory.context_cache_key(ask_request_resident_email_knck)

        assert sms_key != email_key
        assert sms_key.startswith("sms:")
        assert email_key.startswith("email:")
        assert shared_session_id in sms_key
        assert shared_session_id in email_key

    @pytest.mark.asyncio
    async def test_sms_and_email_contexts_are_isolated_in_cache(
        self, ask_request_resident_sms_knck, ask_request_resident_email_knck, current_time
    ):
        """A SessionScope put under the SMS key must not be returned under the EMAIL key."""
        shared_session_id = "shared-stream-id-isolation-test"
        ask_request_resident_sms_knck.chat_session_id = shared_session_id
        ask_request_resident_email_knck.chat_session_id = shared_session_id

        sms_context = SessionScope(current_time=current_time, ask_request=ask_request_resident_sms_knck)
        sms_context.frustrated_user_emitted = True

        await memory.put_context(memory.context_cache_key(ask_request_resident_sms_knck), sms_context)

        email_result = await memory.get_context(memory.context_cache_key(ask_request_resident_email_knck))
        assert email_result is None

        sms_result = await memory.get_context(memory.context_cache_key(ask_request_resident_sms_knck))
        assert sms_result is not None
        assert sms_result.frustrated_user_emitted is True
