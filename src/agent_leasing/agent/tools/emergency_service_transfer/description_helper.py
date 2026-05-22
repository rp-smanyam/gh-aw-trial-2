"""Helper utilities for rendering tool descriptions with Jinja2 templates."""

import jinja2
import structlog

from agent_leasing.agent.util import SessionScope, get_channel_from_context

logger = structlog.get_logger(__name__)


def render_tool_description(template: str, context: SessionScope | None) -> str:
    """Render a tool description template with request context data."""
    if not context:
        return template

    try:
        environment = jinja2.Environment()
        channel = get_channel_from_context(context)
        return environment.from_string(template).render(context=context, channel=channel)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Failed to render tool description; using template", error=str(exc))
        return template
