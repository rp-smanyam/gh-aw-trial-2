"""Internal helpers shared by activity extractors."""

from __future__ import annotations


def optional_str(value) -> str | None:
    """None / empty-string in → None out. Otherwise stringify."""
    if value is None:
        return None
    text = str(value)
    return text or None
