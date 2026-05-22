"""Sanity tests for the Q&A taxonomy module.

These guard against accidental drift between `QnATopic` (the typing
`Literal` source of truth) and `QNA_TOPICS` (the runtime tuple), and
against malformed entries.
"""

import re
from typing import get_args

from agent_leasing.agent.qna_taxonomy import QNA_TOPICS, QnATopic


def test_runtime_tuple_matches_literal_args():
    assert QNA_TOPICS == get_args(QnATopic)


def test_no_duplicates():
    assert len(QNA_TOPICS) == len(set(QNA_TOPICS))


def test_every_value_is_uppercase_and_follows_format():
    # Either bare uppercase token (top-level OTHER) or CATEGORY.SUBTOPIC.
    pattern = re.compile(r"^[A-Z][A-Z_]*(?:\.[A-Z][A-Z_0-9]*)?$")
    bad = [t for t in QNA_TOPICS if not pattern.match(t)]
    assert bad == [], f"Malformed taxonomy entries: {bad}"


def test_top_level_other_present():
    assert "OTHER" in QNA_TOPICS


def test_every_category_has_an_other_fallback():
    # Every multi-subtopic category must offer `<CATEGORY>.OTHER` so the
    # responder always has somewhere to land for unrecognized phrasing.
    categories: dict[str, list[str]] = {}
    for value in QNA_TOPICS:
        if "." not in value:
            continue
        category, subtopic = value.split(".", 1)
        categories.setdefault(category, []).append(subtopic)

    missing = [cat for cat, subs in categories.items() if "OTHER" not in subs]
    assert missing == [], f"Categories missing an OTHER fallback: {missing}"
