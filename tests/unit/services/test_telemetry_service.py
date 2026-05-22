from unittest.mock import MagicMock, patch

import pytest

from agent_leasing.services.telemetry_service import emit_metrics


class TestEmitMetrics:
    """Test emit_metrics function."""

    @pytest.mark.asyncio
    async def test_emit_metrics_with_responses(self):
        """Test emit_metrics with raw responses."""
        result = MagicMock()

        # Create mock responses with usage information
        response1 = MagicMock()
        response1.usage.input_tokens = 100
        response1.usage.output_tokens = 50

        response2 = MagicMock()
        response2.usage.input_tokens = 75
        response2.usage.output_tokens = 25

        result.raw_responses = [response1, response2]

        with (
            patch("agent_leasing.services.telemetry_service.input_token_counter") as mock_input_counter,
            patch("agent_leasing.services.telemetry_service.output_token_counter") as mock_output_counter,
        ):
            await emit_metrics(result, "session_123")

            # Verify input tokens are summed and recorded
            mock_input_counter.add.assert_called_once_with(175, {"agent_leasing.session_id": "session_123"})
            # Verify output tokens are summed and recorded
            mock_output_counter.add.assert_called_once_with(75, {"agent_leasing.session_id": "session_123"})

    @pytest.mark.asyncio
    async def test_emit_metrics_with_no_responses(self):
        """Test emit_metrics when raw_responses is None."""
        result = MagicMock()
        result.raw_responses = None

        with (
            patch("agent_leasing.services.telemetry_service.input_token_counter") as mock_input_counter,
            patch("agent_leasing.services.telemetry_service.output_token_counter") as mock_output_counter,
        ):
            await emit_metrics(result, "session_empty")

            # Verify zero tokens are recorded
            mock_input_counter.add.assert_called_once_with(0, {"agent_leasing.session_id": "session_empty"})
            mock_output_counter.add.assert_called_once_with(0, {"agent_leasing.session_id": "session_empty"})

    @pytest.mark.asyncio
    async def test_emit_metrics_with_empty_responses(self):
        """Test emit_metrics when raw_responses is an empty list."""
        result = MagicMock()
        result.raw_responses = []

        with (
            patch("agent_leasing.services.telemetry_service.input_token_counter") as mock_input_counter,
            patch("agent_leasing.services.telemetry_service.output_token_counter") as mock_output_counter,
        ):
            await emit_metrics(result, "session_empty_list")

            # Verify zero tokens are recorded
            mock_input_counter.add.assert_called_once_with(0, {"agent_leasing.session_id": "session_empty_list"})
            mock_output_counter.add.assert_called_once_with(0, {"agent_leasing.session_id": "session_empty_list"})

    @pytest.mark.asyncio
    async def test_emit_metrics_with_single_response(self):
        """Test emit_metrics with a single response."""
        result = MagicMock()

        response = MagicMock()
        response.usage.input_tokens = 200
        response.usage.output_tokens = 150

        result.raw_responses = [response]

        with (
            patch("agent_leasing.services.telemetry_service.input_token_counter") as mock_input_counter,
            patch("agent_leasing.services.telemetry_service.output_token_counter") as mock_output_counter,
        ):
            await emit_metrics(result, "session_single")

            mock_input_counter.add.assert_called_once_with(200, {"agent_leasing.session_id": "session_single"})
            mock_output_counter.add.assert_called_once_with(150, {"agent_leasing.session_id": "session_single"})

    @pytest.mark.asyncio
    async def test_emit_metrics_with_zero_tokens(self):
        """Test emit_metrics when responses have zero tokens."""
        result = MagicMock()

        response = MagicMock()
        response.usage.input_tokens = 0
        response.usage.output_tokens = 0

        result.raw_responses = [response]

        with (
            patch("agent_leasing.services.telemetry_service.input_token_counter") as mock_input_counter,
            patch("agent_leasing.services.telemetry_service.output_token_counter") as mock_output_counter,
        ):
            await emit_metrics(result, "session_zero")

            mock_input_counter.add.assert_called_once_with(0, {"agent_leasing.session_id": "session_zero"})
            mock_output_counter.add.assert_called_once_with(0, {"agent_leasing.session_id": "session_zero"})

    @pytest.mark.asyncio
    async def test_emit_metrics_with_large_token_counts(self):
        """Test emit_metrics with large token counts."""
        result = MagicMock()

        response1 = MagicMock()
        response1.usage.input_tokens = 10000
        response1.usage.output_tokens = 5000

        response2 = MagicMock()
        response2.usage.input_tokens = 15000
        response2.usage.output_tokens = 7500

        result.raw_responses = [response1, response2]

        with (
            patch("agent_leasing.services.telemetry_service.input_token_counter") as mock_input_counter,
            patch("agent_leasing.services.telemetry_service.output_token_counter") as mock_output_counter,
        ):
            await emit_metrics(result, "session_large")

            mock_input_counter.add.assert_called_once_with(25000, {"agent_leasing.session_id": "session_large"})
            mock_output_counter.add.assert_called_once_with(12500, {"agent_leasing.session_id": "session_large"})

    @pytest.mark.asyncio
    async def test_emit_metrics_logs_debug_message(self):
        """Test that emit_metrics logs a debug message."""
        result = MagicMock()

        response = MagicMock()
        response.usage.input_tokens = 50
        response.usage.output_tokens = 30

        result.raw_responses = [response]

        with (
            patch("agent_leasing.services.telemetry_service.logger") as mock_logger,
            patch("agent_leasing.services.telemetry_service.input_token_counter"),
            patch("agent_leasing.services.telemetry_service.output_token_counter"),
        ):
            await emit_metrics(result, "session_log")

            # Verify debug message was logged
            mock_logger.debug.assert_called_once_with("Input tokens: 50. Output tokens: 30")

    @pytest.mark.asyncio
    async def test_emit_metrics_with_different_session_ids(self):
        """Test emit_metrics with different session IDs."""
        result = MagicMock()

        response = MagicMock()
        response.usage.input_tokens = 100
        response.usage.output_tokens = 50

        result.raw_responses = [response]

        session_ids = ["session_a", "session_b", "session_xyz_123"]

        for session_id in session_ids:
            with (
                patch("agent_leasing.services.telemetry_service.input_token_counter") as mock_input_counter,
                patch("agent_leasing.services.telemetry_service.output_token_counter") as mock_output_counter,
            ):
                await emit_metrics(result, session_id)

                # Verify session ID is passed correctly
                mock_input_counter.add.assert_called_once_with(100, {"agent_leasing.session_id": session_id})
                mock_output_counter.add.assert_called_once_with(50, {"agent_leasing.session_id": session_id})

    @pytest.mark.asyncio
    async def test_emit_metrics_multiple_responses_accumulation(self):
        """Test that token counts accumulate correctly across multiple responses."""
        result = MagicMock()

        # Create 5 responses with varying token counts
        responses = []
        for i in range(1, 6):
            response = MagicMock()
            response.usage.input_tokens = i * 10
            response.usage.output_tokens = i * 5
            responses.append(response)

        result.raw_responses = responses

        with (
            patch("agent_leasing.services.telemetry_service.input_token_counter") as mock_input_counter,
            patch("agent_leasing.services.telemetry_service.output_token_counter") as mock_output_counter,
        ):
            await emit_metrics(result, "session_multi")

            # Expected: 10+20+30+40+50 = 150 input tokens
            # Expected: 5+10+15+20+25 = 75 output tokens
            mock_input_counter.add.assert_called_once_with(150, {"agent_leasing.session_id": "session_multi"})
            mock_output_counter.add.assert_called_once_with(75, {"agent_leasing.session_id": "session_multi"})
