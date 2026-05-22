"""Regression guards for KNCK-39652.

When a voice caller's only utterance is a transfer-target word (a staff role or
title — e.g., "courtesy officer", "manager", "operator"), the responder must
pass `summary=None` to `transfer_to_staff_voice` so the tool's `[Action Required]`
flow asks the caller for the actual reason. Both prompts state this rule; if
either one drops or weakens the guard, the regression returns.
"""

import os

from .conftest import render_template


def _load_transfer_voice_description() -> str:
    path = os.path.join(
        os.path.dirname(__file__),
        "../../../../src/agent_leasing/agent/tools/transfer_to_staff/TRANSFER_TO_STAFF_VOICE_DESCRIPTION.md",
    )
    with open(path) as f:
        return f.read()


class TestVoiceResponderTransferTargetGuard:
    """VOICE_RESPONDER.md must instruct the responder to pass summary=None when
    the caller's only stated intent is a transfer-target word."""

    def test_role_or_title_alone_marked_who_not_why(self, voice_responder_template, mock_context, mock_settings):
        rendered = render_template(voice_responder_template, "VOICE", mock_context, mock_settings)
        lowered = rendered.lower()
        assert "who the caller wants" in lowered
        assert "not why" in lowered

    def test_transfer_target_word_clause_passes_none(self, voice_responder_template, mock_context, mock_settings):
        rendered = render_template(voice_responder_template, "VOICE", mock_context, mock_settings)
        lowered = rendered.lower()
        assert "transfer-target word" in lowered, "guard about a bare transfer-target word is missing"
        assert "summary=none" in lowered.replace(" ", ""), "rule must direct the responder to pass summary=None"


class TestTransferToStaffVoiceDescriptionGuard:
    """TRANSFER_TO_STAFF_VOICE_DESCRIPTION.md must list a paraphrased staff
    role/title as a BAD summary alongside the existing examples."""

    def test_role_or_title_paraphrase_listed_as_bad(self):
        description = _load_transfer_voice_description()
        lowered = description.lower()
        assert "paraphrase of a staff role or title" in lowered
        assert "who the caller wants" in lowered
        assert "not why" in lowered
