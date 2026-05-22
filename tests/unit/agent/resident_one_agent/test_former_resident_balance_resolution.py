"""Tests for the former-resident `balance_resolution` scope restriction.

Refs PRs #1589, #1385.

When the incoming payload carries ``product_info.former_type == "balance_resolution"``,
the LLM agent must restrict itself to the Policy and Ledger workflow only — every
other workflow (Facilities/SR, Packages, Parking, Community Events, Property Q&A,
etc.) is out of scope and the agent must redirect or hand off.

The field must be accepted across all four channels (CHAT, SMS, EMAIL, VOICE).
``former_type=None`` (or omitted) preserves the existing un-restricted behavior.
"""

import uuid

import pytest

from agent_leasing.api.model import (
    AIConfig,
    AskRequest,
    Product,
    ProductInfo,
    UCReference,
)

from .conftest import render_template


def _build_product_info(former_type: str | None = None) -> ProductInfo:
    kwargs = dict(
        knock_property_id="21521",
        ai_config=AIConfig(pna_va_enabled=False),
        knock_prospect_id="1",
        uc_portal_base_url="https://cassidysouth.qa1.loftliving.com",
        uc_resident_member_id=UCReference(id=1, source=""),
        uc_resident_household_id=UCReference(id=2, source=""),
        uc_company_id=UCReference(id=3, source=""),
        uc_property_id=UCReference(id=4, source=""),
        ab_resident_id=UCReference(id=5, source=""),
        uc_lease_id=UCReference(id=6, source=""),
    )
    if former_type is not None:
        kwargs["former_type"] = former_type
    return ProductInfo(**kwargs)


class TestFormerTypeFieldOnProductInfo:
    """Payload contract: ``former_type`` is part of ``product_info`` and tolerates
    ``"balance_resolution"`` or ``None``."""

    def test_former_type_defaults_to_none(self):
        info = _build_product_info()
        assert info.former_type is None

    def test_former_type_accepts_balance_resolution(self):
        info = _build_product_info(former_type="balance_resolution")
        assert info.former_type == "balance_resolution"

    def test_former_type_accepts_explicit_none(self):
        info = _build_product_info(former_type=None)
        assert info.former_type is None

    @pytest.mark.parametrize(
        "product",
        [
            Product.RESIDENT_ONE_CHAT,
            Product.RESIDENT_ONE_SMS,
            Product.RESIDENT_ONE_EMAIL,
            Product.RESIDENT_ONE_VOICE,
        ],
    )
    def test_former_type_round_trips_through_ask_request_on_every_channel(self, product):
        """The field must survive AskRequest construction on every channel."""
        request = AskRequest(
            chat_session_id=uuid.uuid4().hex,
            product=product.value,
            product_info=_build_product_info(former_type="balance_resolution"),
        )
        assert request.product_info.former_type == "balance_resolution"


