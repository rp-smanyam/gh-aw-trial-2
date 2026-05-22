from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cashews import cache

from agent_leasing.util.memory import setup_cache

# Initialize cache once for all tests
setup_cache()


@pytest.fixture(autouse=True)
async def _fresh_openai_client():
    """Reinitialize the OpenAI client so it's bound to the current event loop.

    pytest-asyncio creates a new event loop per test (function scope). The
    module-level AsyncOpenAI client holds httpx connections pinned to the loop
    it was first used on. On later tests those stale connections try to close
    on a dead loop → ``RuntimeError: Event loop is closed`` → APIConnectionError.

    Resetting the singleton here forces a fresh httpx transport on every test.
    """
    import agent_leasing.clients.openai as openai_mod

    openai_mod._openai_client = None
    openai_mod.initialize_openai_client()
    yield


@pytest.fixture(autouse=True)
async def clear_cache():
    yield
    await cache.clear()


@pytest.fixture(autouse=True)
def mock_end_call():
    """Mock the end_call function to prevent actual Twilio API calls during integration tests."""
    with patch("agent_leasing.agent.tools.end_call.end_call.end_call") as mock_end_call_func:
        # Create a mock that returns a success message
        mock_end_call_func.return_value = "Call ended successfully. Status: completed"

        # Also mock the Twilio client to prevent any API calls.
        # The real twilio.rest.Client is synchronous (`client.calls(sid).update(...)`),
        # so the mock must be MagicMock (not AsyncMock) — AsyncMock makes `.calls(sid)`
        # return a coroutine, which then fails with `'coroutine' object has no attribute 'update'`.
        with patch("agent_leasing.agent.tools.end_call.end_call.TwilioClient") as mock_twilio_client:
            mock_client = MagicMock()
            mock_call = MagicMock()
            mock_call.status = "completed"
            mock_call.update.return_value = mock_call
            mock_client.calls.return_value = mock_call
            mock_twilio_client.return_value = mock_client

            yield {
                "end_call_func": mock_end_call_func,
                "twilio_client": mock_twilio_client,
                "mock_call": mock_call,
            }


@pytest.fixture(autouse=True)
def mock_transfer_to_staff_voice():
    """Mock the transfer_to_staff function to prevent actual API calls and Twilio calls during integration tests."""
    with patch(
        "agent_leasing.agent.tools.transfer_to_staff.transfer_to_staff_voice.transfer_to_staff_voice"
    ) as mock_transfer_func:
        # Create a mock that returns a success message
        mock_transfer_func.return_value = "Call transferred successfully."

        # Mock the internal API call
        with patch(
            "agent_leasing.agent.tools.transfer_to_staff.transfer_to_staff_voice._make_transfer_to_staff_api_call"
        ) as mock_api_call:
            mock_api_call.return_value = None

            # Mock the Twilio transfer call
            with patch(
                "agent_leasing.agent.tools.transfer_to_staff.transfer_to_staff_voice._transfer_twilio_call"
            ) as mock_twilio_transfer:
                mock_twilio_transfer.return_value = AsyncMock()

                # Also mock the Twilio client to prevent any API calls
                with patch(
                    "agent_leasing.agent.tools.transfer_to_staff.transfer_to_staff_voice.TwilioClient"
                ) as mock_twilio_client:
                    mock_client = AsyncMock()
                    mock_call = AsyncMock()
                    mock_call.status = "completed"
                    mock_call.update.return_value = mock_call
                    mock_client.calls.return_value = mock_call
                    mock_twilio_client.return_value = mock_client

                    yield {
                        "transfer_func": mock_transfer_func,
                        "api_call": mock_api_call,
                        "twilio_transfer": mock_twilio_transfer,
                        "twilio_client": mock_twilio_client,
                        "mock_call": mock_call,
                    }


@pytest.fixture(autouse=True)
def mock_facilities_api():
    """Prevent outbound Facilities API calls during integration tests and return context-aware responses."""

    async def fake_perform_api_call(*args, **kwargs):
        payload = kwargs.get("payload") or {}
        message = (payload.get("relevant_context_from_last_user_message") or "").lower()

        if "open service" in message or "status" in message:
            return {
                "self_service_available": False,
                "service_request_numbers": ["12345", "67890"],
                "instructions": (
                    "Here are your open service requests:\n\n"
                    "- SR-12345: Kitchen faucet leak (Created Jan 15, 2025) — In Progress\n"
                    "  - Summary: Leaking kitchen faucet reported by resident. Technician notes: Scheduled for repair on Jan 18.\n\n"
                    "- SR-67890: Air conditioning not cooling (Created Jan 10, 2025) — Pending\n"
                    "  - Summary: AC unit not cooling properly. Technician notes: None provided."
                ),
            }

        if "create" in message or "leak" in message or "door" in message:
            return {
                "self_service_available": False,
                "service_request_numbers": ["77777"],
                "instructions": (
                    "Your service request SR-77777 was created for the reported issue. "
                    "Please note there may be a short delay before it appears in the portal."
                ),
            }

        return {
            "self_service_available": True,
            "service_request_numbers": None,
            "instructions": "Please share more details about your maintenance request.",
        }

    with patch(
        "agent_leasing.agent.tools.api_call.api_call.perform_api_call",
        new=fake_perform_api_call,
    ) as mock_call:
        yield mock_call
