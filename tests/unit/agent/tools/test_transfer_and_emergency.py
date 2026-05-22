"""Unit tests for transfer_to_staff_voice, emergency_service_transfer_basic,
and emergency_service_transfer_advanced -- targeting uncovered lines."""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest

from agent_leasing.util.call_state_manager import PlaybackWaitResult

# ---------------------------------------------------------------------------
# Module imports
# ---------------------------------------------------------------------------

tts_voice = importlib.import_module("agent_leasing.agent.tools.transfer_to_staff.transfer_to_staff_voice")
estb_mod = importlib.import_module(
    "agent_leasing.agent.tools.emergency_service_transfer.basic.emergency_service_transfer_basic"
)
esta_mod = importlib.import_module(
    "agent_leasing.agent.tools.emergency_service_transfer.advanced.emergency_service_transfer_advanced"
)
esr_http_util = importlib.import_module("agent_leasing.agent.tools.emergency_service_transfer.http_util")

# ============================================================================
# Fake aiohttp helpers for _make_api_call tests
# ============================================================================


class _FakeResponse:
    """Lightweight stand-in for an aiohttp.ClientResponse."""

    def __init__(
        self,
        status: int = 200,
        body: str = "",
        json_data: Any = None,
        raise_on_json: bool = False,
    ):
        self.status = status
        self._body = body
        self._json_data = json_data
        self._raise_on_json = raise_on_json

    async def text(self):
        return self._body

    async def json(self, content_type=None):
        if self._raise_on_json:
            raise aiohttp.ContentTypeError(Mock(), Mock())
        return self._json_data


class _FakeRequestCM:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *args):
        pass


class _FakeSession:
    def __init__(self, response):
        self._response = response

    def request(self, **kwargs):
        return _FakeRequestCM(self._response)


class _FakeSessionCM:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        pass


def _patch_aiohttp(module, response: _FakeResponse):
    """Return a monkeypatch-ready callable that replaces aiohttp.ClientSession.

    The `module` parameter is kept for call-site clarity; the actual patch
    targets the shared http_util where ClientSession is looked up.
    """
    del module  # ClientSession lookup happens in http_util, not the per-tool modules
    session = _FakeSession(response)

    def _factory(*args, **kwargs):
        return _FakeSessionCM(session)

    return patch.object(esr_http_util.aiohttp, "ClientSession", side_effect=_factory)


# ============================================================================
# 1. transfer_to_staff_voice -- _transfer_to_staff_voice_impl
# ============================================================================


def _voice_ctx(*, call_state=None):
    """Build a minimal ctx for _transfer_to_staff_voice_impl."""
    ctx = SimpleNamespace(
        context=SimpleNamespace(
            call_ended_by_agent=False,
            call_management_in_progress=False,
            ask_request=SimpleNamespace(
                product_info=SimpleNamespace(
                    knock_resident_id="res-123",
                    resident_manager_id="mgr-456",
                    call_sid="CA123",
                ),
                product="resident_one_voice",
            ),
            call_state_manager=call_state,
        ),
    )
    return ctx


