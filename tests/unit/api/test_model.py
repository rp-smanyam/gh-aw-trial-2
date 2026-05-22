import uuid

import pytest
from pydantic_core import ValidationError

from agent_leasing.api.model import (
    AIConfig,
    AskRequest,
    Product,
    ProductInfo,
    UCReference,
    examples,
)


class TestAskRequest:
    def test_valid_resident(self):
        assert AskRequest(
            chat_session_id=uuid.uuid4().hex,
            product=Product.RESIDENT_ONE_CHAT.value,
            product_info=ProductInfo(
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
            ),
        )

    def test_invalid_resident(self):
        with pytest.raises(ValidationError, match="Missing required fields for resident persona"):
            AskRequest(
                chat_session_id=uuid.uuid4().hex,
                product=Product.RESIDENT_ONE_CHAT.value,
                product_info=ProductInfo(
                    knock_property_id="21521",
                    uc_portal_base_url="https://cassidysouth.qa1.loftliving.com",
                    ai_config=AIConfig(pna_va_enabled=False),
                    knock_resident_id="1",
                    uc_resident_member_id=UCReference(id=1, source=""),
                    uc_resident_household_id=None,
                    uc_company_id=UCReference(id=3, source=""),
                    uc_property_id=UCReference(id=4, source=""),
                ),
            )

    def test_valid_example_resident_chat_ll(self):
        assert AskRequest(**examples.ASK_REQUEST_RESIDENT_CHAT_LL)

    def test_valid_example_resident_sms_knck(self):
        assert AskRequest(**examples.ASK_REQUEST_RESIDENT_SMS_KNCK)

    def test_valid_example_resident_sms_ll(self):
        assert AskRequest(**examples.ASK_REQUEST_RESIDENT_SMS_LL)

    def test_valid_example_resident_email_knck(self):
        assert AskRequest(**examples.ASK_REQUEST_RESIDENT_EMAIL_KNCK)

    def test_valid_example_resident_voice_knck(self):
        assert AskRequest(**examples.ASK_REQUEST_RESIDENT_VOICE_KNCK)

    @pytest.mark.parametrize(
        "product",
        [
            Product.RESIDENT_ONE_CHAT,
            Product.RESIDENT_ONE_EMAIL,
            Product.RESIDENT_ONE_SMS,
            Product.RESIDENT_ONE_VOICE,
        ],
    )
    def test_all_resident_products_valid_with_required_fields(self, product):
        """Test that all resident product types pass validation when required fields are present."""
        request = AskRequest(
            chat_session_id=uuid.uuid4().hex,
            product=product,
            product_info=ProductInfo(
                knock_property_id="21521",
                uc_portal_base_url="https://cassidysouth.qa1.loftliving.com",
                ai_config=AIConfig(pna_va_enabled=False),
                knock_prospect_id="1",
                uc_resident_member_id=UCReference(id=1, source=""),
                uc_resident_household_id=UCReference(id=2, source=""),
                uc_company_id=UCReference(id=3, source=""),
                uc_property_id=UCReference(id=4, source=""),
                ab_resident_id=UCReference(id=5, source=""),
                uc_lease_id=UCReference(id=6, source=""),
            ),
        )
        assert request.product == product

    @pytest.mark.parametrize(
        "product",
        [
            Product.RESIDENT_ONE_SMS,
            Product.RESIDENT_ONE_EMAIL,
            Product.RESIDENT_ONE_CHAT,
            Product.RESIDENT_ONE_VOICE,
        ],
    )
    def test_all_resident_products_invalid_missing_fields(self, product):
        """Test that all resident product types fail validation when required fields are missing."""
        with pytest.raises(ValidationError, match="Missing required fields for resident persona"):
            AskRequest(
                chat_session_id=uuid.uuid4().hex,
                product=product,
                product_info=ProductInfo(
                    knock_property_id="21521",
                    ai_config=AIConfig(pna_va_enabled=False),
                    knock_prospect_id="1",
                ),
            )

    def test_non_resident_products_skip_validation(self):
        """Test that non-resident products don't trigger resident validation."""
        # SIMPLE is a prospect product, should not raise ValidationError even without resident fields
        request = AskRequest(
            chat_session_id=uuid.uuid4().hex,
            product=Product.SIMPLE,
            product_info=ProductInfo(
                knock_property_id="21521",
                ai_config=AIConfig(pna_va_enabled=False),
                knock_prospect_id="1",
            ),
        )
        assert request.product == Product.SIMPLE

    def test_get_missing_fields_returns_all_missing_resident_fields(self):
        """Test that _get_missing_fields returns the correct list of missing fields."""
        request = AskRequest(
            chat_session_id=uuid.uuid4().hex,
            product=Product.SIMPLE,  # Use prospect to bypass validation
            product_info=ProductInfo(
                knock_property_id="21521",
                ai_config=AIConfig(pna_va_enabled=False),
                # Only provide uc_company_id, leave others missing
                uc_company_id=UCReference(id=1, source=""),
            ),
        )
        # Manually set persona context to resident for testing _get_missing_fields
        request.product = Product.RESIDENT_ONE_CHAT.value
        missing = request._get_missing_fields()

        assert "product_info.uc_property_id" in missing
        assert "product_info.uc_resident_household_id" in missing
        assert "product_info.uc_resident_member_id" in missing
        assert "product_info.ab_resident_id" in missing
        assert "product_info.uc_lease_id" in missing
        assert "product_info.uc_company_id" not in missing  # This one was provided

    def test_validation_error_message_includes_missing_fields(self):
        """Test that the validation error message lists the specific missing fields."""
        with pytest.raises(ValidationError) as exc_info:
            AskRequest(
                chat_session_id=uuid.uuid4().hex,
                product=Product.RESIDENT_ONE_CHAT.value,
                product_info=ProductInfo(
                    knock_property_id="21521",
                    ai_config=AIConfig(pna_va_enabled=False),
                    uc_company_id=UCReference(id=1, source=""),
                    # Missing: uc_property_id, uc_resident_household_id, uc_resident_member_id,
                    # ab_resident_id, uc_lease_id
                ),
            )
        error_message = str(exc_info.value)
        assert "product_info.uc_property_id" in error_message
        assert "product_info.uc_resident_household_id" in error_message
        assert "product_info.uc_resident_member_id" in error_message
        assert "product_info.ab_resident_id" in error_message
        assert "product_info.uc_lease_id" in error_message

    def test_is_load_test_defaults_to_false(self):
        request = AskRequest(
            chat_session_id=uuid.uuid4().hex,
            product=Product.RESIDENT_ONE_CHAT.value,
            product_info=ProductInfo(
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
            ),
        )
        assert request.is_load_test is False

    def test_is_load_test_accepts_true(self):
        request = AskRequest(
            chat_session_id=uuid.uuid4().hex,
            product=Product.RESIDENT_ONE_CHAT.value,
            is_load_test=True,
            product_info=ProductInfo(
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
            ),
        )
        assert request.is_load_test is True

    def test_missing_uc_lease_id_rejects_resident(self):
        """Resident requests must carry ``uc_lease_id`` — agent-leasing#1405 §3.1.

        Pinned defensively: if cai-genai-service regresses and stops populating
        ``uc_lease_id``, this validator must reject the request so the LLM never sees
        ``lease_id=`` empty and hallucinates a fabricated ID.
        """
        with pytest.raises(ValidationError, match="product_info.uc_lease_id"):
            AskRequest(
                chat_session_id=uuid.uuid4().hex,
                product=Product.RESIDENT_ONE_CHAT.value,
                product_info=ProductInfo(
                    knock_property_id="21521",
                    ai_config=AIConfig(pna_va_enabled=False),
                    knock_prospect_id="1",
                    uc_portal_base_url="https://cassidysouth.qa1.loftliving.com",
                    uc_resident_member_id=UCReference(id=1, source=""),
                    uc_resident_household_id=UCReference(id=2, source=""),
                    uc_company_id=UCReference(id=3, source=""),
                    uc_property_id=UCReference(id=4, source=""),
                    ab_resident_id=UCReference(id=5, source=""),
                    uc_lease_id=None,
                ),
            )