class TestFormerResidentScopeRestrictionRendering:
    """When ``former_type == "balance_resolution"``, the rendered prompt must
    restrict the agent to the Policy and Ledger workflow only.

    The prompt template is the single source of truth for the agent's behavior,
    so we assert on the rendered text rather than on a code-level switch.
    """

    @pytest.mark.parametrize("channel", ["CHAT", "SMS", "EMAIL", "VOICE"])
    def test_balance_resolution_renders_former_resident_restriction_block(
        self, instructions_template, mock_context, mock_settings, channel
    ):
        """The prompt must surface an explicit former-resident restriction block."""
        mock_context.ask_request.product_info.former_type = "balance_resolution"

        rendered = render_template(
            instructions_template,
            channel,
            mock_context,
            mock_settings,
            former_type="balance_resolution",
        )

        # An explicit marker the agent prompt uses to gate behavior.
        assert "FORMER RESIDENT MODE" in rendered, (
            f"channel={channel}: prompt missing the former-resident restriction header"
        )
        # The restriction must explicitly call out Policy and Ledger as the only
        # in-scope workflow.
        assert "Policy and Ledger" in rendered

    @pytest.mark.parametrize("channel", ["CHAT", "SMS", "EMAIL", "VOICE"])
    def test_balance_resolution_drops_other_workflow_offers(
        self, instructions_template, mock_context, mock_settings, channel
    ):
        """In balance_resolution mode the prompt must NOT advertise non-ledger workflows
        as in-scope (Facilities, Packages, Parking, Events). Other workflow sections
        may still exist for ledger cross-references, but the *Available* list and
        *On-Topic* list must restrict to Policy and Ledger only.
        """
        mock_context.ask_request.product_info.former_type = "balance_resolution"

        rendered = render_template(
            instructions_template,
            channel,
            mock_context,
            mock_settings,
            former_type="balance_resolution",
        )

        # Pull just the OFF-TOPIC HANDLING section and confirm only Policy/Ledger
        # is advertised as in-scope.
        off_topic_index = rendered.index("# OFF-TOPIC HANDLING")
        next_header_index = rendered.index("\n# ", off_topic_index + 1)
        off_topic_section = rendered[off_topic_index:next_header_index]

        assert "Policy/Ledger" in off_topic_section
        # Other modules must NOT be listed as on-topic for a balance-resolution session.
        assert "Packages" not in off_topic_section
        assert "Guest Parking" not in off_topic_section
        assert "Maintenance/Service Requests" not in off_topic_section
        assert "Community Events" not in off_topic_section

    @pytest.mark.parametrize("channel", ["CHAT", "SMS", "EMAIL", "VOICE"])
    def test_null_former_type_preserves_full_scope(self, instructions_template, mock_context, mock_settings, channel):
        """When ``former_type`` is None (the common case), the prompt must NOT add
        the restriction block — regression guard against accidentally tripping
        the gate on normal residents.
        """
        mock_context.ask_request.product_info.former_type = None

        rendered = render_template(
            instructions_template,
            channel,
            mock_context,
            mock_settings,
            former_type=None,
        )

        assert "FORMER RESIDENT MODE" not in rendered, (
            f"channel={channel}: restriction block leaked into a normal resident's prompt"
        )

    @pytest.mark.parametrize("channel", ["CHAT", "SMS", "EMAIL", "VOICE"])
    def test_missing_former_type_preserves_full_scope(
        self, instructions_template, mock_context, mock_settings, channel
    ):
        """Backwards compat: payloads that omit ``former_type`` entirely render
        the normal (un-restricted) prompt."""
        # mock_context already doesn't set former_type; emulate the un-rendered case
        # by explicitly passing None for the template variable.
        mock_context.ask_request.product_info.former_type = None

        rendered = render_template(
            instructions_template,
            channel,
            mock_context,
            mock_settings,
            former_type=None,
        )

        assert "FORMER RESIDENT MODE" not in rendered


class TestFormerResidentContextVariablePropagation:
    """``former_type`` must be threaded from ``product_info`` into the template
    context variables that drive instruction rendering. Without this wiring, the
    INSTRUCTIONS.md Jinja template can never observe the flag."""

    def test_former_type_appears_in_instructions_context_variables(self):
        """The base resident agent must include ``former_type`` in the variable
        dict passed to the instructions template.
        """
        from unittest.mock import MagicMock

        from agent_leasing.agent.resident_one_agent.agent import BaseResidentAgent

        # Construct a minimal context mirroring a balance-resolution payload.
        ctx = MagicMock()
        ctx.ask_request.product_info.former_type = "balance_resolution"

        # _build_instructions_context_variables is the function that assembles
        # the kwargs passed into ``template.render(...)`` for INSTRUCTIONS.md.
        # BaseResidentAgent is abstract; bypass __init__ to test the method.
        instance = MagicMock(spec=BaseResidentAgent)
        instance.context = ctx
        instance._build_base_context_variables = lambda *a, **kw: BaseResidentAgent._build_base_context_variables(
            instance, *a, **kw
        )

        variables = BaseResidentAgent._build_instructions_context_variables(
            instance,
            ctx,
            channel="CHAT",
            available_services=[],
            is_office_open=True,
        )

        assert "former_type" in variables, (
            "_build_instructions_context_variables must expose former_type so the prompt template can gate on it"
        )
        assert variables["former_type"] == "balance_resolution"
