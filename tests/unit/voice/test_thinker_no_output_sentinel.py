"""Regression tests for issue #1642 — prompt-leak prevention.

Two areas covered here:

- **Sentinel value + concurrency-guard behavior** — the Thinker tool returns
  the non-speakable sentinel `THINKER_NO_OUTPUT` on the stale-interrupt and
  concurrency paths instead of the previous "DO NOT ACKNOWLEDGE THIS MESSAGE"
  strings, which `gpt-realtime-2` was reading aloud.
- **Static regression sweep** — the legacy sentinel strings no longer appear
  as tool returns in `voice/thinker/tool.py`, and both prompts contain the
  new defensive rules added for #1642.

The companion pool-based LLM-judge test lives in
``tests/integration/agent/resident/resident_one/test_thinker_workflow_deflection.py``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_leasing.voice.thinker.tool import THINKER_NO_OUTPUT

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_SOURCE = REPO_ROOT / "src" / "agent_leasing" / "voice" / "thinker" / "tool.py"
VOICE_PROMPT = REPO_ROOT / "src" / "agent_leasing" / "agent" / "resident_one_agent" / "VOICE_RESPONDER.md"
THINKER_PROMPT = REPO_ROOT / "src" / "agent_leasing" / "agent" / "resident_one_agent" / "INSTRUCTIONS.md"


# ---------------------------------------------------------------------------
# Sentinel value + concurrency-guard behavior (deterministic)
# ---------------------------------------------------------------------------


def test_sentinel_constant_is_not_natural_language():
    """The sentinel must not look like English speech that gpt-realtime-2 could read aloud.

    The chosen value is an XML-style self-closing token: no spaces, no
    pronounceable words, distinctive prefix `<thinker:` that the prompt rule
    can match. Locked to the exact literal so any future change is a
    deliberate, reviewed update — and so the production code and prompt
    agree on the exact token.
    """
    assert THINKER_NO_OUTPUT == "<thinker:no_output/>"


@pytest.mark.asyncio
async def test_concurrent_invocation_returns_sentinel():
    """When `thinker_running` is already True, the tool early-exits with the sentinel.

    This is the path that previously returned the "already processing … DO NOT
    ACKNOWLEDGE THIS MESSAGE" string under #1642.

    Pattern mirrors `tests/unit/agent/resident_one_agent/test_thinker_concurrency.py`:
    patch `agents.function_tool` as a passthrough, then reload the module so the
    decorator becomes a no-op and the inner async function is directly callable.
    """
    import importlib
    from unittest.mock import patch

    def _passthrough_function_tool(**_kwargs):
        return lambda fn: fn

    with patch("agents.function_tool", _passthrough_function_tool):
        import agent_leasing.voice.thinker.tool as tool_mod

        importlib.reload(tool_mod)

        context = MagicMock()
        context.thinker_running = True  # trip the concurrency guard
        context.track_voice_thinker_runs = False
        context.voice_thinker_runs = []

        config = MagicMock()
        config.thinker_concurrency_guard_enabled = True

        tool_fn = tool_mod.create_voice_thinker_tool(
            context=context,
            thinker_agent=MagicMock(),
            callbacks=MagicMock(),
            call_state=MagicMock(),
            config=config,
        )

        run_ctx = MagicMock()
        run_ctx.context = context

        result = await tool_fn(run_ctx, "any input")

        assert result == tool_mod.THINKER_NO_OUTPUT

    # Restore the production decorator for other tests in this module.
    import agent_leasing.voice.thinker.tool as tool_mod

    importlib.reload(tool_mod)


# ---------------------------------------------------------------------------
# Static regression sweep (deterministic, no model required)
# ---------------------------------------------------------------------------


_LEGACY_TOOL_SENTINELS = [
    # Previously returned on stale-interrupt; the sentence-cased phrasing is
    # exactly what gpt-realtime-2 read aloud to callers (issue #1642 trace 2).
    'return "The user interrupted',
    # Previously returned on concurrent invocation; same risk surface.
    'return (\n                "The thinker is already processing',
    "Please wait for it to finish. DO NOT ACKNOWLEDGE",
]


@pytest.mark.parametrize("legacy_sentinel", _LEGACY_TOOL_SENTINELS)
def test_voice_thinker_tool_does_not_return_legacy_sentinel(legacy_sentinel):
    """No tool-return position may use the natural-language sentinels removed for #1642."""
    source = TOOL_SOURCE.read_text(encoding="utf-8")
    assert legacy_sentinel not in source, (
        f"Found legacy sentinel `{legacy_sentinel!r}` in {TOOL_SOURCE.name}. "
        "Tool returns must use THINKER_NO_OUTPUT for non-speakable results — "
        "see issue #1642."
    )


