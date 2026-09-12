"""Task 7 — noise cleaning, batch and streaming.

Spectral subtraction + a light noise gate. Runs automatically on every
extracted Signal (see `clean_signal_in_store`). Streaming variant keeps a
running noise PSD so Practice/Performance can stay inside <1–2s.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "audio-intelligence"))
sys.path.insert(0, str(_ROOT / "signal-processing"))

from audio_io import pcm_to_float32, read_wav, rms, write_wav_bytes  # noqa: E402

N_FFT = 512
HOP = 128
N_NOISE_FRAMES = 8


def _stft(x: np.ndarray, n_fft: int = N_FFT, hop: int = HOP) -> np.ndarray:
    window = np.hanning(n_fft).astype(np.float32)
    if x.size < n_fft:
        x = np.pad(x, (0, n_fft - x.size))
    frames = 1 + (x.size - n_fft) // hop
    spec = np.empty((frames, n_fft // 2 + 1), dtype=np.complex64)
    for i in range(frames):
        sl = x[i * hop : i * hop + n_fft]
        spec[i] = np.fft.rfft(sl * window)
    return spec


def _istft(spec: np.ndarray, n_fft: int = N_FFT, hop: int = HOP) -> np.ndarray:
    window = np.hanning(n_fft).astype(np.float32)
    frames = spec.shape[0]
    out_len = n_fft + (frames - 1) * hop
    acc = np.zeros(out_len, dtype=np.float32)
    wsum = np.zeros(out_len, dtype=np.float32)
    for i in range(frames):
        frame = np.fft.irfft(spec[i], n=n_fft).astype(np.float32) * window
        start = i * hop
        acc[start : start + n_fft] += frame
        wsum[start : start + n_fft] += window * window
    wsum = np.maximum(wsum, 1e-8)
    return acc / wsum


def _estimate_noise_psd(mag: np.ndarray, pcm: Optional[np.ndarray] = None) -> np.ndarray:
    """Prefer low-energy (time-domain) frames. If the buffer is all voice,
    fall back to a small broadband floor so we do not subtract the note."""
    n_frames = mag.shape[0]
    if pcm is not None and pcm.size >= N_FFT:
        energies = np.empty(n_frames, dtype=np.float64)
        for i in range(n_frames):
            sl = pcm[i * HOP : i * HOP + N_FFT]
            energies[i] = float(np.sqrt(np.mean(sl * sl) + 1e-12))
    else:
        energies = mag.mean(axis=1).astype(np.float64)
    quiet_cut = float(np.percentile(energies, 15))
    med = float(np.median(energies) + 1e-12)
    quiet = mag[energies <= max(quiet_cut, 1e-8)]
    if quiet.shape[0] >= 2 and quiet_cut < 0.5 * med:
        return np.median(quiet**2, axis=0)
    n = min(N_NOISE_FRAMES, n_frames)
    if float(np.mean(energies[:n])) < 0.5 * med:
        return np.median(mag[:n] ** 2, axis=0)
    return np.median(mag**2, axis=0) * 0.02


def spectral_subtract(
    pcm: np.ndarray,
    noise_psd: Optional[np.ndarray] = None,
    oversub: float = 2.0,
    floor: float = 0.02,
) -> Tuple[np.ndarray, np.ndarray]:
    x = pcm_to_float32(pcm)
    spec = _stft(x)
    mag = np.abs(spec)
    phase = np.angle(spec)
    if noise_psd is None:
        noise_psd = _estimate_noise_psd(mag, x)
    else:
        instant = _estimate_noise_psd(mag, x)
        noise_psd = 0.85 * noise_psd + 0.15 * np.minimum(noise_psd, instant)
    clean_mag = mag**2 - oversub * noise_psd
    clean_mag = np.maximum(clean_mag, floor * (noise_psd + 1e-12))
    clean_mag = np.sqrt(np.maximum(clean_mag, 0.0))
    cleaned = _istft(clean_mag * np.exp(1j * phase))
    n = min(cleaned.size, x.size)
    out = np.zeros_like(x)
    out[:n] = np.clip(cleaned[:n], -1.0, 1.0)
    return out, noise_psd


def clean_pcm(pcm: np.ndarray, sample_rate: int) -> np.ndarray:
    """Batch clean a complete buffer. `sample_rate` kept for the public signature."""
    del sample_rate
    cleaned, _ = spectral_subtract(pcm)
    return cleaned


def clean_wav_bytes(wav_bytes: bytes) -> Tuple[bytes, int]:
    pcm, sr = read_wav(wav_bytes)
    cleaned = clean_pcm(pcm, sr)
    return write_wav_bytes(cleaned, sr), sr


class StreamingCleaner:
    """Incremental spectral subtraction over a rolling PCM buffer."""

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._buf = np.zeros(0, dtype=np.float32)
        self._noise_psd: Optional[np.ndarray] = None
        self.latency_hop = HOP

    def push(self, pcm: np.ndarray) -> np.ndarray:
        x = pcm_to_float32(pcm)
        self._buf = np.concatenate([self._buf, x])
        # Need at least one FFT window.
        if self._buf.size < N_FFT:
            return np.zeros(0, dtype=np.float32)
        usable = N_FFT + ((self._buf.size - N_FFT) // HOP) * HOP
        chunk = self._buf[:usable]
        cleaned, self._noise_psd = spectral_subtract(chunk, self._noise_psd)
        # Keep overlap for the next hop.
        keep = N_FFT - HOP
        self._buf = self._buf[usable - keep :]
        # Drop the overlap we already emitted previously.
        emit = cleaned[keep:] if cleaned.size > keep else cleaned
        return emit.astype(np.float32)


def clean_signal_in_store(store: Any, signal_id: str) -> Dict[str, Any]:
    """Auto-run Task 7 on an extracted Signal. Writes a sidecar WAV and sets
    metadata.cleaned = true without rewriting Person C's original blob."""
    signal = store.get(signal_id)
    raw = store.get_audio(signal_id)
    cleaned_bytes, _sr = clean_wav_bytes(raw)
    sidecar = store.blob_dir / f"{signal_id}.cleaned.{signal.audio_format}"
    sidecar.write_bytes(cleaned_bytes)
    rel = str(sidecar.relative_to(store.root))
    updated = store.update_metadata(
        signal_id,
        cleaned=True,
        cleaned_audio_ref=rel,
    )
    return updated.to_dict()
