"""Tests for emergency service product code resolution logic."""

from unittest.mock import patch

import pytest

from agent_leasing.api.model import EmergencyServiceProduct, _resolve_emergency_product_from_code


class TestResolveEmergencyProductFromCode:
    """Tests for _resolve_emergency_product_from_code."""

    @pytest.mark.parametrize(
        "value, lo_property_id, expected",
        [
            # New SKU strings
            ("BASIC", None, EmergencyServiceProduct.BASIC),
            ("BASIC", "27504", EmergencyServiceProduct.BASIC),
            ("ADVANCED", "27504", EmergencyServiceProduct.ADVANCED),
            ("RPCC", "27504", EmergencyServiceProduct.RPCC),
            # Legacy upstream SKU codes (still accepted)
            ("None", None, EmergencyServiceProduct.BASIC),
            ("None", "27504", EmergencyServiceProduct.BASIC),
            ("AI Maintenance", "27504", EmergencyServiceProduct.ADVANCED),
            ("AA", "27504", EmergencyServiceProduct.ADVANCED),
            # Legacy bool — True meant "advanced dispatch is active"
            (True, "27504", EmergencyServiceProduct.ADVANCED),
            (False, "27504", EmergencyServiceProduct.BASIC),
            (False, None, EmergencyServiceProduct.BASIC),
            # Absent field -> BASIC
            (None, None, EmergencyServiceProduct.BASIC),
            (None, "27504", EmergencyServiceProduct.BASIC),
        ],
    )
    def test_standard_mappings(self, value, lo_property_id, expected):
        # Voice path — non-voice has a separate fallback rule for RPCC (tested below).
        result = _resolve_emergency_product_from_code(value, lo_property_id, is_voice=True)
        assert result == expected

    def test_unknown_code_falls_back_to_basic(self):
        result = _resolve_emergency_product_from_code("SomethingNew", "27504")
        assert result == EmergencyServiceProduct.BASIC

    @pytest.mark.parametrize(
        "product_code",
        ["AI Maintenance", "RPCC", "AA"],
    )
    def test_missing_lo_property_id_falls_back_to_basic(self, product_code):
        """ADVANCED and RPCC require lo_property_id; fall back to BASIC without it."""
        result = _resolve_emergency_product_from_code(product_code, None)
        assert result == EmergencyServiceProduct.BASIC

    def test_advanced_disabled_by_feature_flag(self):
        with patch(
            "agent_leasing.api.model.settings.emergency_service_transfer_advanced_enabled",
            False,
        ):
            result = _resolve_emergency_product_from_code("AI Maintenance", "27504")
            assert result == EmergencyServiceProduct.BASIC

    def test_aa_disabled_by_advanced_feature_flag(self):
        """AA maps to ADVANCED, so the advanced feature flag controls it."""
        with patch(
            "agent_leasing.api.model.settings.emergency_service_transfer_advanced_enabled",
            False,
        ):
            result = _resolve_emergency_product_from_code("AA", "27504")
            assert result == EmergencyServiceProduct.BASIC

    def test_rpcc_disabled_by_feature_flag(self):
        with patch(
            "agent_leasing.api.model.settings.emergency_service_transfer_rpcc_enabled",
            False,
        ):
            result = _resolve_emergency_product_from_code("RPCC", "27504")
            assert result == EmergencyServiceProduct.BASIC

    def test_rpcc_enabled_does_not_affect_advanced(self):
        """Disabling RPCC should not affect AI Maintenance / AA."""
        with patch(
            "agent_leasing.api.model.settings.emergency_service_transfer_rpcc_enabled",
            False,
        ):
            result = _resolve_emergency_product_from_code("AI Maintenance", "27504")
            assert result == EmergencyServiceProduct.ADVANCED

    def test_advanced_enabled_does_not_affect_rpcc(self):
        """Disabling ADVANCED should not affect RPCC (voice path)."""
        with patch(
            "agent_leasing.api.model.settings.emergency_service_transfer_advanced_enabled",
            False,
        ):
            result = _resolve_emergency_product_from_code("RPCC", "27504", is_voice=True)
            assert result == EmergencyServiceProduct.RPCC

    def test_rpcc_nonvoice_routes_to_advanced(self):
        """Non-voice RPCC unconditionally routes to ADVANCED (RPCC non-voice not production-ready)."""
        result = _resolve_emergency_product_from_code("RPCC", "27504", is_voice=False)
        assert result == EmergencyServiceProduct.ADVANCED

    def test_rpcc_voice_routes_to_rpcc(self):
        """Voice RPCC stays on RPCC tool."""
        result = _resolve_emergency_product_from_code("RPCC", "27504", is_voice=True)
        assert result == EmergencyServiceProduct.RPCC

    def test_rpcc_nonvoice_falls_back_to_basic_if_advanced_disabled(self):
        """When the ADVANCED kill switch is off, RPCC non-voice must not smuggle traffic to ADVANCED."""
        with patch(
            "agent_leasing.api.model.settings.emergency_service_transfer_advanced_enabled",
            False,
        ):
            result = _resolve_emergency_product_from_code("RPCC", "27504", is_voice=False)
            assert result == EmergencyServiceProduct.BASIC