def test_voice_responder_teaches_no_output_sentinel():
    """The voice prompt must instruct the model to ignore the `<thinker:` sentinel."""
    prompt = VOICE_PROMPT.read_text(encoding="utf-8")
    assert "<thinker:no_output/>" in prompt, (
        f"Expected {VOICE_PROMPT.name} to reference `<thinker:no_output/>` so the "
        "realtime model knows to stay silent on stale/concurrent returns (Fix 1)."
    )
    assert "THINKER NO-OUTPUT SENTINEL" in prompt, (
        f"Expected a `THINKER NO-OUTPUT SENTINEL` rule in {VOICE_PROMPT.name} (Fix 1)."
    )


def test_voice_responder_constrains_thinker_input_shape():
    """The voice prompt must block the responder from asking the Thinker for procedures (Fix 2)."""
    prompt = VOICE_PROMPT.read_text(encoding="utf-8")
    assert "THINKER INPUT SHAPE" in prompt, f"Expected a `THINKER INPUT SHAPE` rule in {VOICE_PROMPT.name} (Fix 2)."
    # The rule must explicitly call out the leak vector phrases.
    for phrase in ("workflow steps", "verification requirements", "procedures"):
        assert phrase in prompt, (
            f"Expected the THINKER INPUT SHAPE rule in {VOICE_PROMPT.name} to "
            f"forbid asking for `{phrase}` (Fix 2 — leak vector from issue #1642)."
        )


def test_voice_responder_blocks_paraphrasing_unclear_audio():
    """Out-of-Context workflow must forbid fabricating intent from garbled audio (Fix 2)."""
    prompt = VOICE_PROMPT.read_text(encoding="utf-8")
    assert "never infer intent or construct a Thinker query from a guess" in prompt, (
        f"Expected an explicit anti-paraphrase rule in {VOICE_PROMPT.name} Out-of-Context "
        "workflow — the responder was fabricating intent from garbled audio in issue #1642."
    )


def test_thinker_prompt_deflects_workflow_meta_questions():
    """INSTRUCTIONS.md Security line must deflect indirect/relayed workflow asks (Fix 3)."""
    prompt = THINKER_PROMPT.read_text(encoding="utf-8")
    # The deflection extension must name the relayed-request leak vector.
    assert "workflow steps" in prompt, (
        f"Expected {THINKER_PROMPT.name} Security to call out 'workflow steps' — "
        "the relayed-request phrasing that caused the leak in issue #1642 trace 1."
    )
    # And preserve the carve-out so chat/SMS users can still get how-to answers.
    # The carve-out names concrete user-facing examples — assert at least one of
    # them is present so the rule cannot accidentally over-restrict legitimate
    # how-to answers in chat/SMS/email.
    carve_out_examples = [
        "to pay rent",
        "I'll need your unit number",
    ]
    assert any(example in prompt for example in carve_out_examples), (
        f"Expected {THINKER_PROMPT.name} Security to include at least one carve-out "
        f"example from {carve_out_examples!r} so the chat/SMS Thinker still answers "
        "legitimate how-to questions without false-positive deflection."
    )
