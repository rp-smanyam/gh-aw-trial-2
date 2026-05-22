from __future__ import annotations

import math

import audioop
import noisereduce as nr
import numpy as np
import structlog
from scipy.signal import resample_poly

logger = structlog.getLogger(__name__)

_ULAW_SAMPLE_RATE = 8000
_TARGET_SAMPLE_RATE = 24000


def _reduce_ulaw_noise(ulaw_bytes: bytes) -> bytes:
    """Apply noise reduction to mu-law audio and return mu-law bytes."""
    if not ulaw_bytes:
        return ulaw_bytes

    try:
        pcm16_bytes = audioop.ulaw2lin(ulaw_bytes, 2)
        pcm16 = np.frombuffer(pcm16_bytes, dtype=np.int16)
        if pcm16.size == 0:
            return ulaw_bytes

        audio_float = pcm16.astype(np.float32) / 32768.0
        gcd = math.gcd(_ULAW_SAMPLE_RATE, _TARGET_SAMPLE_RATE)
        up = _TARGET_SAMPLE_RATE // gcd
        down = _ULAW_SAMPLE_RATE // gcd
        audio_float = resample_poly(audio_float, up=up, down=down).astype(np.float32)

        reduced_noise = nr.reduce_noise(y=audio_float, sr=_TARGET_SAMPLE_RATE)
        reduced_noise = resample_poly(reduced_noise, up=down, down=up).astype(np.float32)
        reduced_noise = np.nan_to_num(reduced_noise, nan=0.0, posinf=1.0, neginf=-1.0)

        target_samples = len(ulaw_bytes)
        if reduced_noise.size < target_samples:
            reduced_noise = np.pad(reduced_noise, (0, target_samples - reduced_noise.size))
        elif reduced_noise.size > target_samples:
            reduced_noise = reduced_noise[:target_samples]

        reduced_noise = np.clip(reduced_noise, -1.0, 1.0)
        pcm16_out = (reduced_noise * 32767.0).astype(np.int16).tobytes()
        return audioop.lin2ulaw(pcm16_out, 2)
    except Exception as e:
        logger.warning("Noise reduction failed; sending original audio", error=str(e))
        return ulaw_bytes


def apply_noise_reduction(buffer_data: bytes, data_format: str) -> bytes:
    """Apply format-specific noise reduction and return audio bytes."""
    if data_format == "g711_ulaw":
        return _reduce_ulaw_noise(buffer_data)
    return buffer_data
