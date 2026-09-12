"""Synthetic singing vs speech fixtures for Task 2 tests and the demo."""
from __future__ import annotations

import numpy as np


def singing_scale(
    sample_rate: int = 16000,
    note_hz=(261.63, 293.66, 329.63, 349.23, 392.00, 440.00, 493.88, 523.25),
    note_ms: float = 350.0,
    vibrato_hz: float = 5.0,
    vibrato_cents: float = 18.0,
) -> np.ndarray:
    """Sustained sung scale: stable F0 + light vibrato (should detect as singing)."""
    parts = []
    t_note = int(sample_rate * note_ms / 1000.0)
    fade = int(0.02 * sample_rate)
    for hz in note_hz:
        t = np.arange(t_note) / sample_rate
        cents = (vibrato_cents / 1200.0) * np.sin(2 * np.pi * vibrato_hz * t)
        inst = hz * (2.0 ** cents)
        phase = np.cumsum(2 * np.pi * inst / sample_rate)
        tone = 0.28 * np.sin(phase)
        # Odd harmonics: more voice-like than a pure sine.
        tone += 0.10 * np.sin(2 * phase) + 0.05 * np.sin(3 * phase)
        if fade > 0:
            env = np.ones_like(tone)
            env[:fade] = np.linspace(0, 1, fade)
            env[-fade:] = np.linspace(1, 0, fade)
            tone *= env
        parts.append(tone.astype(np.float32))
    return np.concatenate(parts)


def conversational_speech(sample_rate: int = 16000, duration_s: float = 2.8) -> np.ndarray:
    """Erratic F0 + noise bursts approximating talk, not song."""
    n = int(sample_rate * duration_s)
    rng = np.random.default_rng(4)
    t = np.arange(n) / sample_rate
    # Jump F0 every ~60–90 ms like syllables, not held notes.
    hop = int(0.07 * sample_rate)
    f0 = np.zeros(n, dtype=np.float32)
    pos = 0
    while pos < n:
        slen = hop + int(rng.integers(-800, 800))
        slen = max(int(0.04 * sample_rate), min(slen, n - pos))
        f0[pos : pos + slen] = float(rng.uniform(110, 240))
        pos += slen
    phase = np.cumsum(2 * np.pi * f0 / sample_rate)
    voiced = 0.12 * np.sin(phase)
    # Rapid amplitude modulation (syllable-like).
    syll = 0.5 + 0.5 * np.sin(2 * np.pi * 7.5 * t)
    bursts = (rng.random(n) > 0.35).astype(np.float32)
    # Smooth bursts a bit
    kernel = np.ones(int(0.015 * sample_rate), dtype=np.float32)
    kernel /= kernel.size
    gate = np.convolve(bursts, kernel, mode="same")
    noise = 0.04 * rng.standard_normal(n).astype(np.float32)
    speech = (voiced * syll * gate + noise).astype(np.float32)
    return np.clip(speech, -1.0, 1.0)


def noisy(pcm: np.ndarray, snr_db: float = 8.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = np.asarray(pcm, dtype=np.float32)
    power = np.mean(x * x) + 1e-12
    noise_power = power / (10.0 ** (snr_db / 10.0))
    noise = rng.standard_normal(x.size).astype(np.float32) * np.sqrt(noise_power)
    return np.clip(x + noise, -1.0, 1.0)
