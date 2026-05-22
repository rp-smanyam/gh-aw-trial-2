"""Tracing utilities: cap OpenAI span_data to ~10KB (5KB/value, 9KB soft total, 9.5KB hard) to avoid trace payload rejection/drops."""

import asyncio
import datetime
import json
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import langsmith as ls
import structlog
from agents import Span
from agents.tracing import get_current_span
from fastapi.responses import JSONResponse
from langsmith.run_helpers import tracing_context

MAX_SPAN_DATA_VALUE_BYTES = 5 * 1024  # 5 KB per key/value (UTF-8 bytes)
MAX_SPAN_DATA_TOTAL_VALUES_BYTES = 9 * 1024  # 9 KB soft cap across values
MAX_SPAN_DATA_TOTAL_OBJECT_BYTES = 9728  # 9.5 KB hard cap for whole JSON object
MAX_LANGSMITH_PROMPT_BYTES = 1_000_000  # 1 MB safety cap per prompt logged to LangSmith

logger = structlog.getLogger()


def record_initial_greeting_latency(
    root_run: ls.RunTree | None,
    startup_span: "DeferredSpanTree",
) -> int | None:
    """Stamp user-perceived initial-greeting latency on the voice root run.

    Measures from ``start_event_received`` (Twilio MediaStream start) to
    ``first_utterance_sent`` (first greeting audio frame handed to transport),
    both pre-recorded on ``startup_span``. The value is written as
    ``metadata.initial_greeting_latency_ms`` (integer ms) on the LangSmith run
    for downstream voice-responsiveness eval consumption.

    Returns the ms value when the write succeeds, ``None`` when either anchor
    mark is missing, no root run is available, or the write raises. Callers
    are responsible for one-shot semantics (a guard flag), since the underlying
    mark events fire on every audio frame.
    """
    if root_run is None:
        return None
    elapsed = startup_span.elapsed_ms("start_event_received", "first_utterance_sent")
    if elapsed is None:
        return None
    value = round(elapsed)
    try:
        root_run.add_metadata({"initial_greeting_latency_ms": value})
    except Exception:
        logger.warning("Failed to record initial_greeting_latency_ms", exc_info=True)
        return None
    return value


def post_trace_marker(
    root_run: ls.RunTree | None,
    name: str,
    *,
    inputs: dict[str, Any] | None = None,
    message: str,
) -> None:
    """Post a synthetic LangSmith child run as an observability marker.

    These are not real tool calls — they're zero-duration markers that make
    it easy to identify why a call ended when reviewing traces in LangSmith.
    """
    if not root_run:
        return
    now = datetime.datetime.now(datetime.UTC)
    child = root_run.create_child(
        name=name,
        run_type="chain",
        inputs=inputs or {},
        outputs={"message": message},
        start_time=now,
        end_time=now,
    )
    child.post()


def parse_missing_fields(error_str: str) -> list[str]:
    """Pull the field list out of a 'Missing required fields...: a, b, c [type=...' pydantic error."""
    if "Missing required fields" not in error_str:
        return []
    after = error_str.split("Missing required fields", 1)[1]
    fields_part = after.split("[", 1)[0]
    if ":" in fields_part:
        fields_part = fields_part.split(":", 1)[1]
    return [f.strip() for f in fields_part.split(",") if f.strip()]


def build_validation_failure_marker_inputs(
    error_str: str,
    validation_reason: str,
    payload: dict[str, Any],
    variant: str,
) -> dict[str, Any]:
    """Build inputs for the validation_failure trace marker.

    Captures who/where/what signals so the trace is self-identifying without
    a CloudWatch round-trip — see issue #1567.
    """
    product_info = payload.get("product_info") or {}
    return {
        "validation_reason": validation_reason,
        "missing_fields": parse_missing_fields(error_str),
        "call_sid": payload.get("call_sid") or product_info.get("call_sid"),
        "account_sid": payload.get("account_sid") or product_info.get("account_sid"),
        "caller": product_info.get("caller") or payload.get("caller"),
        "product": payload.get("product"),
        "product_info_keys": sorted(product_info.keys()),
        "voice_handler_variant": variant,
    }


