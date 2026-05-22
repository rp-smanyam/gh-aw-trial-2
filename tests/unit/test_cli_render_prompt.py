"""Unit tests for cli_render_prompt.py functionality."""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_leasing.api.model import AskRequest
from agent_leasing.cli_render_prompt import (
    PROMPT_REGISTRY,
    build_session_scope,
    fetch_live_data,
    list_prompts,
    load_context_overrides,
    load_payload,
    render_prompt,
)

# -- Fixtures ----------------------------------------------------------------


def _minimal_payload(product: str = "resident_one_chat") -> dict:
    """Return a minimal valid AskRequest payload dict with all required resident fields."""
    return {
        "product": product,
        "prompt": "",
        "prompt_version": 0,
        "product_info": {
            "knock_property_id": "12345",
            "knock_prospect_id": "67890",
            "knock_resident_id": "137",
            "source": "LL",
            "uc_company_id": {"id": "1", "source": "OS"},
            "uc_property_id": {"id": "1", "source": "OS"},
            "uc_resident_household_id": {"id": "1", "source": "OS"},
            "uc_resident_member_id": {"id": "137", "source": "OS"},
            "ab_resident_id": {"id": "1", "source": "AB"},
            "uc_lease_id": {"id": "1", "source": "OS"},
            "uc_portal_base_url": "https://example.com",
        },
    }


def _make_ask_request(product: str = "resident_one_chat") -> AskRequest:
    return AskRequest(**_minimal_payload(product))


# -- load_payload tests ------------------------------------------------------


def test_load_payload_success():
    """Test loading a valid JSON file."""
    test_data = _minimal_payload()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(test_data, f)
        temp_path = f.name
    try:
        result = load_payload(temp_path)
        assert result == test_data
    finally:
        Path(temp_path).unlink()


def test_load_payload_file_not_found(capsys: pytest.CaptureFixture[str]):
    """Test that FileNotFoundError is handled correctly."""
    with pytest.raises(SystemExit):
        load_payload("nonexistent_file.json")
    captured = capsys.readouterr()
    assert "nonexistent_file.json" in captured.err