class TestTransferToStaffVoiceImpl:
    """Tests targeting _transfer_to_staff_voice_impl (lines 42-80)."""

    @pytest.mark.asyncio
    async def test_with_summary_proceeds_with_transfer(self, monkeypatch):
        """When summary is provided, should proceed with transfer."""
        ctx = _voice_ctx()
        monkeypatch.setattr(tts_voice, "get_call_state_from_context", lambda _ctx: None)
        monkeypatch.setattr(tts_voice, "_make_transfer_to_staff_api_call", AsyncMock())
        monkeypatch.setattr(tts_voice, "_transfer_twilio_call", AsyncMock())

        result = await tts_voice._transfer_to_staff_voice_impl(
            ctx,
            summary="User needs help with a package issue",
        )
        assert result == "Call transferred successfully."
        assert ctx.context.call_ended_by_agent is True

    @pytest.mark.asyncio
    async def test_no_summary_first_time_asks_for_summary(self):
        """When summary is None and haven't asked yet, should ask for summary."""
        ctx = _voice_ctx()
        result = await tts_voice._transfer_to_staff_voice_impl(
            ctx,
            summary=None,
        )
        assert "[Action Required]" in result
        assert "In a few words" in result
        assert "summary=None" in result
        assert ctx.context.transfer_summary_requested is True

    @pytest.mark.asyncio
    async def test_skip_summary_transfers_immediately(self, monkeypatch):
        """When skip_summary=True, should proceed with transfer even on first call."""
        ctx = _voice_ctx()
        monkeypatch.setattr(tts_voice, "get_call_state_from_context", lambda _ctx: None)
        monkeypatch.setattr(tts_voice, "_make_transfer_to_staff_api_call", AsyncMock())
        monkeypatch.setattr(tts_voice, "_transfer_twilio_call", AsyncMock())

        result = await tts_voice._transfer_to_staff_voice_impl(
            ctx,
            summary=None,
            skip_summary=True,
        )
        assert result == "Call transferred successfully."
        assert ctx.context.call_ended_by_agent is True

    @pytest.mark.asyncio
    async def test_no_summary_second_time_proceeds_with_default(self, monkeypatch):
        """When summary is None but already asked, should proceed with default message."""
        ctx = _voice_ctx()
        ctx.context.transfer_summary_requested = True
        monkeypatch.setattr(tts_voice, "get_call_state_from_context", lambda _ctx: None)
        monkeypatch.setattr(tts_voice, "_make_transfer_to_staff_api_call", AsyncMock())
        monkeypatch.setattr(tts_voice, "_transfer_twilio_call", AsyncMock())

        result = await tts_voice._transfer_to_staff_voice_impl(
            ctx,
            summary=None,
        )
        assert result == "Call transferred successfully."
        assert ctx.context.call_ended_by_agent is True

    @pytest.mark.asyncio
    async def test_playback_not_completed_logs_warning(self, monkeypatch):
        """When playback started but did not complete, log warning and proceed."""
        call_state = AsyncMock()
        call_state.wait_for_message_playback = AsyncMock(
            return_value=PlaybackWaitResult(
                success=True,
                started=True,
                completed=False,
            )
        )
        ctx = _voice_ctx(call_state=call_state)
        monkeypatch.setattr(tts_voice, "get_call_state_from_context", lambda _ctx: call_state)
        monkeypatch.setattr(tts_voice, "_make_transfer_to_staff_api_call", AsyncMock())
        monkeypatch.setattr(tts_voice, "_transfer_twilio_call", AsyncMock())

        result = await tts_voice._transfer_to_staff_voice_impl(
            ctx,
            summary="User needs help",
        )
        # Should still succeed
        assert result == "Call transferred successfully."

    @pytest.mark.asyncio
    async def test_successful_transfer(self, monkeypatch):
        """Happy path: summary provided, no call_state, transfer succeeds.
        Knock API call must complete before Twilio transfer to prevent data loss."""
        call_order: list[str] = []

        async def _mock_api_call(*args, **kwargs):
            call_order.append("knock_api")

        async def _mock_twilio_call(*args, **kwargs):
            call_order.append("twilio_transfer")

        ctx = _voice_ctx()
        monkeypatch.setattr(tts_voice, "get_call_state_from_context", lambda _ctx: None)
        monkeypatch.setattr(tts_voice, "_make_transfer_to_staff_api_call", _mock_api_call)
        monkeypatch.setattr(tts_voice, "_transfer_twilio_call", _mock_twilio_call)

        result = await tts_voice._transfer_to_staff_voice_impl(
            ctx,
            summary="please transfer",
        )
        assert result == "Call transferred successfully."
        assert call_order == ["knock_api", "twilio_transfer"]
        assert ctx.context.call_ended_by_agent is True

    @pytest.mark.asyncio
    async def test_transfer_summary_requested_cleared_on_success(self, monkeypatch):
        """transfer_summary_requested is reset to False after successful transfer."""
        ctx = _voice_ctx()
        ctx.context.transfer_summary_requested = True
        monkeypatch.setattr(tts_voice, "get_call_state_from_context", lambda _ctx: None)
        monkeypatch.setattr(tts_voice, "_make_transfer_to_staff_api_call", AsyncMock())
        monkeypatch.setattr(tts_voice, "_transfer_twilio_call", AsyncMock())

        await tts_voice._transfer_to_staff_voice_impl(ctx, summary="help with rent")
        assert ctx.context.transfer_summary_requested is False

    @pytest.mark.asyncio
    async def test_transfer_summary_requested_cleared_on_cancellation(self, monkeypatch):
        """transfer_summary_requested is reset to False when the call is cancelled."""
        ctx = _voice_ctx()
        ctx.context.transfer_summary_requested = True
        monkeypatch.setattr(tts_voice, "get_call_state_from_context", lambda _ctx: None)
        monkeypatch.setattr(
            tts_voice,
            "_make_transfer_to_staff_api_call",
            AsyncMock(side_effect=asyncio.CancelledError),
        )
        monkeypatch.setattr(tts_voice, "_transfer_twilio_call", AsyncMock())

        result = await tts_voice._transfer_to_staff_voice_impl(ctx, summary="help")
        assert "cancelled" in result.lower()
        assert ctx.context.transfer_summary_requested is False

    @pytest.mark.asyncio
    async def test_exception_during_transfer_reraises(self, monkeypatch):
        """If an exception occurs during the transfer API calls, it should re-raise."""
        ctx = _voice_ctx()
        monkeypatch.setattr(tts_voice, "get_call_state_from_context", lambda _ctx: None)
        monkeypatch.setattr(
            tts_voice,
            "_make_transfer_to_staff_api_call",
            AsyncMock(side_effect=RuntimeError("network error")),
        )
        monkeypatch.setattr(tts_voice, "_transfer_twilio_call", AsyncMock())

        with pytest.raises(RuntimeError, match="network error"):
            await tts_voice._transfer_to_staff_voice_impl(
                ctx,
                summary="msg",
            )
        # Flag stays False when transfer crashes — stop event should still log call_hangup
        assert ctx.context.call_ended_by_agent is False


