"""Noise reduction wrapper — delegates to the existing utility.

Kept as a thin wrapper so the voice package does not import the
heavy numpy/scipy/noisereduce stack at module level.
"""

from __future__ import annotations


def apply_noise_reduction(audio: bytes, audio_format: str) -> bytes:
    """Apply format-specific noise reduction and return processed bytes.

    Lazy-imports the utility to avoid loading numpy at import time.
    """
    from agent_leasing.util.audio_noise_reduction import (
        apply_noise_reduction as _apply,
    )

    return _apply(audio, audio_format)
