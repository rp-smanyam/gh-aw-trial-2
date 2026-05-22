"""Tests for agent_leasing.util.audio_noise_reduction."""

from __future__ import annotations

from unittest.mock import patch

import audioop
import numpy as np
import pytest

from agent_leasing.util.audio_noise_reduction import (
    _reduce_ulaw_noise,
    apply_noise_reduction,
)


def _make_ulaw_bytes(num_samples: int = 8000, freq: float = 440.0) -> bytes:
    """Generate realistic mu-law encoded audio bytes from a sine wave.

    Returns mu-law bytes of length ``num_samples``.
    """
    t = np.linspace(0, num_samples / 8000, num_samples, endpoint=False)
    samples = np.sin(2 * np.pi * freq * t).astype(np.float32)
    pcm16 = (samples * 32767).astype(np.int16).tobytes()
    return audioop.lin2ulaw(pcm16, 2)


# ---------------------------------------------------------------------------
# _reduce_ulaw_noise
# ---------------------------------------------------------------------------
class TestReduceUlawNoise:
    def test_empty_bytes_returns_unchanged(self):
        """Empty input should be returned as-is."""
        result = _reduce_ulaw_noise(b"")
        assert result == b""

    def test_valid_ulaw_returns_same_length(self):
        """Output should have the same number of bytes as input."""
        ulaw = _make_ulaw_bytes(num_samples=8000)
        result = _reduce_ulaw_noise(ulaw)
        assert isinstance(result, bytes)
        assert len(result) == len(ulaw)

    def test_output_is_valid_ulaw(self):
        """Output should be decodable as mu-law audio."""
        ulaw = _make_ulaw_bytes(num_samples=4000)
        result = _reduce_ulaw_noise(ulaw)
        # Should not raise — valid mu-law can be decoded back to linear PCM
        pcm = audioop.ulaw2lin(result, 2)
        assert len(pcm) == len(result) * 2  # 2 bytes per 16-bit sample

    def test_short_input(self):
        """Even very short audio should process without error."""
        ulaw = _make_ulaw_bytes(num_samples=100)
        result = _reduce_ulaw_noise(ulaw)
        assert isinstance(result, bytes)
        assert len(result) == len(ulaw)

    def test_exception_falls_back_to_original(self):
        """If noise reduction fails, original bytes are returned."""
        ulaw = _make_ulaw_bytes(num_samples=2000)
        with patch(
            "agent_leasing.util.audio_noise_reduction.nr.reduce_noise",
            side_effect=RuntimeError("boom"),
        ):
            result = _reduce_ulaw_noise(ulaw)
        assert result == ulaw

    def test_exception_logs_warning(self):
        """Failure path should log a warning."""
        ulaw = _make_ulaw_bytes(num_samples=2000)
        with (
            patch(
                "agent_leasing.util.audio_noise_reduction.nr.reduce_noise",
                side_effect=RuntimeError("boom"),
            ),
            patch("agent_leasing.util.audio_noise_reduction.logger") as mock_logger,
        ):
            _reduce_ulaw_noise(ulaw)
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert "Noise reduction failed" in call_args[0][0]

    def test_nan_handling(self):
        """NaN values in noise-reduced output should be replaced, not crash."""
        ulaw = _make_ulaw_bytes(num_samples=4000)

        def fake_reduce_noise(y, sr):
            # Return array with NaN values
            result = np.full_like(y, np.nan)
            return result

        with patch(
            "agent_leasing.util.audio_noise_reduction.nr.reduce_noise",
            side_effect=fake_reduce_noise,
        ):
            result = _reduce_ulaw_noise(ulaw)

        assert isinstance(result, bytes)
        assert len(result) == len(ulaw)


# ---------------------------------------------------------------------------
# apply_noise_reduction
# ---------------------------------------------------------------------------
class TestApplyNoiseReduction:
    def test_g711_ulaw_delegates_to_reduce_ulaw_noise(self):
        """g711_ulaw format should call _reduce_ulaw_noise."""
        ulaw = _make_ulaw_bytes(num_samples=1000)
        with patch(
            "agent_leasing.util.audio_noise_reduction._reduce_ulaw_noise",
            return_value=b"processed",
        ) as mock_reduce:
            result = apply_noise_reduction(ulaw, "g711_ulaw")
        mock_reduce.assert_called_once_with(ulaw)
        assert result == b"processed"

    def test_unknown_format_returns_data_unchanged(self):
        """Non-ulaw formats should return data as-is."""
        data = b"some-pcm-data"
        result = apply_noise_reduction(data, "pcm16")
        assert result == data

    @pytest.mark.parametrize(
        "data_format",
        ["pcm16", "g711_alaw", "opus", "mp3", "", "G711_ULAW"],
    )
    def test_non_ulaw_formats_passthrough(self, data_format):
        """Various non-matching format strings should all pass through."""
        data = b"\x00\x01\x02"
        result = apply_noise_reduction(data, data_format)
        assert result is data

    def test_g711_ulaw_with_empty_bytes(self):
        """g711_ulaw with empty buffer should still work (delegates to _reduce_ulaw_noise)."""
        result = apply_noise_reduction(b"", "g711_ulaw")
        assert result == b""