# ============================================================================
# 1b. transfer_to_staff_voice -- helper functions
# ============================================================================


class TestBuildTransferPayload:
    def test_builds_correct_payload(self):
        ctx = SimpleNamespace(
            context=SimpleNamespace(
                ask_request=SimpleNamespace(product_info=SimpleNamespace(resident_manager_id="mgr-789"))
            )
        )
        payload = tts_voice._build_transfer_payload(ctx, "water leak")
        assert payload == {
            "type": "note",
            "message": "Transfer to human agent - reason: water leak",
            "manager_id": "mgr-789",
        }


class TestBuildTransferTwiml:
    def test_builds_correct_twiml(self):
        twiml = tts_voice._build_transfer_twiml("https://example.com")
        assert "<Response>" in twiml
        assert '<Pause length="1"/>' in twiml
        assert "https://example.com/v1/relay/voice/clay/callback" in twiml
        assert "<Redirect" in twiml


class TestBuildUrl:
    def test_with_path_params_no_query(self):
        url = tts_voice._build_url(
            "https://api.test",
            "/v1/residents/{id}/activity",
            {"id": "42"},
        )
        assert url == "https://api.test/v1/residents/42/activity"

    def test_with_query_params(self):
        url = tts_voice._build_url(
            "https://api.test",
            "/v1/items",
            {},
            {"page": "1", "limit": "10"},
        )
        assert url == "https://api.test/v1/items?page=1&limit=10"

    def test_with_both(self):
        url = tts_voice._build_url(
            "https://api.test",
            "/v1/users/{user_id}",
            {"user_id": "5"},
            {"fields": "name"},
        )
        assert url == "https://api.test/v1/users/5?fields=name"


# ============================================================================
# 2. emergency_service_transfer_basic
# ============================================================================


class TestGetEmergencyServiceTransferBasicFxn:
    """Tests for get_emergency_service_transfer_basic_fxn (lines 59-61)."""

    def test_returns_function_tool_with_rendered_description(self):
        tool = estb_mod.get_emergency_service_transfer_basic_fxn(context=None)
        # When context is None, the description should be the raw template
        assert tool.description == estb_mod._DESCRIPTION_TEMPLATE

    def test_returns_copy_not_original(self):
        tool = estb_mod.get_emergency_service_transfer_basic_fxn(context=None)
        assert tool is not estb_mod.emergency_service_transfer_basic


