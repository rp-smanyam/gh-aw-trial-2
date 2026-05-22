"""Tests for verification_check module."""

from types import SimpleNamespace

import pytest

from agent_leasing.agent.tools.mcp_pre_processors import VerificationError, verification_pre_processor
from agent_leasing.agent.tools.verification_check import PROTECTED_TOOLS, check_verification_status
from agent_leasing.settings import settings


class _VerificationContext(SimpleNamespace):
    """SimpleNamespace with channel-aware verification methods for testing."""

    def is_identity_verified(self, channel: str) -> bool:
        return self.identity_verified.get(channel, False)

    def is_identity_verified_with_birth_year(self, channel: str) -> bool:
        return self.identity_verified_with_birth_year.get(channel, False)

    def get_verification_attempts(self, channel: str) -> int:
        return self.verification_attempts.get(channel, 0)

    def set_identity_verified(self, channel: str, value: bool = True) -> None:
        self.identity_verified[channel] = value

    def set_identity_verified_with_birth_year(self, channel: str, value: bool = True) -> None:
        self.identity_verified_with_birth_year[channel] = value

    def increment_verification_attempts(self, channel: str) -> None:
        self.verification_attempts[channel] = self.verification_attempts.get(channel, 0) + 1


def _make_context(product: str) -> _VerificationContext:
    product_info = SimpleNamespace(ab_unit_number="64", date_of_birth="09/09/1960")
    ask_request = SimpleNamespace(product=product, product_info=product_info)
    return _VerificationContext(
        ask_request=ask_request,
        identity_verified={},
        identity_verified_with_birth_year={},
        verification_attempts={},
    )


@pytest.fixture
def context_chat():
    """Context for CHAT channel (pre-authenticated)."""
    return _make_context("RESIDENT_ONE_CHAT")


@pytest.fixture
def context_voice():
    """Context for VOICE channel."""
    return _make_context("RESIDENT_ONE_VOICE")


@pytest.fixture
def context_sms():
    """Context for SMS channel."""
    return _make_context("RESIDENT_ONE_SMS")


class TestCheckVerificationStatus:
    """Tests for check_verification_status function."""

    def test_chat_always_passes(self, context_chat):
        """CHAT channel bypasses verification."""
        is_verified, error = check_verification_status(context_chat, "call_facilities_thinker_via_api")
        assert is_verified is True
        assert error is None

    def test_unprotected_tool_passes(self, context_sms):
        """Non-protected tools pass without verification."""
        is_verified, error = check_verification_status(context_sms, "create_link")
        assert is_verified is True
        assert error is None

    def test_protected_tool_fails_without_verification(self, context_sms):
        """Protected tools fail if not verified."""
        is_verified, error = check_verification_status(context_sms, "call_facilities_thinker_via_api")
        assert is_verified is False
        assert "VERIFICATION_REQUIRED" in error

    def test_unit_tool_passes_when_verified(self, context_sms):
        """Unit-only tools pass when identity_verified=True."""
        context_sms.set_identity_verified("SMS")
        is_verified, error = check_verification_status(context_sms, "call_facilities_thinker_via_api")
        assert is_verified is True
        assert error is None

    def test_birth_year_tool_fails_without_birth_year(self, context_sms):
        """Birth year tools fail if only unit verified."""
        context_sms.set_identity_verified("SMS")
        is_verified, error = check_verification_status(context_sms, "get_rent_information")
        assert is_verified is False
        assert "birth_year" in error

    def test_birth_year_tool_passes_with_full_verification(self, context_sms):
        """Birth year tools pass with full verification."""
        context_sms.set_identity_verified("SMS")
        context_sms.set_identity_verified_with_birth_year("SMS")
        is_verified, error = check_verification_status(context_sms, "get_rent_information")
        assert is_verified is True
        assert error is None

    def test_voice_rent_requires_birth_year(self, context_voice):
        """VOICE channel fails get_rent_information with only unit verified (no birth year)."""
        context_voice.set_identity_verified("VOICE")
        is_verified, error = check_verification_status(context_voice, "get_rent_information")
        assert is_verified is False
        assert "birth_year" in error

    def test_voice_rent_passes_with_birth_year(self, context_voice):
        """VOICE channel passes get_rent_information with unit and birth year verified."""
        context_voice.set_identity_verified("VOICE")
        context_voice.set_identity_verified_with_birth_year("VOICE")
        is_verified, error = check_verification_status(context_voice, "get_rent_information")
        assert is_verified is True
        assert error is None

    def test_voice_rent_fails_without_any_verification(self, context_voice):
        """VOICE channel still fails get_rent_information without any verification."""
        is_verified, error = check_verification_status(context_voice, "get_rent_information")
        assert is_verified is False
        assert "VERIFICATION_REQUIRED" in error

    def test_create_service_request_fails_without_verification(self, context_sms):
        """create_service_request fails if not verified."""
        is_verified, error = check_verification_status(context_sms, "create_service_request")
        assert is_verified is False
        assert "VERIFICATION_REQUIRED" in error

    def test_create_service_request_passes_when_verified(self, context_sms):
        """create_service_request passes when identity_verified=True."""
        context_sms.set_identity_verified("SMS")
        is_verified, error = check_verification_status(context_sms, "create_service_request")
        assert is_verified is True
        assert error is None

    def test_get_active_service_requests_fails_without_verification(self, context_sms):
        """get_active_service_requests fails if not verified."""
        is_verified, error = check_verification_status(context_sms, "get_active_service_requests")
        assert is_verified is False
        assert "VERIFICATION_REQUIRED" in error

    def test_get_active_service_requests_passes_when_verified(self, context_sms):
        """get_active_service_requests passes when identity_verified=True."""
        context_sms.set_identity_verified("SMS")
        is_verified, error = check_verification_status(context_sms, "get_active_service_requests")
        assert is_verified is True
        assert error is None

    def test_sms_verification_does_not_apply_to_email(self):
        """Verification on SMS channel should not carry over to email."""
        # Create a context verified for SMS only
        context = _make_context("RESIDENT_ONE_SMS")
        context.set_identity_verified("SMS")

        # SMS should pass
        is_verified, error = check_verification_status(context, "create_service_request")
        assert is_verified is True
        assert error is None

        # Switch product to EMAIL — same context, different channel
        context.ask_request.product = "RESIDENT_ONE_EMAIL"
        is_verified, error = check_verification_status(context, "create_service_request")
        assert is_verified is False
        assert "VERIFICATION_REQUIRED" in error


