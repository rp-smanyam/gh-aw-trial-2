from agent_leasing.services.tracing_service import (
    LangsmithResponseWrapper,
    OutputMessage,
)


class TestOutputMessage:
    """Test OutputMessage class."""

    def test_init_and_model_dump(self):
        """Test OutputMessage initialization and model_dump."""
        data = {"key": "value", "number": 123}
        msg = OutputMessage(data)
        assert msg.data == data
        assert msg.model_dump() == data


class TestLangsmithResponseWrapper:
    """Test LangsmithResponseWrapper class."""

    def test_init(self):
        """Test initialization."""
        wrapper = LangsmithResponseWrapper("test output")
        assert wrapper.instructions == ""
        assert wrapper._output_text == "test output"
        assert wrapper._response_id.startswith("override-response-")

    def test_id_property(self):
        """Test id property."""
        wrapper = LangsmithResponseWrapper("test")
        assert wrapper.id == wrapper._response_id

    def test_model_dump_with_exclude_none(self):
        """Test model_dump with exclude_none=True."""
        wrapper = LangsmithResponseWrapper("test output")
        result = wrapper.model_dump(exclude_none=True)

        assert result["id"] == wrapper._response_id
        assert result["object"] == "response"
        assert "created_at" in result
        assert len(result["output"]) == 1
        assert result["output"][0]["type"] == "message"
        assert result["output"][0]["role"] == "assistant"
        assert result["output"][0]["content"][0]["text"] == "test output"
        assert result["parallel_tool_calls"] is False

    def test_model_dump_without_exclude_none(self):
        """Test model_dump with exclude_none=False."""
        wrapper = LangsmithResponseWrapper("test output")
        result = wrapper.model_dump(exclude_none=False)

        assert result["id"] == wrapper._response_id
        assert result["tool_choice"] is None
        assert result["tools"] == []

    def test_output_property(self):
        """Test output property."""
        wrapper = LangsmithResponseWrapper("test output")
        output = wrapper.output

        assert len(output) == 1
        assert isinstance(output[0], OutputMessage)
        output_data = output[0].model_dump()
        assert output_data["type"] == "message"
        assert output_data["role"] == "assistant"
        assert output_data["content"][0]["text"] == "test output"