class TestMakeApiCallBasic:
    """Tests for _make_api_call in emergency_service_transfer_basic (lines 278-298)."""

    @pytest.mark.asyncio
    async def test_success_json_response(self):
        response = _FakeResponse(
            status=200,
            body='{"result": "ok"}',
            json_data={"result": "ok"},
        )
        with _patch_aiohttp(estb_mod, response):
            result = await estb_mod._make_api_call(
                url="https://test/api",
                payload={},
                headers={},
                api_name="Test",
                method="GET",
            )
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_status_400_raises_runtime_error(self):
        response = _FakeResponse(status=400, body="Bad Request")
        with _patch_aiohttp(estb_mod, response):
            with pytest.raises(RuntimeError, match="Test API returned status 400"):
                await estb_mod._make_api_call(
                    url="https://test/api",
                    payload={},
                    headers={},
                    api_name="Test",
                    method="GET",
                )

    @pytest.mark.asyncio
    async def test_content_type_error_falls_back_to_json_loads(self):
        response = _FakeResponse(
            status=200,
            body='{"fallback": true}',
            raise_on_json=True,
        )
        with _patch_aiohttp(estb_mod, response):
            result = await estb_mod._make_api_call(
                url="https://test/api",
                payload={},
                headers={},
                api_name="Test",
                method="GET",
            )
        assert result == {"fallback": True}

    @pytest.mark.asyncio
    async def test_content_type_error_and_json_decode_error_raises(self):
        response = _FakeResponse(
            status=200,
            body="not json at all",
            raise_on_json=True,
        )
        with _patch_aiohttp(estb_mod, response):
            with pytest.raises(RuntimeError, match="non-JSON response"):
                await estb_mod._make_api_call(
                    url="https://test/api",
                    payload={},
                    headers={},
                    api_name="Test",
                    method="GET",
                )


class TestGetBooksIds:
    """Tests for get_books_ids (lines 141-146)."""

    @pytest.mark.asyncio
    async def test_calls_both_id_functions(self):
        with (
            patch.object(estb_mod, "get_company_id", new_callable=AsyncMock, return_value="c-1") as mock_company,
            patch.object(
                estb_mod,
                "get_property_id",
                new_callable=AsyncMock,
                return_value="p-2",
            ) as mock_property,
        ):
            company_id, property_id = await estb_mod.get_books_ids("uc-c", "uc-p")

        assert company_id == "c-1"
        assert property_id == "p-2"
        mock_company.assert_awaited_once_with("uc-c")
        mock_property.assert_awaited_once_with("uc-p")


class TestEmergencyServiceTransferBasicImplExceptionHandler:
    """Tests covering the except Exception handler (lines 116-119)."""

    @pytest.mark.asyncio
    async def test_exception_returns_error_message(self):
        ctx = SimpleNamespace(
            context=SimpleNamespace(
                call_ended_by_agent=False,
                call_management_in_progress=False,
                ask_request=SimpleNamespace(
                    product="resident_one_voice",
                    product_info=SimpleNamespace(
                        uc_company_id=SimpleNamespace(id="c-1"),
                        uc_property_id=SimpleNamespace(id="p-1"),
                        call_sid="CA1",
                        emerg_phone="+12025551234",
                    ),
                ),
                disabled_modules=[],
            ),
        )

        with patch.object(
            estb_mod,
            "get_books_ids",
            new_callable=AsyncMock,
            side_effect=RuntimeError("books down"),
        ):
            result = await estb_mod._emergency_service_transfer_basic_impl(
                ctx,
                already_created_emergency_service_request=True,
                service_request_summary="Test emergency",
            )

        assert "Failed to route emergency transfer request" in result
        assert ctx.context.call_ended_by_agent is True

    @pytest.mark.asyncio
    async def test_playback_not_completed_logs_warning_and_proceeds(self):
        """Cover lines 106-107: playback_result.completed is False."""
        call_state = AsyncMock()
        call_state.wait_for_message_playback = AsyncMock(
            return_value=PlaybackWaitResult(
                success=True,
                started=True,
                completed=False,
            )
        )
        ctx = SimpleNamespace(
            context=SimpleNamespace(
                call_ended_by_agent=False,
                call_management_in_progress=False,
                ask_request=SimpleNamespace(
                    product="resident_one_voice",
                    product_info=SimpleNamespace(
                        uc_company_id=SimpleNamespace(id="c-1"),
                        uc_property_id=SimpleNamespace(id="p-1"),
                        call_sid="CA1",
                        emerg_phone="+12025551234",
                    ),
                ),
                disabled_modules=[],
                call_state_manager=call_state,
            ),
        )

        with (
            patch.object(
                estb_mod,
                "get_books_ids",
                new_callable=AsyncMock,
                return_value=("c-1", "p-1"),
            ),
            patch.object(
                estb_mod,
                "get_call_state_from_context",
                return_value=call_state,
            ),
            patch.object(
                estb_mod,
                "redirect_to_number_via_twilio",
                new_callable=AsyncMock,
            ),
        ):
            result = await estb_mod._emergency_service_transfer_basic_impl(
                ctx,
                already_created_emergency_service_request=True,
                service_request_summary="Test emergency",
            )

        assert "+12025551234" in result


