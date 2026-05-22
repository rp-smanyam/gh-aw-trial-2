"""Tests that ``ProductInfo`` accepts the new ``former_type`` payload field.

Refs PRs #1589, #1385.

The incoming payload — for all four channels — now carries
``product_info.former_type`` with value ``"balance_resolution"`` or ``null``.
Reject the field at parse time and the agent never gets a chance to scope
itself to Policy and Ledger only.
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


def _make_resident_product_info(**overrides) -> ProductInfo:
    base = dict(
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
    base.update(overrides)
    return ProductInfo(**base)


class TestFormerTypeField:
    def test_former_type_defaults_to_none_when_omitted(self):
        info = _make_resident_product_info()
        assert info.former_type is None

    def test_former_type_explicit_none(self):
        info = _make_resident_product_info(former_type=None)
        assert info.former_type is None

    def test_former_type_balance_resolution(self):
        info = _make_resident_product_info(former_type="balance_resolution")
        assert info.former_type == "balance_resolution"

    @pytest.mark.parametrize(
        "product",
        [
            Product.RESIDENT_ONE_CHAT,
            Product.RESIDENT_ONE_SMS,
            Product.RESIDENT_ONE_EMAIL,
            Product.RESIDENT_ONE_VOICE,
        ],
    )
    def test_former_type_round_trips_through_ask_request(self, product):
        """All channels must accept the field — the gate is channel-agnostic."""
        request = AskRequest(
            chat_session_id=uuid.uuid4().hex,
            product=product.value,
            product_info=_make_resident_product_info(former_type="balance_resolution"),
        )
        assert request.product_info.former_type == "balance_resolution"