def test_load_payload_invalid_json(capsys: pytest.CaptureFixture[str]):
    """Test that invalid JSON is handled correctly."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{ invalid json content")
        temp_path = f.name
    try:
        with pytest.raises(SystemExit):
            load_payload(temp_path)
    finally:
        Path(temp_path).unlink()
    captured = capsys.readouterr()
    assert "Invalid JSON" in captured.err


# -- load_context_overrides tests -------------------------------------------


def test_load_context_overrides_success():
    """Test loading valid context overrides."""
    overrides = {"disabled_modules": ["MR"], "property_data": "test data"}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(overrides, f)
        temp_path = f.name
    try:
        result = load_context_overrides(temp_path)
        assert result == overrides
    finally:
        Path(temp_path).unlink()


def test_load_context_overrides_file_not_found(capsys: pytest.CaptureFixture[str]):
    """Test that missing context file is handled."""
    with pytest.raises(SystemExit):
        load_context_overrides("nonexistent_context.json")
    captured = capsys.readouterr()
    assert "nonexistent_context.json" in captured.err


# -- build_session_scope tests -----------------------------------------------


def test_build_session_scope_defaults():
    """Verify safe defaults for runtime-populated fields."""
    ask_request = _make_ask_request()
    scope = build_session_scope(ask_request)

    assert scope.ask_request is ask_request
    assert scope.property_data == ""
    assert scope.disabled_modules == []
    assert scope.disabled_tools == []
    assert scope.packages is None
    assert scope.service_requests is None
    assert scope.signed_up_community_events is None
    assert scope.identity_verified == {}
    assert scope.identity_verified_with_birth_year == {}
    assert scope.sms_consent_status is None


def test_build_session_scope_with_overrides():
    """Verify overrides are applied on top of defaults."""
    ask_request = _make_ask_request()
    overrides = {
        "property_data": "Some property overview text",
        "disabled_modules": ["MR", "PKG"],
        "identity_verified": {"CHAT": True},
    }
    scope = build_session_scope(ask_request, overrides=overrides)

    assert scope.property_data == "Some property overview text"
    assert scope.disabled_modules == ["MR", "PKG"]
    assert scope.identity_verified == {"CHAT": True}
    # Non-overridden defaults still hold
    assert scope.disabled_tools == []
    assert scope.packages is None


def test_build_session_scope_with_current_time():
    """Verify current_time override."""
    ask_request = _make_ask_request()
    fixed_time = datetime(2025, 6, 15, 10, 30, 0)
    scope = build_session_scope(ask_request, current_time=fixed_time)
    assert scope.current_time == fixed_time


# -- render_prompt tests -----------------------------------------------------


def test_render_simple_template():
    """Simple templates use only current_time and context."""
    template_text = "Time: {{ current_time }}, Property: {{ context.property_id }}"
    ask_request = _make_ask_request()
    scope = build_session_scope(ask_request, current_time=datetime(2025, 1, 1, 12, 0, 0))

    rendered = render_prompt(template_text, scope, "CHAT", "simple:prompt")
    assert "2025-01-01T12:00:00" in rendered
    assert "12345" in rendered


def test_render_resident_template_with_channel():
    """Resident templates include channel variable."""
    template_text = "Channel: {{ channel }}"
    ask_request = _make_ask_request()
    scope = build_session_scope(ask_request)

    rendered = render_prompt(template_text, scope, "CHAT", "resident:instructions")
    assert rendered == "Channel: CHAT"

    rendered_voice = render_prompt(template_text, scope, "VOICE", "resident:instructions")
    assert rendered_voice == "Channel: VOICE"


def test_render_resident_instructions_chat():
    """Render actual resident instructions template with chat channel."""
    from agent_leasing.agent.util import AgentWithMCP

    template_text = AgentWithMCP._get_prompt(PROMPT_REGISTRY["resident:instructions"], version=0)
    ask_request = _make_ask_request("resident_one_chat")
    scope = build_session_scope(ask_request, current_time=datetime(2025, 6, 15, 10, 0, 0))

    rendered = render_prompt(template_text, scope, "CHAT", "resident:instructions")

    # Should have no remaining Jinja syntax
    assert "{{" not in rendered
    assert "{%" not in rendered


def test_render_resident_instructions_voice():
    """Render actual resident instructions template with voice channel."""
    from agent_leasing.agent.util import AgentWithMCP

    template_text = AgentWithMCP._get_prompt(PROMPT_REGISTRY["resident:instructions"], version=0)
    ask_request = _make_ask_request("resident_one_voice")
    scope = build_session_scope(ask_request, current_time=datetime(2025, 6, 15, 10, 0, 0))

    rendered = render_prompt(template_text, scope, "VOICE", "resident:instructions")

    assert "{{" not in rendered
    assert "{%" not in rendered


def test_render_with_disabled_modules():
    """Verify disabled_modules affects conditional blocks."""
    template_text = "{% if 'MR' not in disabled_modules %}MR enabled{% else %}MR disabled{% endif %}"
    ask_request = _make_ask_request()

    # All modules enabled
    scope_enabled = build_session_scope(ask_request)
    rendered_enabled = render_prompt(template_text, scope_enabled, "CHAT", "resident:instructions")
    assert rendered_enabled == "MR enabled"

    # MR disabled
    scope_disabled = build_session_scope(ask_request, overrides={"disabled_modules": ["MR"]})
    rendered_disabled = render_prompt(template_text, scope_disabled, "CHAT", "resident:instructions")
    assert rendered_disabled == "MR disabled"


# -- list_prompts tests ------------------------------------------------------


def test_list_prompts(capsys: pytest.CaptureFixture[str]):
    """Verify listing output shows all registered prompts."""
    list_prompts()
    captured = capsys.readouterr()
    for identifier in PROMPT_REGISTRY:
        assert identifier in captured.out


# -- JSON output test --------------------------------------------------------


def test_json_output(capsys: pytest.CaptureFixture[str]):
    """Verify --json output produces valid JSON with expected structure."""
    payload = _minimal_payload()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        payload_path = f.name

    try:
        with patch(
            "sys.argv",
            ["cli-render-prompt", "simple:prompt", payload_path, "--json", "--current-time", "2025-01-01T12:00:00"],
        ):
            from agent_leasing.cli_render_prompt import main

            main()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["prompt_id"] == "simple:prompt"
        assert data["channel"] == "CHAT"
        assert data["version"] == 0
        assert "rendered" in data
        assert isinstance(data["rendered"], str)
    finally:
        Path(payload_path).unlink()


# -- Version selection test --------------------------------------------------


def test_version_selection_from_payload():
    """Verify version is read from payload's prompt_version when --version not given."""
    payload = _minimal_payload()
    payload["prompt_version"] = 0

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        payload_path = f.name

    try:
        with patch(
            "sys.argv",
            ["cli-render-prompt", "resident:instructions", payload_path, "--current-time", "2025-01-01T12:00:00"],
        ):
            from agent_leasing.cli_render_prompt import main

            main()
        # If it runs without error, version was resolved correctly
    finally:
        Path(payload_path).unlink()


# -- Channel override test ---------------------------------------------------


