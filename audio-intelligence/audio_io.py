"""WAV / PCM helpers used across detection, cleaning, and notes."""
from __future__ import annotations

import io
import wave
from typing import Tuple

import numpy as np


def pcm_to_float32(pcm: np.ndarray, sample_width_bytes: int = 2) -> np.ndarray:
    x = np.asarray(pcm)
    if x.dtype == np.float32 or x.dtype == np.float64:
        return x.astype(np.float32)
    if sample_width_bytes == 2:
        return (x.astype(np.float32) / 32768.0).clip(-1.0, 1.0)
    raise ValueError(f"unsupported sample width {sample_width_bytes}")


def float32_to_int16(x: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(x, dtype=np.float32) * 32767.0, -32767, 32767).astype(
        np.int16
    )


def read_wav(path_or_bytes) -> Tuple[np.ndarray, int]:
    """Return mono float32 PCM and sample rate."""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        src = io.BytesIO(path_or_bytes)
    else:
        src = str(path_or_bytes)
    with wave.open(src, "rb") as wf:
        sr = wf.getframerate()
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if sw != 2:
        raise ValueError("only 16-bit PCM WAV is supported")
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if nch > 1:
        pcm = pcm.reshape(-1, nch).mean(axis=1)
    return pcm, sr


def write_wav_bytes(pcm: np.ndarray, sample_rate: int) -> bytes:
    audio = float32_to_int16(pcm)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio.tobytes())
    return buf.getvalue()


def rms(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=np.float32)
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(x * x) + 1e-12))