# ============================================================================
# 3. emergency_service_transfer_advanced -- _make_api_call
# ============================================================================


class TestMakeApiCallAdvanced:
    """Tests for _make_api_call in emergency_service_transfer_advanced (lines 208-235)."""

    @pytest.mark.asyncio
    async def test_success_json_response(self):
        response = _FakeResponse(
            status=200,
            body='{"status": 200}',
            json_data={"status": 200},
        )
        with _patch_aiohttp(esta_mod, response):
            result = await esta_mod._make_api_call(
                url="https://test/dispatch",
                payload={"key": "val"},
                headers={"Authorization": "Bearer tok"},
                api_name="Dispatch",
                method="POST",
            )
        assert result == {"status": 200}

    @pytest.mark.asyncio
    async def test_status_400_raises_runtime_error(self):
        response = _FakeResponse(status=500, body="Server Error")
        with _patch_aiohttp(esta_mod, response):
            with pytest.raises(RuntimeError, match="Dispatch API returned status 500"):
                await esta_mod._make_api_call(
                    url="https://test/dispatch",
                    payload={},
                    headers={},
                    api_name="Dispatch",
                    method="POST",
                )

    @pytest.mark.asyncio
    async def test_empty_body_returns_success_dict(self):
        response = _FakeResponse(status=200, body="")
        with _patch_aiohttp(esta_mod, response):
            result = await esta_mod._make_api_call(
                url="https://test/dispatch",
                payload={},
                headers={},
                api_name="Dispatch",
                method="POST",
            )
        assert result == {"success": True, "status": 200}

    @pytest.mark.asyncio
    async def test_whitespace_body_returns_success_dict(self):
        response = _FakeResponse(status=200, body="   ")
        with _patch_aiohttp(esta_mod, response):
            result = await esta_mod._make_api_call(
                url="https://test/dispatch",
                payload={},
                headers={},
                api_name="Dispatch",
                method="POST",
            )
        assert result == {"success": True, "status": 200}

    @pytest.mark.asyncio
    async def test_content_type_error_falls_back_to_json_loads(self):
        response = _FakeResponse(
            status=200,
            body='{"dispatched": true}',
            raise_on_json=True,
        )
        with _patch_aiohttp(esta_mod, response):
            result = await esta_mod._make_api_call(
                url="https://test/dispatch",
                payload={},
                headers={},
                api_name="Dispatch",
                method="POST",
            )
        assert result == {"dispatched": True}

    @pytest.mark.asyncio
    async def test_content_type_error_and_json_decode_error_raises(self):
        response = _FakeResponse(
            status=200,
            body="<html>not json</html>",
            raise_on_json=True,
        )
        with _patch_aiohttp(esta_mod, response):
            with pytest.raises(RuntimeError, match="non-JSON response"):
                await esta_mod._make_api_call(
                    url="https://test/dispatch",
                    payload={},
                    headers={},
                    api_name="Dispatch",
                    method="POST",
                )

    @pytest.mark.asyncio
    async def test_uses_default_get_method(self):
        """Verify the default method is GET when not specified."""
        captured = {}

        class _CapturingSession(_FakeSession):
            def request(self, **kwargs):
                captured.update(kwargs)
                return super().request(**kwargs)

        response = _FakeResponse(
            status=200,
            body='{"ok": true}',
            json_data={"ok": True},
        )
        session = _CapturingSession(response)

        with patch.object(
            esr_http_util.aiohttp,
            "ClientSession",
            side_effect=lambda *a, **kw: _FakeSessionCM(session),
        ):
            await esta_mod._make_api_call(
                url="https://test/api",
                payload={},
                headers={},
                api_name="Test",
            )
        assert captured["method"] == "GET"
