"""Tests for the manage_custom_reminders workflow in INSTRUCTIONS.md.

PTP and plain reminder intents flow through a single merged section, but the
stored `reminder_context` still has two intent-driven variants:

- PTP:     `PTP: User committed to paying $<amount> on <YYYY-MM-DD>. Channel: <channel>`
- REMINDER: `REMINDER: User set reminder for <YYYY-MM-DD>. Channel: <channel>`

Key invariants the prompt must encode:

- One record per date, regardless of intent — `PTP:` and `REMINDER:` share
  the same slot for a given date.
- Always call `get_custom_reminders` before writing so the agent knows
  whether to insert or update.
- Date changes use `action="update"` with the new `new_reminder_date` field
  (no more delete-then-insert).
- The 7-day-forward / future-only date window rule still applies.
"""

from tests.unit.agent.resident_one_agent.conftest import render_template

REMINDER_SECTION_START = "**Custom Reminders (including Promise to Pay)**"
REMINDER_SECTION_END = "- **Balance**:"


class TestReminderWorkflowUnifiedSection:
    """The PTP and plain-reminder sub-flows are merged into a single section."""

    def test_unified_section_exists(self, instructions_template, mock_context, mock_settings):
        rendered = render_template(instructions_template, "CHAT", mock_context, mock_settings)
        assert REMINDER_SECTION_START in rendered, (
            "Prompt must have a single combined 'Custom Reminders (including Promise to Pay)' section"
        )

    def test_no_separate_ptp_section(self, instructions_template, mock_context, mock_settings):
        rendered = render_template(instructions_template, "CHAT", mock_context, mock_settings)
        assert "- **Promise to Pay (PTP)**:" not in rendered, (
            "The standalone 'Promise to Pay (PTP)' section must be removed — PTP is handled "
            "inside the unified Custom Reminders section."
        )
        assert "- **Custom Reminder**:" not in rendered, (
            "The standalone 'Custom Reminder' section must be removed — it is folded into the "
            "unified Custom Reminders section."
        )

    def test_intent_detection_still_describes_both_flavors(self, instructions_template, mock_context, mock_settings):
        """The merged section must still teach the agent which kinds of user
        phrasings signal PTP vs plain reminder intent — those cues drive
        which `reminder_context` variant the agent writes."""
        rendered = render_template(instructions_template, "CHAT", mock_context, mock_settings)
        section = _extract_section(rendered, start=REMINDER_SECTION_START, end=REMINDER_SECTION_END)
        assert section, "Unified reminder section not found"

        assert "Promise to Pay (PTP)" in section, "PTP intent flavor must still be documented"
        assert "Plain reminder" in section or "plain reminder" in section, (
            "Plain reminder intent flavor must still be documented"
        )
        assert "I'll pay $500" in section, "PTP example phrasings must be preserved"
        assert "remind me" in section.lower(), "Plain reminder example phrasings must be preserved"


class TestReminderContextSchema:
    """Two reminder_context variants — one per intent — share the same record slot."""

    def test_ptp_context_format_documented(self, instructions_template, mock_context, mock_settings):
        rendered = render_template(instructions_template, "CHAT", mock_context, mock_settings)
        section = _extract_section(rendered, start=REMINDER_SECTION_START, end=REMINDER_SECTION_END)
        assert "PTP: User committed to paying $<amount> on <YYYY-MM-DD>" in section, (
            "PTP reminder_context variant must be documented in the prompt"
        )

    def test_plain_reminder_context_format_documented(self, instructions_template, mock_context, mock_settings):
        rendered = render_template(instructions_template, "CHAT", mock_context, mock_settings)
        section = _extract_section(rendered, start=REMINDER_SECTION_START, end=REMINDER_SECTION_END)
        assert "REMINDER: User set reminder for <YYYY-MM-DD>" in section, (
            "Plain reminder reminder_context variant must be documented in the prompt"
        )


class TestOneRecordPerDate:
    """A single record per date is enforced regardless of intent."""

    def test_one_record_per_date_documented(self, instructions_template, mock_context, mock_settings):
        rendered = render_template(instructions_template, "CHAT", mock_context, mock_settings)
        section = _extract_section(rendered, start=REMINDER_SECTION_START, end=REMINDER_SECTION_END)
        assert "One record per date" in section, "The one-record-per-date invariant must be called out in the prompt"

    def test_one_record_per_date_applies_across_both_variants(
        self, instructions_template, mock_context, mock_settings
    ):
        rendered = render_template(instructions_template, "CHAT", mock_context, mock_settings)
        section = _extract_section(rendered, start=REMINDER_SECTION_START, end=REMINDER_SECTION_END)
        rule_block = _extract_section(section, start="One record per date", end="Date window rule")
        assert rule_block, "One-record-per-date rule block not found"
        # The rule must make it explicit that PTP and REMINDER records share the
        # same slot — a same-date conflict blocks regardless of intent type.
        assert "PTP:" in rule_block and "REMINDER:" in rule_block, (
            "One-record-per-date rule must mention both PTP and REMINDER so the agent knows "
            "either kind of existing record blocks a new write on the same date"
        )


