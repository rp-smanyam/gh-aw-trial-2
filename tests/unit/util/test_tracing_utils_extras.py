import json
from unittest.mock import MagicMock

from agent_leasing.util.tracing_utils import (
    MAX_SPAN_DATA_TOTAL_OBJECT_BYTES,
    MAX_SPAN_DATA_VALUE_BYTES,
    _cap_span_data_value,
    _json_value_size_bytes,
    build_validation_failure_marker_inputs,
    extract_langsmith_trace_id,
    get_langsmith_project_id,
    normalize_metadata_keys,
    parse_missing_fields,
    process_nonstreaming_outputs,
    set_span_data,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _DummySpanData:
    def __init__(self):
        self.data: dict = {}


class _DummySpan:
    def __init__(self):
        self.span_data = _DummySpanData()


# ---------------------------------------------------------------------------
# normalize_metadata_keys
# ---------------------------------------------------------------------------


class TestNormalizeMetadataKeys:
    def test_replaces_dashes_with_underscores(self):
        metadata = {"content-type": "json", "x-request-id": "abc"}
        result = normalize_metadata_keys(metadata)
        assert result == {"content_type": "json", "x_request_id": "abc"}

    def test_no_dashes_unchanged(self):
        metadata = {"already_fine": 1, "ok": True}
        result = normalize_metadata_keys(metadata)
        assert result == metadata

    def test_empty_dict(self):
        assert normalize_metadata_keys({}) == {}

    def test_values_preserved(self):
        metadata = {"some-key": [1, 2, 3]}
        result = normalize_metadata_keys(metadata)
        assert result["some_key"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# extract_langsmith_trace_id
# ---------------------------------------------------------------------------


class TestExtractLangsmithTraceId:
    def test_extracts_trace_id_from_url(self):
        url = "https://smith.langchain.com/o/org-id/projects/p/proj-id/r/trace-id-123/some-suffix"
        result = extract_langsmith_trace_id(url)
        assert result == "trace-id-123"

    def test_extracts_trace_id_no_suffix(self):
        url = "https://smith.langchain.com/o/org-id/projects/p/proj-id/r/trace-id-456"
        result = extract_langsmith_trace_id(url)
        assert result == "trace-id-456"


# ---------------------------------------------------------------------------
# get_langsmith_project_id
# ---------------------------------------------------------------------------


class TestGetLangsmithProjectId:
    def test_returns_none_when_tracing_not_set(self, monkeypatch):
        monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
        assert get_langsmith_project_id("some-project") is None

    def test_returns_none_when_tracing_false(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_TRACING", "false")
        assert get_langsmith_project_id("some-project") is None

    def test_returns_none_when_tracing_empty(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_TRACING", "")
        assert get_langsmith_project_id("some-project") is None


# ---------------------------------------------------------------------------
# _cap_span_data_value
# ---------------------------------------------------------------------------


class TestCapSpanDataValue:
    """Tests for _cap_span_data_value edge cases."""

    def test_none_passes_through(self):
        assert _cap_span_data_value(None, 1024) is None

    def test_bool_passes_through(self):
        assert _cap_span_data_value(True, 1024) is True
        assert _cap_span_data_value(False, 1024) is False

    def test_int_passes_through(self):
        assert _cap_span_data_value(42, 1024) == 42

    def test_float_passes_through(self):
        assert _cap_span_data_value(3.14, 1024) == 3.14

    def test_bytes_get_repr_and_truncated(self):
        data = b"x" * 2000
        result = _cap_span_data_value(data, 100)
        assert isinstance(result, str)
        assert len(result.encode("utf-8")) <= 100
        # repr of bytes starts with b'
        assert result.startswith("b'")

    def test_string_truncated_to_max_bytes(self):
        long_str = "a" * 10000
        result = _cap_span_data_value(long_str, 500)
        assert len(result.encode("utf-8")) <= 500

    def test_string_within_limit_unchanged(self):
        short_str = "hello"
        assert _cap_span_data_value(short_str, 500) == "hello"

    def test_cycle_detection_returns_cycle(self):
        # Build a self-referencing dict
        d: dict = {"a": {}}
        d["a"]["self"] = d  # type: ignore[index]
        result = _cap_span_data_value(d, MAX_SPAN_DATA_VALUE_BYTES)
        # The inner reference should produce "<cycle>"
        dumped = json.dumps(result, default=str)
        assert "<cycle>" in dumped

    def test_max_depth_returns_max_depth(self):
        # Build a deeply nested dict (depth > 8)
        inner: dict = {"leaf": "value"}
        for _ in range(10):
            inner = {"nested": inner}
        result = _cap_span_data_value(inner, MAX_SPAN_DATA_VALUE_BYTES)
        dumped = json.dumps(result, default=str)
        assert "<max depth>" in dumped

    def test_list_truncation_when_exceeding_max_bytes(self):
        # A list of large strings should get truncated
        big_list = ["x" * 500 for _ in range(50)]
        result = _cap_span_data_value(big_list, 1024)
        assert isinstance(result, list)
        assert len(result) < len(big_list)
        size = _json_value_size_bytes(result)
        assert size <= 1024

    def test_dict_truncation_when_exceeding_max_bytes(self):
        big_dict = {f"key_{i}": "v" * 500 for i in range(50)}
        result = _cap_span_data_value(big_dict, 1024)
        assert isinstance(result, dict)
        assert len(result) < len(big_dict)
        size = _json_value_size_bytes(result)
        assert size <= 1024

    def test_unknown_type_stringified(self):
        """Non-JSON-native types (e.g., set) are stringified and truncated."""

        class Custom:
            def __repr__(self):
                return "CustomRepr"

        result = _cap_span_data_value(Custom(), 1024)
        assert isinstance(result, str)
        assert "CustomRepr" in result


# ---------------------------------------------------------------------------
# _json_value_size_bytes
# ---------------------------------------------------------------------------


class TestJsonValueSizeBytes:
    def test_string_size(self):
        # JSON encoding of "hello" is '"hello"' = 7 bytes
        assert _json_value_size_bytes("hello") == 7

    def test_none_size(self):
        # JSON encoding of None is 'null' = 4 bytes
        assert _json_value_size_bytes(None) == 4

    def test_dict_size(self):
        d = {"a": 1}
        expected = len(json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        assert _json_value_size_bytes(d) == expected

    def test_non_serializable_falls_back_to_repr(self):
        """When json.dumps raises, fallback to repr size."""
        val = object()
        result = _json_value_size_bytes(val)
        # default=str should handle it, but either way we get a positive int
        assert result > 0


# ---------------------------------------------------------------------------
# set_span_data — hard cap exceeded, keys dropped from end
# ---------------------------------------------------------------------------


class TestSetSpanDataHardCap:
    def test_hard_cap_drops_keys_from_end(self):
        """When soft cap is exceeded quickly and many None-keyed entries push past
        the hard cap, keys should be dropped from the end."""
        span = _DummySpan()

        # Create enough keys that even storing them all as None would exceed hard cap.
        # Each entry like "key_NNN":null is ~14 bytes + comma.
        # Hard cap is 9728 bytes. With 700 keys we would need ~10 KB.
        updates = {f"key_{i:03d}": f"value_{i}" for i in range(700)}

        set_span_data(span, **updates)

        payload = span.span_data.data
        total_bytes = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))
        assert total_bytes <= MAX_SPAN_DATA_TOTAL_OBJECT_BYTES

        # Some keys from the end should have been dropped
        assert len(payload) < 700

    def test_hard_cap_while_loop_trims_entries(self):
        """Trigger the while-loop at line 220-232 that drops entries after initial build."""
        span = _DummySpan()

        # Strategy: use one big value that just fits under soft cap, then enough
        # None entries to push total object past hard cap (9728 bytes).
        # The big value uses ~5KB of the soft cap (9216 bytes).
        # Each None entry ("extra_NNN":null) is ~18 bytes + comma.
        # We need enough entries so that total exceeds 9728 bytes.
        big_value = "x" * (MAX_SPAN_DATA_VALUE_BYTES - 64)
        updates = {"big": big_value}
        # Add 500 small keys — enough to push well past the hard cap
        for i in range(500):
            updates[f"extra_{i:03d}"] = f"data_{i}"

        set_span_data(span, **updates)

        payload = span.span_data.data
        total_bytes = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))
        assert total_bytes <= MAX_SPAN_DATA_TOTAL_OBJECT_BYTES

        # The big value should still be present (it fit under soft cap)
        assert "big" in payload
        # Not all extra keys should have survived
        extra_keys = [k for k in payload if k.startswith("extra_")]
        assert len(extra_keys) < 500

    def test_span_none_uses_current_span(self, monkeypatch):
        """When span=None, set_span_data calls get_current_span()."""
        dummy_span = _DummySpan()
        monkeypatch.setattr(
            "agent_leasing.util.tracing_utils.get_current_span",
            lambda: dummy_span,
        )
        set_span_data(span=None, hello="world")
        assert dummy_span.span_data.data.get("hello") == "world"

    def test_span_none_no_current_span_is_noop(self, monkeypatch):
        """When span=None and get_current_span() returns None, nothing blows up."""
        monkeypatch.setattr(
            "agent_leasing.util.tracing_utils.get_current_span",
            lambda: None,
        )
        # Should not raise
        set_span_data(span=None, hello="world")


# ---------------------------------------------------------------------------
# process_nonstreaming_outputs
# ---------------------------------------------------------------------------


class TestProcessNonstreamingOutputs:
    def _make_response(self, body: bytes):
        mock = MagicMock()
        mock.body = body
        return mock

    def _make_body(self, response_text: str) -> bytes:
        inner = json.dumps({"response": response_text, "languageCode": "en"})
        return json.dumps({"content": {"chat": inner}}).encode()

    def test_extracts_response_message(self):
        response = self._make_response(self._make_body("Hello, how can I help?"))
        assert process_nonstreaming_outputs(response) == {"message": "Hello, how can I help?"}

    def test_returns_dict_not_string(self):
        response = self._make_response(self._make_body("Some reply"))
        result = process_nonstreaming_outputs(response)
        assert isinstance(result, dict)
        assert "message" in result

    def test_returns_empty_on_invalid_outer_json(self):
        response = self._make_response(b"not valid json")
        assert process_nonstreaming_outputs(response) == {"message": ""}

    def test_returns_empty_when_content_key_missing(self):
        body = json.dumps({"other": "data"}).encode()
        assert process_nonstreaming_outputs(self._make_response(body)) == {"message": ""}

    def test_returns_empty_when_chat_key_missing(self):
        body = json.dumps({"content": {"other": "data"}}).encode()
        assert process_nonstreaming_outputs(self._make_response(body)) == {"message": ""}

    def test_returns_empty_when_chat_is_invalid_json(self):
        body = json.dumps({"content": {"chat": "not valid json"}}).encode()
        assert process_nonstreaming_outputs(self._make_response(body)) == {"message": ""}

    def test_returns_empty_when_inner_response_key_missing(self):
        inner = json.dumps({"languageCode": "en"})  # no "response" key
        body = json.dumps({"content": {"chat": inner}}).encode()
        assert process_nonstreaming_outputs(self._make_response(body)) == {"message": ""}

    def test_returns_empty_when_body_is_none(self):
        # json.loads(None) raises TypeError
        assert process_nonstreaming_outputs(self._make_response(None)) == {"message": ""}

    def test_returns_empty_when_content_is_null(self):
        # HANDOFF_TO_HUMAN_FLOW responses have content: null
        body = json.dumps({"content": None, "flow_name": "HANDOFF_TO_HUMAN_FLOW"}).encode()
        assert process_nonstreaming_outputs(self._make_response(body)) == {"message": ""}


class TestParseMissingFields:
    def test_parses_typical_pydantic_message(self):
        msg = (
            "1 validation error for AskRequest\n"
            "  Value error, Missing required fields for resident persona: "
            "product_info.uc_company_id, product_info.uc_property_id [type=value_error]"
        )
        assert parse_missing_fields(msg) == ["product_info.uc_company_id", "product_info.uc_property_id"]

    def test_no_match_returns_empty(self):
        assert parse_missing_fields("some other validation error") == []

    def test_handles_single_field(self):
        msg = "Missing required fields: product_info.uc_company_id [type=value_error]"
        assert parse_missing_fields(msg) == ["product_info.uc_company_id"]


class TestBuildValidationFailureMarkerInputs:
    def test_extracts_signals_from_payload(self):
        error_str = (
            "Missing required fields: product_info.uc_company_id, product_info.ab_resident_id [type=value_error]"
        )
        payload = {
            "product": "resident_one_voice",
            "call_sid": "CAtest",
            "product_info": {"call_sid": "CAtest", "caller": "+15551234567", "account_sid": "AC123"},
        }
        out = build_validation_failure_marker_inputs(error_str, "missing_required_fields", payload, "v1")
        assert out["validation_reason"] == "missing_required_fields"
        assert out["missing_fields"] == ["product_info.uc_company_id", "product_info.ab_resident_id"]
        assert out["call_sid"] == "CAtest"
        assert out["caller"] == "+15551234567"
        assert out["account_sid"] == "AC123"
        assert out["product"] == "resident_one_voice"
        assert "caller" in out["product_info_keys"]
        assert out["voice_handler_variant"] == "v1"

    def test_handles_empty_payload(self):
        out = build_validation_failure_marker_inputs("other error", "other", {}, "v2")
        assert out["validation_reason"] == "other"
        assert out["missing_fields"] == []
        assert out["call_sid"] is None
        assert out["product_info_keys"] == []
        assert out["voice_handler_variant"] == "v2"