def test_channel_override(capsys: pytest.CaptureFixture[str]):
    """Verify --channel overrides product-derived channel."""
    template_text = "Channel: {{ channel }}"
    ask_request = _make_ask_request("resident_one_chat")  # Would derive CHAT
    scope = build_session_scope(ask_request)

    # Override to VOICE
    rendered = render_prompt(template_text, scope, "VOICE", "resident:instructions")
    assert rendered == "Channel: VOICE"


def test_channel_override_via_cli(capsys: pytest.CaptureFixture[str]):
    """Verify --channel flag works end-to-end."""
    payload = _minimal_payload("resident_one_chat")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        payload_path = f.name

    try:
        with patch(
            "sys.argv",
            [
                "cli-render-prompt",
                "simple:prompt",
                payload_path,
                "--channel",
                "VOICE",
                "--json",
                "--current-time",
                "2025-01-01T12:00:00",
            ],
        ):
            from agent_leasing.cli_render_prompt import main

            main()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["channel"] == "VOICE"
    finally:
        Path(payload_path).unlink()


# -- live_overrides tests ----------------------------------------------------


def test_build_session_scope_live_overrides():
    """Verify live_overrides populate property_data and disabled_modules (flag=False path)."""
    ask_request = _make_ask_request()
    live_overrides = {
        "property_data": "Welcome to Oakwood Apartments...",
        "disabled_modules": ["PACKAGES", "EVENTS"],
        "disabled_tools": ["get_residents_packages", "fetch_community_events"],
    }
    with patch("agent_leasing.cli_render_prompt.settings") as mock_settings:
        mock_settings.property_marketing_info_tool_enabled = False
        scope = build_session_scope(ask_request, live_overrides=live_overrides)

    assert scope.property_data == "Welcome to Oakwood Apartments..."
    assert scope.disabled_modules == ["PACKAGES", "EVENTS"]
    assert scope.disabled_tools == ["get_residents_packages", "fetch_community_events"]
    # Non-overridden defaults still hold
    assert scope.identity_verified == {}
    assert scope.packages is None


def test_build_session_scope_context_overrides_win():
    """Verify --context overrides beat --live data (flag=False path)."""
    ask_request = _make_ask_request()
    live_overrides = {
        "property_data": "Live property data from LDP",
        "disabled_modules": ["PACKAGES"],
    }
    context_overrides = {
        "property_data": "OVERRIDE from --context",
        "disabled_modules": ["EVENTS"],
    }
    with patch("agent_leasing.cli_render_prompt.settings") as mock_settings:
        mock_settings.property_marketing_info_tool_enabled = False
        scope = build_session_scope(ask_request, live_overrides=live_overrides, overrides=context_overrides)

    # --context wins for property_data
    assert scope.property_data == "OVERRIDE from --context"
    # --context wins for disabled_modules too
    assert scope.disabled_modules == ["EVENTS"]


@pytest.mark.asyncio
async def test_fetch_live_data_success():
    """Verify fetch_live_data returns correct dict from LDP data."""
    mock_ldp_response = {
        "resident_summary": "A luxury apartment community",
        "enabled_modules": ["PAYMENT_CENTER", "MR", "PACKAGES"],
        "pte_setting": True,
    }
    with patch(
        "agent_leasing.clients.ldp.fetch_ldp_property_data",
        return_value=mock_ldp_response,
    ):
        # flag=False: property_data included in result
        with patch("agent_leasing.cli_render_prompt.settings") as mock_settings:
            mock_settings.property_marketing_info_tool_enabled = False
            result_legacy = await fetch_live_data("12345")

        # flag=True: property_data NOT included in result
        with patch("agent_leasing.cli_render_prompt.settings") as mock_settings:
            mock_settings.property_marketing_info_tool_enabled = True
            result_tool = await fetch_live_data("12345")

    assert result_legacy["property_data"] == "A luxury apartment community"
    assert "property_data" not in result_tool
    # PARKING_PASS and EVENTS are not in enabled_modules, so they should be disabled
    assert "PARKING_PASS" in result_legacy["disabled_modules"]
    assert "EVENTS" in result_legacy["disabled_modules"]
    # PAYMENT_CENTER, MR, PACKAGES are enabled, so not in disabled
    assert "PAYMENT_CENTER" not in result_legacy["disabled_modules"]
    assert "MR" not in result_legacy["disabled_modules"]
    assert "PACKAGES" not in result_legacy["disabled_modules"]
    assert isinstance(result_legacy["disabled_tools"], list)