class TestVerificationPreProcessor:
    """Tests for verification_pre_processor factory."""

    def test_passes_when_verified(self, context_sms):
        """Pre-processor passes arguments through when verified."""
        context_sms.set_identity_verified("SMS")
        pre_processor = verification_pre_processor("call_facilities_thinker_via_api")
        result = pre_processor({"message": "test"}, context=context_sms)
        assert result == {"message": "test"}

    def test_raises_when_not_verified(self, context_sms):
        """Pre-processor raises VerificationError when not verified."""
        pre_processor = verification_pre_processor("call_facilities_thinker_via_api")
        with pytest.raises(VerificationError):
            pre_processor({"message": "test"}, context=context_sms)

    def test_chat_passes_without_verification(self, context_chat):
        """Pre-processor passes for CHAT channel."""
        pre_processor = verification_pre_processor("call_facilities_thinker_via_api")
        result = pre_processor({"message": "test"}, context=context_chat)
        assert result == {"message": "test"}

    def test_create_service_request_raises_when_not_verified(self, context_sms):
        """Pre-processor raises VerificationError for create_service_request when not verified."""
        pre_processor = verification_pre_processor("create_service_request")
        with pytest.raises(VerificationError):
            pre_processor({"description": "leaky faucet"}, context=context_sms)

    def test_create_service_request_passes_when_verified(self, context_sms):
        """Pre-processor passes for create_service_request when verified."""
        context_sms.set_identity_verified("SMS")
        pre_processor = verification_pre_processor("create_service_request")
        result = pre_processor({"description": "leaky faucet"}, context=context_sms)
        assert result == {"description": "leaky faucet"}

    def test_uses_call_time_context_not_closure(self, context_sms):
        """Regression: pre-processor uses context passed at call time, not from factory closure.

        When MCP connections are pooled, the pre-processor is created once but
        called across multiple turns. Each turn has a fresh SessionScope.
        The processor must use the context given at call time so that
        verification state from the current turn is respected.
        """
        # Create processor (no context captured)
        pre_processor = verification_pre_processor("call_facilities_thinker_via_api")

        # First turn: not verified -> should raise
        with pytest.raises(VerificationError):
            pre_processor({"msg": "t1"}, context=context_sms)

        # Simulate pool reuse: new context for second turn, already verified
        new_context = _make_context("RESIDENT_ONE_SMS")
        new_context.set_identity_verified("SMS")
        result = pre_processor({"msg": "t2"}, context=new_context)
        assert result == {"msg": "t2"}


class TestProtectedToolsConfig:
    """Tests for PROTECTED_TOOLS configuration."""

    def test_facilities_requires_unit_only(self):
        assert "call_facilities_thinker_via_api" in PROTECTED_TOOLS
        assert PROTECTED_TOOLS["call_facilities_thinker_via_api"] is False

    def test_parking_requires_unit_only(self):
        assert "issue_guest_parking_pass" in PROTECTED_TOOLS
        assert PROTECTED_TOOLS["issue_guest_parking_pass"] is False

    def test_rent_requires_birth_year(self):
        assert "get_rent_information" in PROTECTED_TOOLS
        assert PROTECTED_TOOLS["get_rent_information"] is True

    def test_create_service_request_requires_unit_only(self):
        assert "create_service_request" in PROTECTED_TOOLS
        assert PROTECTED_TOOLS["create_service_request"] is False

    def test_get_active_service_requests_requires_unit_only(self):
        assert "get_active_service_requests" in PROTECTED_TOOLS
        assert PROTECTED_TOOLS["get_active_service_requests"] is False


class TestVerificationDisabled:
    """Tests for identity_verification_enabled=False toggle."""

    def test_protected_tool_passes_when_verification_disabled(self, context_sms, monkeypatch):
        """Protected tools pass when verification is globally disabled."""
        monkeypatch.setattr(settings, "identity_verification_enabled", False)
        is_verified, error = check_verification_status(context_sms, "call_facilities_thinker_via_api")
        assert is_verified is True
        assert error is None

    def test_birth_year_tool_passes_when_verification_disabled(self, context_sms, monkeypatch):
        """Birth-year tools also pass when verification is globally disabled."""
        monkeypatch.setattr(settings, "identity_verification_enabled", False)
        is_verified, error = check_verification_status(context_sms, "get_rent_information")
        assert is_verified is True
        assert error is None

    def test_pre_processor_passes_when_verification_disabled(self, context_sms, monkeypatch):
        """MCP pre-processor passes when verification is globally disabled."""
        monkeypatch.setattr(settings, "identity_verification_enabled", False)
        pre_processor = verification_pre_processor("call_facilities_thinker_via_api")
        result = pre_processor({"msg": "test"}, context=context_sms)
        assert result == {"msg": "test"}
