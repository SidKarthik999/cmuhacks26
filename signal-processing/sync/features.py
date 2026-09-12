"""Audio decoding + chroma feature extraction shared by the batch and
streaming DTW aligners.
"""
from __future__ import annotations

import io
from typing import Tuple

import librosa
import numpy as np
import soundfile as sf


def load_audio_mono(audio_bytes: bytes) -> Tuple[np.ndarray, int]:
    """Decode WAV/FLAC bytes into a mono float32 waveform + its sample rate."""
    data, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data, sr


def to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    """Encode a float32 waveform as 16-bit PCM WAV bytes."""
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def chroma_features(
    y: np.ndarray, sr: int, hop_length: int = 2048
) -> Tuple[np.ndarray, float]:
    """Chroma-CQT features: robust to timbre differences (e.g. two different
    voices/instruments singing the same melody), which is why Task 4 uses
    chroma rather than raw pitch for the DTW cost function.

    Returns (chroma of shape (12, n_frames), hop_length_ms).
    """
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)
    hop_length_ms = hop_length / sr * 1000.0
    return chroma, hop_length_ms


def resample_to_match(y: np.ndarray, sr: int, target_sr: int) -> np.ndarray:
    if sr == target_sr:
        return y
    return librosa.resample(y, orig_sr=sr, target_sr=target_sr)
