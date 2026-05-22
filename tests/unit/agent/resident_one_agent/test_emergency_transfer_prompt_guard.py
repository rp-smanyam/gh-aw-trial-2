"""Prompt regression guards for emergency-transfer wording and tool-call coupling.

These tests validate emergency-specific transfer instructions rendered in both
VOICE_RESPONDER.md and INSTRUCTIONS.md.
"""

from .conftest import render_template


class TestVoiceResponderEmergencyTransferPrompt:
    """VOICE_RESPONDER.md emergency-transfer guardrails."""

    def test_vague_emergency_maintenance_request_does_not_confirm_active_emergency_flow(
        self, voice_responder_template, mock_context, mock_settings
    ):
        """A bare request for "emergency maintenance" should not trigger the active-emergency override."""
        rendered = render_template(voice_responder_template, "VOICE", mock_context, mock_settings)
        assert 'The phrase "emergency maintenance" by itself does NOT confirm an emergency.' in rendered
        assert "ask what the issue is or follow the normal Human Handoff flow instead" in rendered

    def test_emergency_override_requires_confirmed_active_emergency_workflow(
        self, voice_responder_template, mock_context, mock_settings
    ):
        """The override should apply only after an actual emergency has been described."""
        rendered = render_template(voice_responder_template, "VOICE", mock_context, mock_settings)
        assert "This override applies only during a confirmed active emergency workflow" in rendered

    def test_emergency_override_uses_neutral_first_request_wording(
        self, voice_responder_template, mock_context, mock_settings
    ):
        """First-request emergency override wording should use the approved neutral phrase."""
        rendered = render_template(voice_responder_template, "VOICE", mock_context, mock_settings)
        assert (
            "I understand you want to speak with someone. I'll get you over to a staff member to help as quickly as possible."
            in rendered
        )
        assert (
            "I'm connecting you with our emergency maintenance team right now who are best equipped to help with this."
            not in rendered
        )

    def test_transfer_announcements_must_be_coupled_to_same_turn_tool_call(
        self, voice_responder_template, mock_context, mock_settings
    ):
        """Immediate transfer wording should be explicitly tied to same-turn tool execution."""
        rendered = render_template(voice_responder_template, "VOICE", mock_context, mock_settings)
        assert "Announcement/tool coupling is mandatory." in rendered
        assert "Never announce a transfer and wait until a later turn to fire the tool." in rendered


class TestThinkerEmergencyTransferPrompt:
    """INSTRUCTIONS.md emergency-transfer guardrails."""

    def test_vague_emergency_maintenance_request_does_not_confirm_override(
        self, instructions_template, mock_context, mock_settings
    ):
        """Thinker prompt should also require issue details before the emergency override applies."""
        rendered = render_template(instructions_template, "VOICE", mock_context, mock_settings)
        assert "This override applies only during a confirmed active emergency workflow" in rendered
        assert 'only asked for "emergency maintenance," that is not a confirmed emergency workflow' in rendered

    def test_instructions_use_neutral_first_request_emergency_wording(
        self, instructions_template, mock_context, mock_settings
    ):
        """Thinker prompt should mirror the neutral first-request emergency wording."""
        rendered = render_template(instructions_template, "VOICE", mock_context, mock_settings)
        assert (
            "I understand you want to speak with someone. I'll get you over to a staff member to help as quickly as possible."
            in rendered
        )
        assert (
            "I'm connecting you with our emergency maintenance team right now who are best equipped to help with this."
            not in rendered
        )