def _truncate_str_by_bytes(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value

    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _json_kv_size_bytes(key: str, value: Any) -> int:
    """Return the UTF-8 byte size of a single JSON object entry: "key":<value>.

    This uses the same JSON encoding settings as span serialization and allows
    incremental size accounting without re-serializing the entire payload.
    """

    key_json = json.dumps(key, ensure_ascii=False, separators=(",", ":"), default=str)
    value_json = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return len((key_json + ":" + value_json).encode("utf-8"))


def _json_value_size_bytes(value: Any) -> int:
    """Return UTF-8 byte size of a compact JSON encoding of `value`."""
    try:
        dumped = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        return len(dumped.encode("utf-8"))
    except Exception:
        return len(repr(value).encode("utf-8"))


def _cap_span_data_value(value: Any, max_value_bytes: int) -> Any:
    """Cap a value so that its compact JSON representation <= max_value_bytes (UTF-8).

    For nested dicts/lists, we truncate *structurally* (i.e., shorten strings,
    drop tail items/keys) so the returned Python object remains JSON-serializable.
    """
    if value is None or isinstance(value, bool | int | float):
        return value

    if isinstance(value, bytes):
        return _truncate_str_by_bytes(repr(value), max_value_bytes)

    if isinstance(value, str):
        return _truncate_str_by_bytes(value, max_value_bytes)

    # For nested objects, recursively cap strings/items/keys rather than
    # truncating a serialized JSON string mid-way (which produces invalid JSON).

    def _cap_inner(current: Any, seen: set[int], depth: int) -> Any:
        if current is None or isinstance(current, bool | int | float):
            return current

        if isinstance(current, bytes):
            return _truncate_str_by_bytes(repr(current), max_value_bytes)

        if isinstance(current, str):
            return _truncate_str_by_bytes(current, max_value_bytes)

        current_id = id(current)
        if current_id in seen:
            return "<cycle>"
        if depth >= 8:
            return "<max depth>"

        if isinstance(current, dict):
            seen.add(current_id)
            result: dict[str, Any] = {}

            for key, item in current.items():
                key_str = _truncate_str_by_bytes(str(key), min(256, max_value_bytes))
                capped_item = _cap_inner(item, seen, depth + 1)
                result[key_str] = capped_item

                if _json_value_size_bytes(result) > max_value_bytes:
                    result.pop(key_str, None)
                    break

            seen.remove(current_id)
            return result

        if isinstance(current, list | tuple):
            seen.add(current_id)
            result_list: list[Any] = []

            for item in current:
                capped_item = _cap_inner(item, seen, depth + 1)
                result_list.append(capped_item)
                if _json_value_size_bytes(result_list) > max_value_bytes:
                    result_list.pop()
                    break

            seen.remove(current_id)
            return result_list

        return _truncate_str_by_bytes(str(current), max_value_bytes)

    capped = _cap_inner(value, seen=set(), depth=0)

    # Final safety: if the result still exceeds max_value_bytes, fall back to
    # a truncated repr string.
    if _json_value_size_bytes(capped) > max_value_bytes:
        return _truncate_str_by_bytes(repr(value), max_value_bytes)
    return capped


def build_openai_trace_url(trace_id: str) -> str | None:
    return f"https://platform.openai.com/logs/trace?trace_id={trace_id}" if trace_id else None


def build_openai_group_url(group_id: str) -> str | None:
    return f"https://platform.openai.com/logs?api=traces&group_id={group_id}" if group_id else None


def is_langsmith_enabled() -> bool:
    """Check whether LangSmith tracing is enabled and an API key is configured."""
    from agent_leasing.settings import settings

    return bool(settings.langsmith_tracing and settings.langsmith_api_key)


def get_langsmith_trace_url(run: ls.RunTree) -> str | None:
    """Return the LangSmith trace URL, or None if tracing is disabled."""
    if not is_langsmith_enabled():
        return None
    try:
        return run.get_url()
    except Exception:
        return None


def get_langsmith_project_id(project_name: str) -> str | None:
    if not is_langsmith_enabled():
        return None
    try:
        client = ls.client.Client()
        project = client.read_project(project_name=project_name) if project_name else None
        project_id = getattr(project, "id", None)
        return project_id
    except Exception:
        return None


def set_span_data(span: Span | None = None, **kwargs: Any) -> None:
    """Safely attach additional structured data onto the current tracing span.

        This helper exists to avoid OpenAI tracing ingestion issues caused by
        oversized ``span_data.data`` payloads.

        Strategy:
        - Cap each individual value to :data:`MAX_SPAN_DATA_VALUE_BYTES`.
        - As we add values, enforce a soft total budget
            (:data:`MAX_SPAN_DATA_TOTAL_VALUES_BYTES`). If adding a value would exceed
            the soft budget, we store ``None`` for that key instead.
        - After building the payload, enforce a hard total object size cap
            (:data:`MAX_SPAN_DATA_TOTAL_OBJECT_BYTES`) by dropping keys from the end.

    Notes:
    - Size is measured on the UTF-8 bytes of a compact JSON encoding.
    - Non-JSON-serializable values are stringified via ``default=str``.
    """
    if span is None:
        span = get_current_span()

    if span is not None:
        MAX_VALUE_SIZE = MAX_SPAN_DATA_VALUE_BYTES
        MAX_TOTAL_VALUES = MAX_SPAN_DATA_TOTAL_VALUES_BYTES
        MAX_TOTAL_OBJECT = MAX_SPAN_DATA_TOTAL_OBJECT_BYTES

        payload: dict[str, Any] = {}
        payload_keys_in_order: list[str] = []
        payload_entry_sizes: list[int] = []

        # Incremental JSON size accounting for the dict.
        # Start with {} -> 2 bytes. Each subsequent entry adds:
        #   - 1 comma if not the first entry
        #   - the bytes of "key":<json(value)>
        payload_size_bytes = 2

        def _prospective_size(entry_size_bytes: int, current_entries: int) -> int:
            comma_bytes = 1 if current_entries > 0 else 0
            return payload_size_bytes + comma_bytes + entry_size_bytes

        def _append_entry(key: str, value: Any, entry_size_bytes: int) -> None:
            nonlocal payload_size_bytes
            if payload_keys_in_order:
                payload_size_bytes += 1  # comma
            payload_size_bytes += entry_size_bytes
            payload[key] = value
            payload_keys_in_order.append(key)
            payload_entry_sizes.append(entry_size_bytes)

        soft_exceeded = False

        for key, value in kwargs.items():
            key_str = str(key)

            # Precompute sizes for the possible stored values.
            # Note: we size based on the actual value we would store in `payload`,
            # which preserves existing behavior.
            none_entry_size = _json_kv_size_bytes(key_str, None)

            if soft_exceeded:
                prospective_none = _prospective_size(none_entry_size, len(payload_keys_in_order))
                if prospective_none > MAX_TOTAL_OBJECT:
                    break
                _append_entry(key_str, None, none_entry_size)
                continue

            capped = _cap_span_data_value(value, MAX_VALUE_SIZE)
            capped_entry_size = _json_kv_size_bytes(key_str, capped)
            prospective_with_value = _prospective_size(capped_entry_size, len(payload_keys_in_order))
            if prospective_with_value <= MAX_TOTAL_VALUES:
                _append_entry(key_str, capped, capped_entry_size)
                continue

            soft_exceeded = True

            prospective_with_none = _prospective_size(none_entry_size, len(payload_keys_in_order))
            if prospective_with_none > MAX_TOTAL_OBJECT:
                break
            _append_entry(key_str, None, none_entry_size)

        if payload_size_bytes > MAX_TOTAL_OBJECT:
            # Drop keys from the end until within hard cap.
            while payload_size_bytes > MAX_TOTAL_OBJECT and payload_keys_in_order:
                last_key = payload_keys_in_order.pop()
                last_entry_size = payload_entry_sizes.pop()
                payload.pop(last_key, None)

                # Remove the entry bytes.
                payload_size_bytes -= last_entry_size

                # Remove the comma that preceded the removed entry, if any.
                # Since we only ever remove from the end, the removed entry had
                # a leading comma iff it wasn't the first entry.
                if payload_keys_in_order:
                    payload_size_bytes -= 1

        span.span_data.data.update(payload)


def _truncate_prompt_for_logging(rendered_prompt: str) -> str:
    """Truncate a rendered prompt to stay within the LangSmith byte limit."""
    if len(rendered_prompt.encode("utf-8")) <= MAX_LANGSMITH_PROMPT_BYTES:
        return rendered_prompt
    marker = "\n\n[truncated]"
    truncated = _truncate_str_by_bytes(rendered_prompt, MAX_LANGSMITH_PROMPT_BYTES - len(marker.encode("utf-8")))
    return truncated + marker


def log_prompt_to_langsmith(
    prompt_name: str,
    rendered_prompt: str,
    context_variables: dict[str, Any],
    parent: dict | None = None,
) -> None:
    """Log a rendered prompt to LangSmith as a ChatPromptTemplate span.

    Creates a child span under the given parent LangSmith trace that
    captures the fully rendered prompt and the context variables used
    for template rendering. This mirrors the ChatPromptTemplate span that
    LangChain-based repos produce automatically.

    Args:
        prompt_name: Name of the prompt template file (e.g. "INSTRUCTIONS.md").
        rendered_prompt: The fully rendered prompt string.
        context_variables: Template variables used during rendering.
        parent: LangSmith tracing headers (from ``RunTree.to_headers()``)
            used to nest this span under the correct parent trace.  Required
            because the instructions callbacks run in a context where the
            LangSmith contextvar is not propagated.
    """
    if not is_langsmith_enabled():
        return
    try:
        prompt_to_log = _truncate_prompt_for_logging(rendered_prompt)

        with ls.trace(
            name="ChatPromptTemplate",
            run_type="prompt",
            inputs={"template_name": prompt_name, "context_variables": context_variables},
            parent=parent,
        ) as run:
            run.end(outputs={"rendered_prompt": prompt_to_log})
        logger.debug("Logged prompt to LangSmith", prompt_name=prompt_name, has_parent=parent is not None)
    except Exception:
        logger.warning("Failed to log prompt to LangSmith", exc_info=True)


def log_prompt_to_langsmith_child(
    parent_run: ls.RunTree,
    prompt_name: str,
    rendered_prompt: str,
    context_variables: dict[str, Any],
) -> None:
    """Log a rendered prompt as a direct child of an existing RunTree.

    Uses the ``create_child()`` + ``post()`` pattern used by voice traces
    (see ``TwilioHandler._post_langsmith_child_run``).  This avoids the
    orphan-run problem that ``ls.trace(parent=headers)`` causes when the
    LangSmith contextvar is not propagated.

    Args:
        parent_run: The LangSmith RunTree to attach the child span to.
        prompt_name: Name of the prompt template file.
        rendered_prompt: The fully rendered prompt string.
        context_variables: Template variables used during rendering.
    """
    if not is_langsmith_enabled():
        return
    try:
        prompt_to_log = _truncate_prompt_for_logging(rendered_prompt)

        child = parent_run.create_child(
            name="ChatPromptTemplate",
            run_type="prompt",
            inputs={"template_name": prompt_name, "context_variables": context_variables},
            outputs={"rendered_prompt": prompt_to_log},
        )
        child.post()
        logger.info(
            "Logged prompt child run to LangSmith",
            prompt_name=prompt_name,
            parent_project=parent_run.session_name,
        )
    except Exception:
        logger.warning("Failed to log prompt child run to LangSmith", exc_info=True)


def normalize_metadata_keys(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of `metadata` with '-' in keys replaced by '_'."""
    return {key.replace("-", "_"): value for key, value in metadata.items()}


def extract_langsmith_trace_id(url: str) -> str:
    path = urlparse(url).path
    return path.split("/r/", 1)[1].split("/", 1)[0]


def log_ai_message_span(
    user_message: str,
    ai_message: str,
    rendered_system_prompt: str | None = None,
) -> None:
    """Create an AIMessage LangSmith span capturing the user input and AI output."""
    with ls.trace(name="AIMessage", run_type="llm") as agent_trace:
        ai_inputs: dict[str, str] = {"message": user_message}
        if rendered_system_prompt:
            ai_inputs["system_prompt"] = rendered_system_prompt
        agent_trace.add_inputs(ai_inputs)
        agent_trace.end(outputs={"message": ai_message})


def annotate_handoff_bypass(run: ls.RunTree | None, prompt: str) -> str | None:
    """Annotate a LangSmith trace for the active handoff bypass path.

    Returns the LangSmith trace URL (or None if tracing is disabled).
    """
    if is_langsmith_enabled() and run:
        run.add_metadata({"active_handoff_bypass": True, "flow_name": "HANDOFF_TO_HUMAN_FLOW"})
    with ls.trace(name="HumanMessage", run_type="llm") as ht:
        ht.end(outputs={"message": prompt})
    with ls.trace(name="AIMessage", run_type="llm") as at:
        at.end(outputs={"message": "Active handoff bypass — no response returned to the user"})
    return get_langsmith_trace_url(run)


def process_nonstreaming_outputs(response: JSONResponse) -> dict:
    """
    Callback function for LangSmith `process_outputs` for non-streaming responses.
    """
    try:
        body = json.loads(response.body)
        content = body.get("content")
        if content is None:
            return {"message": ""}
        return {"message": json.loads(content["chat"])["response"]}
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error("Failed to extract message from response body", error=str(e), body=response.body)
        return {"message": ""}


@dataclass
class DeferredSpanTree:
    """Record timestamps now, build a LangSmith span tree later.

    Useful when a logical span crosses async task boundaries where context
    managers can't reach.  Call ``mark()`` at phase boundaries, ``attach()``
    to set the LangSmith parent for live nesting, and ``finalize()`` at cleanup
    to post the tree.
    """

    name: str
    phases: list[tuple[str, str, str]]  # (span_name, start_mark, end_mark)
    _marks: dict[str, tuple[float, float]] = field(default_factory=dict)  # name → (monotonic, wall)
    _run: ls.RunTree | None = field(default=None, repr=False)
    _finalized: bool = False

    def mark(self, name: str) -> None:
        """Record a timestamp. First write wins."""
        if name not in self._marks:
            self._marks[name] = (time.monotonic(), time.time())

    def has_mark(self, name: str) -> bool:
        return name in self._marks

    def attach(self, parent: ls.RunTree) -> AbstractContextManager:
        """Create the span under *parent* and return a context that parents child spans under it."""
        self._run = parent.create_child(name=self.name, run_type="chain")
        return tracing_context(parent=self._run)

    def elapsed_ms(self, start: str, end: str) -> float | None:
        s, e = self._marks.get(start), self._marks.get(end)
        return (e[0] - s[0]) * 1000 if s and e else None

    async def finalize(self, parent: ls.RunTree | None) -> None:
        """Set times, create phase children, and post. Idempotent, swallows errors."""
        if self._finalized or not parent:
            return
        self._finalized = True

        first_key, last_key = self.phases[0][1], self.phases[-1][2]
        start = self._marks.get(first_key)
        if not start:
            return

        end = self._marks.get(last_key)
        _dt = lambda wall: datetime.datetime.fromtimestamp(wall, tz=datetime.UTC)  # noqa: E731

        run = self._run or parent.create_child(name=self.name, run_type="chain")
        run.start_time = _dt(start[1])
        run.end_time = _dt(end[1] if end else start[1])
        run.outputs = {"total_ms": self.elapsed_ms(first_key, last_key)}

        children = []
        for span_name, sk, ek in self.phases:
            s, e = self._marks.get(sk), self._marks.get(ek)
            if not s or not e:
                continue
            children.append(
                run.create_child(
                    name=span_name,
                    run_type="chain",
                    start_time=_dt(s[1]),
                    end_time=_dt(e[1]),
                    outputs={"duration_ms": round((e[0] - s[0]) * 1000, 2)},
                )
            )

        try:

            def _post() -> None:
                run.post()
                for c in children:
                    c.post()

            await asyncio.wait_for(asyncio.to_thread(_post), timeout=5.0)
        except Exception:
            logger.warning(f"DeferredSpanTree({self.name}): failed to post", exc_info=True)
