from datetime import timezone

from agent_leasing.models.context import SessionScope


def test_session_scope_current_time_defaults_to_aware_utc():
    ctx = SessionScope()

    assert ctx.current_time.tzinfo is not None
    assert ctx.current_time.utcoffset() == timezone.utc.utcoffset(ctx.current_time)