class TestGetBeforeWrite:
    """The agent must always call get_custom_reminders before writing."""

    def test_workflow_starts_with_get_custom_reminders(self, instructions_template, mock_context, mock_settings):
        rendered = render_template(instructions_template, "CHAT", mock_context, mock_settings)
        section = _extract_section(rendered, start=REMINDER_SECTION_START, end=REMINDER_SECTION_END)
        workflow = _extract_section(section, start="**Workflow**", end="If `manage_custom_reminders`")
        assert workflow, "Workflow block not found in unified reminder section"

        assert "get_custom_reminders" in workflow, "Workflow must call get_custom_reminders before any write"
        # The get call must come before any insert call in the rendered prompt.
        get_idx = workflow.find("get_custom_reminders")
        insert_idx = workflow.find('action="insert"')
        assert insert_idx == -1 or get_idx < insert_idx, "get_custom_reminders must be called before action='insert'"

    def test_existing_record_branch_offers_update(self, instructions_template, mock_context, mock_settings):
        rendered = render_template(instructions_template, "CHAT", mock_context, mock_settings)
        section = _extract_section(rendered, start=REMINDER_SECTION_START, end=REMINDER_SECTION_END)
        # When a record already exists for the requested date, the agent must surface the
        # conflict and ask whether to update — it must not blindly try insert again.
        assert "already exists for that date" in section.lower(), (
            "Prompt must instruct the agent to surface the same-date conflict to the resident"
        )
        assert "whether they want to update" in section.lower() or ("ask whether to update" in section.lower()), (
            "Prompt must instruct the agent to ask the resident whether to update"
        )


class TestDateChangeUsesUpdate:
    """Date changes now go through action='update' with new_reminder_date."""

    def test_tool_signature_includes_new_reminder_date(self, instructions_template, mock_context, mock_settings):
        rendered = render_template(instructions_template, "CHAT", mock_context, mock_settings)
        assert "new_reminder_date=" in rendered, (
            "manage_custom_reminders tool signature must document the new_reminder_date parameter"
        )

    def test_update_uses_new_reminder_date_for_date_change(self, instructions_template, mock_context, mock_settings):
        rendered = render_template(instructions_template, "CHAT", mock_context, mock_settings)
        section = _extract_section(rendered, start=REMINDER_SECTION_START, end=REMINDER_SECTION_END)
        update_block = _extract_section(section, start='Update (`action="update"`)', end='Delete (`action="delete"`)')
        assert update_block, "Update sub-flow not found"
        assert 'action="update"' in update_block, "Update sub-flow must call action='update'"
        assert "new_reminder_date" in update_block, (
            "Update sub-flow must pass new_reminder_date so the date can change without delete+insert"
        )

    def test_no_delete_then_insert_for_date_change(self, instructions_template, mock_context, mock_settings):
        """The old delete-then-insert pattern for date changes is gone."""
        rendered = render_template(instructions_template, "CHAT", mock_context, mock_settings)
        section = _extract_section(rendered, start=REMINDER_SECTION_START, end=REMINDER_SECTION_END)
        update_block = _extract_section(section, start='Update (`action="update"`)', end='Delete (`action="delete"`)')
        assert update_block, "Update sub-flow not found"
        assert 'action="delete"' not in update_block, (
            "Date changes must not call action='delete' — they use action='update' with new_reminder_date"
        )


class TestSameDateUpdateStillUsesUpdate:
    """When only the amount changes (date unchanged) we still use action='update'."""

    def test_update_block_handles_unchanged_date(self, instructions_template, mock_context, mock_settings):
        rendered = render_template(instructions_template, "CHAT", mock_context, mock_settings)
        section = _extract_section(rendered, start=REMINDER_SECTION_START, end=REMINDER_SECTION_END)
        update_block = _extract_section(section, start='Update (`action="update"`)', end='Delete (`action="delete"`)')
        assert update_block, "Update sub-flow not found"
        # The block must allow new_reminder_date="" so callers can update a record in place
        # without changing the date (e.g. resident only changes the PTP amount).
        assert '""' in update_block, 'Update sub-flow must document new_reminder_date="" for same-date amount updates'


class TestDateWindowRulePreserved:
    """The 7-day-forward date window rule still applies in the merged section."""

    def test_date_window_rule_in_unified_section(self, instructions_template, mock_context, mock_settings):
        rendered = render_template(instructions_template, "CHAT", mock_context, mock_settings)
        section = _extract_section(rendered, start=REMINDER_SECTION_START, end=REMINDER_SECTION_END)
        assert "Date window rule" in section, "Date window rule must remain in the unified section"
        assert "7 days" in section, "Date window rule must still document the 7-day limit"
        assert "strictly in the future" in section, (
            "Date window rule must still require the date to be strictly in the future"
        )


def _extract_section(text: str, *, start: str, end: str) -> str:
    """Return the substring between the first occurrence of ``start`` and the next ``end``."""
    if not text:
        return ""
    start_idx = text.find(start)
    if start_idx == -1:
        return ""
    after_start = text[start_idx:]
    end_idx = after_start.find(end, len(start))
    if end_idx == -1:
        return after_start
    return after_start[:end_idx]
