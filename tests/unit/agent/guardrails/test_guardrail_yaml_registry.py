"""Registry coherence between guardrail agents and guardrails_reponses.yaml.

Every ``guardrail_name`` string passed by a guardrail agent to
``localize_guardrail_response`` must have a matching top-level key in
``guardrails_reponses.yaml`` with at least an ``en`` entry. Otherwise the
non-English path silently falls through to a live ``translate_text()`` LLM
call instead of returning the static, human-reviewed response.

See issue #1640 Task 5.
"""

from __future__ import annotations

import pytest
import yaml

from agent_leasing.util.language_utils import GUARDRAILS_RESPONSES_PATH

# Canonical list of guardrail_name strings used by the 8 production guardrail
# agents. Update this list whenever a new guardrail agent is added (and add a
# corresponding YAML block in guardrails_reponses.yaml at the same time).
GUARDRAIL_NAMES: tuple[str, ...] = (
    "competitor_blocking_guardrail",
    "fair_housing_guardrail",
    "legal_advice_guardrail",
    "pii_guardrail",
    "prisma_airs_guardrail",
    "prompt_injection_guardrail",
    "security_guardrail",
    "unauthorized_promises_guardrail",
)


@pytest.fixture(scope="module")
def yaml_registry() -> dict[str, dict[str, str]]:
    with open(GUARDRAILS_RESPONSES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@pytest.mark.parametrize("guardrail_name", GUARDRAIL_NAMES)
def test_every_guardrail_name_has_yaml_entry(yaml_registry, guardrail_name):
    assert guardrail_name in yaml_registry, (
        f"guardrail_name {guardrail_name!r} is passed in code but missing from "
        f"guardrails_reponses.yaml. Add a top-level block with at least an 'en' "
        f"key so non-English speakers get the static reviewed response."
    )


@pytest.mark.parametrize("guardrail_name", GUARDRAIL_NAMES)
def test_every_guardrail_entry_has_english_default(yaml_registry, guardrail_name):
    entry = yaml_registry.get(guardrail_name) or {}
    assert "en" in entry and entry["en"], (
        f"YAML block for {guardrail_name!r} must include a non-empty 'en' entry — "
        f"the default fallback when an unsupported language_code is encountered."
    )


def test_canonical_list_matches_guardrail_name_strings_in_code():
    # Guard against drift: scan all guardrail agent source files for
    # `guardrail_name="..."` literals and make sure GUARDRAIL_NAMES is the
    # full set. If a new guardrail agent ships without a matching YAML entry,
    # the parametrized tests above flag it as well — this one catches the
    # other direction (a YAML key with no live caller).
    import re
    from pathlib import Path

    guardrails_dir = Path(__file__).resolve().parents[4] / "src" / "agent_leasing" / "agent" / "guardrails"
    pattern = re.compile(r'guardrail_name\s*=\s*"([a-z_]+_guardrail)"')

    discovered: set[str] = set()
    for py_file in guardrails_dir.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        discovered.update(pattern.findall(py_file.read_text(encoding="utf-8")))

    assert discovered == set(GUARDRAIL_NAMES), (
        f"Drift between code and canonical list:\n"
        f"  In code but not in GUARDRAIL_NAMES: {discovered - set(GUARDRAIL_NAMES)}\n"
        f"  In GUARDRAIL_NAMES but not in code: {set(GUARDRAIL_NAMES) - discovered}"
    )
