"""Tests for office-hours-aware voice transfer feature."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import jinja2
import pytest

from agent_leasing.api.model import OfficeHour
from agent_leasing.util.helpers import is_office_currently_open

from .conftest import render_template

# ── Helpers ────────────────────────────────────────────────────────────


def _make_office_hours(**day_overrides: OfficeHour) -> dict[str, OfficeHour]:
    """Build office_hours dict. Defaults to Mon-Fri 9-5 active, Sat-Sun inactive."""
    defaults = {str(d): OfficeHour(start_time="09:00:00", end_time="17:00:00", is_active=d <= 5) for d in range(1, 8)}
    defaults.update(day_overrides)
    return defaults


def _make_aware_dt(year: int, month: int, day: int, hour: int, minute: int, tz: str) -> datetime:
    """Create a timezone-aware datetime."""
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz))


# ── is_office_currently_open() unit tests ──────────────────────────────


class TestIsOfficeCurrentlyOpen:
    """Tests for the is_office_currently_open helper function."""

    def test_returns_true_during_business_hours(self):
        """Office is open during scheduled hours."""
        # Monday 11:00 CST
        now = _make_aware_dt(2026, 3, 30, 11, 0, "America/Chicago")
        result = is_office_currently_open(_make_office_hours(), "America/Chicago", now)
        assert result is True

    def test_returns_false_after_hours(self):
        """Office is closed after end_time."""
        # Monday 20:00 CST
        now = _make_aware_dt(2026, 3, 30, 20, 0, "America/Chicago")
        result = is_office_currently_open(_make_office_hours(), "America/Chicago", now)
        assert result is False

    def test_returns_false_before_hours(self):
        """Office is closed before start_time."""
        # Monday 07:00 CST
        now = _make_aware_dt(2026, 3, 30, 7, 0, "America/Chicago")
        result = is_office_currently_open(_make_office_hours(), "America/Chicago", now)
        assert result is False

    def test_returns_false_when_day_inactive(self):
        """Office is closed when is_active=False even during hours."""
        office_hours = _make_office_hours()
        office_hours["5"] = OfficeHour(start_time="08:00:00", end_time="18:00:00", is_active=False)
        # Friday 10:00 CST
        now = _make_aware_dt(2026, 4, 3, 10, 0, "America/Chicago")
        result = is_office_currently_open(office_hours, "America/Chicago", now)
        assert result is False

    def test_returns_none_when_office_hours_missing(self):
        """Returns None when office_hours is None."""
        now = _make_aware_dt(2026, 3, 30, 11, 0, "America/Chicago")
        result = is_office_currently_open(None, "America/Chicago", now)
        assert result is None

    def test_returns_none_when_timezone_missing(self):
        """Returns None when property_timezone is None."""
        now = _make_aware_dt(2026, 3, 30, 11, 0, "America/Chicago")
        result = is_office_currently_open(_make_office_hours(), None, now)
        assert result is None

    def test_returns_none_when_day_not_in_schedule(self):
        """Returns None when current day has no entry in schedule."""
        office_hours = {
            str(d): OfficeHour(start_time="09:00:00", end_time="17:00:00", is_active=True) for d in range(1, 6)
        }
        # Saturday (day 6) — not in the dict
        now = _make_aware_dt(2026, 4, 4, 10, 0, "America/Chicago")
        result = is_office_currently_open(office_hours, "America/Chicago", now)
        assert result is None

    def test_returns_none_when_invalid_timezone(self):
        """Returns None for an unrecognized timezone string."""
        now = _make_aware_dt(2026, 3, 30, 11, 0, "America/Chicago")
        result = is_office_currently_open(_make_office_hours(), "Invalid/Timezone", now)
        assert result is None

    def test_returns_none_when_start_time_missing(self):
        """Returns None when start_time is None."""
        office_hours = _make_office_hours()
        office_hours["1"] = OfficeHour(start_time=None, end_time="17:00:00", is_active=True)
        now = _make_aware_dt(2026, 3, 30, 11, 0, "America/Chicago")
        result = is_office_currently_open(office_hours, "America/Chicago", now)
        assert result is None

    def test_boundary_at_start_time(self):
        """Start time is inclusive — exactly at open returns True."""
        now = _make_aware_dt(2026, 3, 30, 9, 0, "America/Chicago")
        result = is_office_currently_open(_make_office_hours(), "America/Chicago", now)
        assert result is True

    def test_boundary_at_end_time(self):
        """End time is exclusive — exactly at close returns False."""
        now = _make_aware_dt(2026, 3, 30, 17, 0, "America/Chicago")
        result = is_office_currently_open(_make_office_hours(), "America/Chicago", now)
        assert result is False

    def test_timezone_conversion_applied(self):
        """UTC time is correctly converted to property timezone."""
        # UTC 14:00 = EST 09:00 (winter, UTC-5)
        now = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)
        result = is_office_currently_open(_make_office_hours(), "America/New_York", now)
        assert result is True

    def test_naive_datetime_treated_as_property_local(self):
        """Naive datetime (no tzinfo) is treated as property-local time, matching SessionScope.current_time."""
        # Monday 11:00, no tzinfo — should be treated as 11:00 in America/Chicago
        now = datetime(2026, 3, 30, 11, 0)
        result = is_office_currently_open(_make_office_hours(), "America/Chicago", now)
        assert result is True
        # Monday 20:00, no tzinfo — outside hours
        now = datetime(2026, 3, 30, 20, 0)
        result = is_office_currently_open(_make_office_hours(), "America/Chicago", now)
        assert result is False

    def test_uses_real_payload_format(self):
        """Works with OfficeHour objects matching actual payload structure."""
        office_hours = {
            "1": OfficeHour(start_time="09:00:00", end_time="17:00:00", is_active=True),
            "2": OfficeHour(start_time="08:00:00", end_time="17:00:00", is_active=True),
            "5": OfficeHour(start_time="08:00:00", end_time="18:45:00", is_active=False),
            "6": OfficeHour(start_time="09:00:00", end_time="16:00:00", is_active=False),
            "7": OfficeHour(start_time="09:00:00", end_time="17:00:00", is_active=False),
        }
        # Tuesday 10:00 CST — active
        now = _make_aware_dt(2026, 3, 31, 10, 0, "America/Chicago")
        assert is_office_currently_open(office_hours, "America/Chicago", now) is True
        # Friday 10:00 CST — inactive
        now = _make_aware_dt(2026, 4, 3, 10, 0, "America/Chicago")
        assert is_office_currently_open(office_hours, "America/Chicago", now) is False


# ── VOICE_RESPONDER.md template rendering tests ────────────────────────


class TestOfficeHoursVoiceResponderTemplate:
    """Tests for office-closed block rendering in VOICE_RESPONDER.md."""

    def test_office_closed_renders_block(self, voice_responder_template, mock_context, mock_settings):
        """When is_office_open=False, the office-closed handoff block is rendered."""
        rendered = render_template(
            voice_responder_template, "VOICE", mock_context, mock_settings, is_office_open=False
        )
        assert "OFFICE CLOSED — TWICE-TO-TRANSFER" in rendered

    def test_office_open_hides_warning(self, voice_responder_template, mock_context, mock_settings):
        """When is_office_open=True, closed warning is hidden and open guidance is rendered."""
        rendered = render_template(voice_responder_template, "VOICE", mock_context, mock_settings, is_office_open=True)
        assert "office is currently closed" not in rendered
        assert "OFFICE OPEN — NORMAL TRANSFER" in rendered

    def test_none_hides_warning(self, voice_responder_template, mock_context, mock_settings):
        """When is_office_open=None (data missing), no explicit open/closed claim appears."""
        rendered = render_template(voice_responder_template, "VOICE", mock_context, mock_settings, is_office_open=None)
        assert "office is currently closed" not in rendered
        assert "OFFICE OPEN — NORMAL TRANSFER" not in rendered
        assert 'If office-hours status is unknown, do NOT say "office is open" or "office is closed."' in rendered

    def test_undefined_hides_warning(self, voice_responder_template, mock_context, mock_settings):
        """When is_office_open is not passed at all, the block is NOT rendered."""
        rendered = render_template(voice_responder_template, "VOICE", mock_context, mock_settings)
        assert "office is currently closed" not in rendered

    def test_handoff_section_intact_when_closed(self, voice_responder_template, mock_context, mock_settings):
        """Office-closed block adds guidance without replacing existing handoff rules."""
        rendered = render_template(
            voice_responder_template, "VOICE", mock_context, mock_settings, is_office_open=False
        )
        assert "transfer_to_staff_voice" in rendered
        assert "Core Rules" in rendered
        assert "CRITICAL EXCEPTION" in rendered

    def test_skip_warning_for_frustrated_and_callbacks(self, voice_responder_template, mock_context, mock_settings):
        """Office-closed block instructs to skip warning for frustrated callers and callbacks."""
        rendered = render_template(
            voice_responder_template, "VOICE", mock_context, mock_settings, is_office_open=False
        )
        assert "frustrated" in rendered.lower()
        assert "callback" in rendered.lower() or "return-call" in rendered.lower()

    def test_office_closed_block_delegates_warning_to_tool(
        self, voice_responder_template, mock_context, mock_settings
    ):
        """Regression guard for KNCK-39167: closed-hours warning is tool-driven to avoid duplicate playback."""
        rendered = render_template(
            voice_responder_template, "VOICE", mock_context, mock_settings, is_office_open=False
        )
        assert "Tool-first on first request" in rendered, "closed-hours first step must route through tool"
        assert "Do NOT independently deliver the closed-hours warning text" in rendered
        assert "When tool asks for closed-hours warning" in rendered
        assert "one-time per call" in rendered.lower(), "must explicitly preserve one-time warning behavior"


# ── INSTRUCTIONS.md (Thinker) office-hours QnA tests ────────────────────


class TestThinkerOfficeHoursQnA:
    """Tests for STAFF_AND_HOURS.OFFICE_HOURS QnA in Thinker instructions."""

    @staticmethod
    def _render_instructions_for_office_status(instructions_template, mock_context, mock_settings, is_office_open):
        """Render INSTRUCTIONS.md for a specific office-hours status."""
        return render_template(
            instructions_template,
            "CHAT",
            mock_context,
            mock_settings,
            is_office_open=is_office_open,
        )

    def test_instructions_include_office_hours_guidance(self, instructions_template, mock_context, mock_settings):
        """INSTRUCTIONS.md includes guidance for handling STAFF_AND_HOURS.OFFICE_HOURS questions."""
        rendered_when_open = self._render_instructions_for_office_status(
            instructions_template, mock_context, mock_settings, True
        )
        rendered_when_closed = self._render_instructions_for_office_status(
            instructions_template, mock_context, mock_settings, False
        )
        rendered_when_unknown = self._render_instructions_for_office_status(
            instructions_template, mock_context, mock_settings, None
        )

        # Check for the Staff & Hours section
        assert "Staff & Hours" in rendered_when_open
        assert "STAFF_AND_HOURS.OFFICE_HOURS" in rendered_when_open
        # Check that is_office_open value is rendered (as True/False/None)
        assert "true" in rendered_when_open.lower() and "false" in rendered_when_closed.lower()
        assert rendered_when_open != rendered_when_closed
        assert rendered_when_open != rendered_when_unknown
        assert rendered_when_closed != rendered_when_unknown

    def test_instructions_mention_real_time_office_status(self, instructions_template, mock_context, mock_settings):
        """Instructions explain that is_office_open is computed real-time."""
        rendered = self._render_instructions_for_office_status(
            instructions_template, mock_context, mock_settings, True
        )
        assert "decision-time" in rendered.lower() or "real-time" in rendered.lower() or "current" in rendered.lower()

    def test_office_closed_guidance_in_instructions(self, instructions_template, mock_context, mock_settings):
        """Instructions include guidance for when office is closed."""
        rendered = self._render_instructions_for_office_status(
            instructions_template, mock_context, mock_settings, False
        )
        # Should provide guidance on how to respond when closed
        assert "closed" in rendered.lower()

    def test_office_open_guidance_in_instructions(self, instructions_template, mock_context, mock_settings):
        """Instructions include guidance for when office is open."""
        rendered = self._render_instructions_for_office_status(
            instructions_template, mock_context, mock_settings, True
        )
        # Should provide guidance on how to respond when open
        assert "open" in rendered.lower()

    def test_unknown_office_hours_guidance_in_instructions(self, instructions_template, mock_context, mock_settings):
        """Instructions include guidance for when office-hours status is unknown."""
        rendered = self._render_instructions_for_office_status(
            instructions_template, mock_context, mock_settings, None
        )
        # Unknown status should follow open behavior and avoid "we don't know" language.
        assert "treat as open behavior" in rendered.lower()
        assert "do not mention missing/unknown hours" in rendered.lower()

    def test_instructions_render_office_status_value(self, instructions_template, mock_context, mock_settings):
        """Instructions must render the computed is_office_open value in prompt text."""
        rendered_open = render_template(
            instructions_template, "CHAT", mock_context, mock_settings, is_office_open=True
        )
        rendered_closed = render_template(
            instructions_template, "CHAT", mock_context, mock_settings, is_office_open=False
        )
        rendered_unknown = render_template(
            instructions_template, "CHAT", mock_context, mock_settings, is_office_open=None
        )

        assert "`True`" in rendered_open or "True" in rendered_open
        assert "`False`" in rendered_closed or "False" in rendered_closed
        assert "`None`" in rendered_unknown or "None" in rendered_unknown

    def test_instructions_require_is_office_open_template_variable(
        self, instructions_template, mock_context, mock_settings
    ):
        """Rendering with StrictUndefined should fail if is_office_open is not provided."""
        env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        template = env.from_string(instructions_template)

        with pytest.raises(jinja2.exceptions.UndefinedError):
            template.render(
                channel="CHAT",
                context=mock_context,
                disabled_modules=[],
                disabled_tools=[],
                settings=mock_settings,
                current_time="2025-06-25T11:00:00",
            )
