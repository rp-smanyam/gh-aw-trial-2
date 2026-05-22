"""Shared fixtures for resident_one_agent tests."""

import os
from unittest.mock import MagicMock

import jinja2
import pytest


@pytest.fixture
def instructions_template():
    """Load the INSTRUCTIONS.md template."""
    path = os.path.join(
        os.path.dirname(__file__),
        "../../../../src/agent_leasing/agent/resident_one_agent/INSTRUCTIONS.md",
    )
    with open(path) as f:
        return f.read()


@pytest.fixture
def voice_responder_template():
    """Load the VOICE_RESPONDER.md template."""
    path = os.path.join(
        os.path.dirname(__file__),
        "../../../../src/agent_leasing/agent/resident_one_agent/VOICE_RESPONDER.md",
    )
    with open(path) as f:
        return f.read()


@pytest.fixture
def mock_context():
    """Create a mock context for template rendering."""
    context = MagicMock()
    context.welcome_greeting_delivered = False
    context.sms_consent_status = "granted"
    context.sms_needs_consent_prompt = False
    context.ask_request.product_info.source = "KNCK"
    context.ask_request.product_info.knock_resident_id = "123"
    context.ask_request.product_info.ab_resident_id.id = "456"
    context.ask_request.product_info.uc_community_id.id = "789"
    context.is_identity_verified = MagicMock(return_value=False)
    context.is_identity_verified_with_birth_year = MagicMock(return_value=False)
    context.verification_attempts = {}
    return context


@pytest.fixture
def mock_settings():
    """Create mock settings."""
    settings = MagicMock()
    settings.facilities_thinker_api_enabled = True
    settings.welcome_message_sections = []
    settings.max_identity_verification_attempts = 2
    return settings


@pytest.fixture
def mock_mcp_servers():
    """Mock MCP servers for prefetch-gating tests."""
    return {
        "knock_mcp_server": MagicMock(),
        "facilities_mcp_server": MagicMock(),
        "loft_mcp_server": MagicMock(),
    }


@pytest.fixture
def prefetch_mock_context():
    """Session context for prefetch-gating tests (distinct from the template-rendering mock_context)."""
    context = MagicMock()
    context.property_id = 123
    context.disabled_modules = []
    context.previous_response_id = None
    context.has_openai_server_history = False
    context.ask_request.product_info.uc_company_id.id = 456
    context.ask_request.product_info.uc_resident_household_id.id = 789
    context.ask_request.product_info.uc_property_id.id = 123
    context.ask_request.product_info.uc_resident_member_id.id = 101
    context.ask_request.product_info.ab_resident_id.id = 202
    context.ask_request.product_info.uc_community_id.id = 303
    return context


def render_template(template_str, channel, context, settings, **extra_vars):
    """Render a Jinja2 template with common variables.

    Pass additional template variables (e.g. custom_greeting, available_services)
    as keyword arguments.
    """
    env = jinja2.Environment()
    template = env.from_string(template_str)
    return template.render(
        channel=channel,
        context=context,
        disabled_modules=[],
        disabled_tools=[],
        settings=settings,
        current_time="2025-06-25T11:00:00",
        **extra_vars,
    )
