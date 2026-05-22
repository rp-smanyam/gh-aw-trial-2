from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from agents import TResponseInputItem


def extract_text_from_input(
    value: str | Sequence[TResponseInputItem] | object,
) -> str:
    """Extract text from an input value (used by input guardrails).

    When the input is a list (e.g. history + current message), only the last
    user message is extracted so that guardrails evaluate the newest turn
    rather than re-moderating the entire conversation history.

    If the input is a list and contains no user messages (for example, an
    empty list), an empty string is returned.
    """
    last_user_message = _get_last_user_message(value)
    return _extract_text_from_object(last_user_message)


def extract_text_from_output(
    value: str | Sequence[TResponseInputItem] | object,
) -> str:
    """Extract text from an output value (used by output guardrails)."""
    return _extract_text_from_object(value)


def _get_last_user_message(
    text_value: str | list[TResponseInputItem] | object,
) -> str | list[TResponseInputItem] | object:
    """Return the last user message item from the input.

    If value is a list, iterates in reverse so that when history is prepended
    to the current input, only the most recent user utterance is returned.
    For non-list inputs (str, dict, objects, etc.), returns the value as-is.
    If no user message is found in the list, returns an empty string so
    guardrails skip moderation entirely.
    """
    if not isinstance(text_value, list):
        return text_value
    for item in reversed(text_value):
        is_user = (hasattr(item, "get") and item.get("role") == "user") or (
            hasattr(item, "role") and getattr(item, "role") == "user"
        )
        if is_user:
            return item
    return ""


def _extract_text_from_object(
    object: str | Sequence[TResponseInputItem] | object,
) -> str:
    """
    Helper functions to extract text from an output value from an Output Guardrail

    Defensive programming to handle unexpected input types.
    Sub-helper functions are used to handle the extraction based on the type of input.
    """
    if isinstance(object, str):
        return object

    if isinstance(object, Sequence) and not isinstance(object, str | bytes):
        text = _maybe_text_from_sequence(object)
        if text or text == "":
            return text

    if isinstance(object, dict):
        text = _maybe_text_from_mapping(object)
        if text or text == "":
            return text

    text = _maybe_text_from_object(object)
    if text or text == "":
        return text

    return str(object)


def _maybe_text_from_sequence(sequence: Sequence[TResponseInputItem]) -> str | None:
    """Extracts and joins text from a sequence of items."""
    parts = list(_iter_sequence_items(sequence))
    return " ".join(parts)


def _maybe_text_from_mapping(item: dict[str, Any]) -> str | None:
    """Extracts text from a mapping."""
    for key in ("text", "content", "response"):
        value = item.get(key)
        if isinstance(value, str):
            return value
    return None


def _maybe_text_from_object(item: Any) -> str | None:
    """Extracts text from a response object"""
    for attr in ("response", "content", "text"):
        value = getattr(item, attr, None)
        if isinstance(value, str):
            return value

    suggested = getattr(item, "suggested_response", None)
    detailed = getattr(item, "detailed_information", None)
    parts: list[str] = []
    if isinstance(suggested, str):
        parts.append(suggested)
    if isinstance(detailed, str):
        parts.append(detailed)
    if parts:
        return " ".join(parts)

    return None


def _coerce_text_from_item(item: Any) -> str | None:
    if item is None:
        return None
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return _maybe_text_from_mapping(item)
    return _maybe_text_from_object(item)


def _iter_sequence_items(sequence: Sequence[TResponseInputItem]) -> Iterable[str]:
    for item in sequence:
        text = _coerce_text_from_item(item)
        if text:
            yield text