@pytest.mark.asyncio
async def test_fetch_live_data_empty_summary():
    """Verify fetch_live_data handles None resident_summary gracefully."""
    mock_ldp_response = {
        "resident_summary": None,
        "enabled_modules": ["PAYMENT_CENTER", "MR", "PACKAGES", "PARKING_PASS", "EVENTS"],
        "pte_setting": False,
    }
    with patch(
        "agent_leasing.clients.ldp.fetch_ldp_property_data",
        return_value=mock_ldp_response,
    ):
        with patch("agent_leasing.cli_render_prompt.settings") as mock_settings:
            mock_settings.property_marketing_info_tool_enabled = False
            result = await fetch_live_data("12345")

    assert result["property_data"] == ""
    assert result["disabled_modules"] == []
    assert result["disabled_tools"] == []


# -- Template rendering tests for live data scenarios -------------------------


def test_render_with_property_data():
    """Verify {{ context.property_data }} renders populated property overview (flag=False path)."""
    template_text = "Property Overview:\n```\n{{ context.property_data }}\n```"
    ask_request = _make_ask_request()
    with patch("agent_leasing.cli_render_prompt.settings") as mock_settings:
        mock_settings.property_marketing_info_tool_enabled = False
        scope = build_session_scope(
            ask_request,
            live_overrides={"property_data": "A luxury 200-unit community with pool and gym"},
        )

    rendered = render_prompt(template_text, scope, "CHAT", "resident:instructions")
    assert "A luxury 200-unit community with pool and gym" in rendered


def test_render_insights_with_packages():
    """Verify INSIGHT NEWS section renders when packages populated."""
    template_text = "{% if context.packages %}Packages: {{ context.packages }}{% endif %}"
    ask_request = _make_ask_request()

    # Without packages - empty
    scope_empty = build_session_scope(ask_request)
    rendered_empty = render_prompt(template_text, scope_empty, "CHAT", "resident:instructions")
    assert rendered_empty == ""

    # With packages - renders
    scope_pkgs = build_session_scope(ask_request, overrides={"packages": "2 packages awaiting pickup"})
    rendered_pkgs = render_prompt(template_text, scope_pkgs, "CHAT", "resident:instructions")
    assert "2 packages awaiting pickup" in rendered_pkgs


def test_render_insights_with_service_requests():
    """Verify INSIGHT NEWS section renders service requests when populated."""
    template_text = (
        "{% if context.service_requests %}Active Service Requests: {{ context.service_requests }}{% endif %}"
    )
    ask_request = _make_ask_request()

    scope = build_session_scope(ask_request, overrides={"service_requests": "SR-1234: Leaky faucet (In Progress)"})
    rendered = render_prompt(template_text, scope, "CHAT", "resident:instructions")
    assert "SR-1234: Leaky faucet (In Progress)" in rendered


def test_render_verification_unverified():
    """Verify unverified branch renders ask-for-unit prompt."""
    template_text = (
        "{% if not context.is_identity_verified(channel) %}"
        "ask for unit number"
        "{% elif channel != 'VOICE' and not context.is_identity_verified_with_birth_year(channel) %}"
        "ask for birth year"
        "{% else %}"
        "Fully verified"
        "{% endif %}"
    )
    ask_request = _make_ask_request()

    # Unverified (default)
    scope = build_session_scope(ask_request)
    rendered = render_prompt(template_text, scope, "CHAT", "resident:instructions")
    assert "ask for unit number" in rendered


def test_render_verification_unit_only():
    """Verify unit-verified-but-no-birth-year branch renders for non-VOICE."""
    template_text = (
        "{% if not context.is_identity_verified(channel) %}"
        "ask for unit number"
        "{% elif channel != 'VOICE' and not context.is_identity_verified_with_birth_year(channel) %}"
        "ask for birth year"
        "{% else %}"
        "Fully verified"
        "{% endif %}"
    )
    ask_request = _make_ask_request()
    scope = build_session_scope(
        ask_request,
        overrides={"identity_verified": {"CHAT": True}, "identity_verified_with_birth_year": {}},
    )

    rendered = render_prompt(template_text, scope, "CHAT", "resident:instructions")
    assert "ask for birth year" in rendered


def test_render_verification_fully_verified():
    """Verify fully verified branch renders."""
    template_text = (
        "{% if not context.is_identity_verified(channel) %}"
        "ask for unit number"
        "{% elif channel != 'VOICE' and not context.is_identity_verified_with_birth_year(channel) %}"
        "ask for birth year"
        "{% else %}"
        "Fully verified"
        "{% endif %}"
    )
    ask_request = _make_ask_request()
    scope = build_session_scope(
        ask_request,
        overrides={"identity_verified": {"CHAT": True}, "identity_verified_with_birth_year": {"CHAT": True}},
    )

    rendered = render_prompt(template_text, scope, "CHAT", "resident:instructions")
    assert "Fully verified" in rendered
