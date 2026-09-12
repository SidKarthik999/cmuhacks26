import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "audio-intelligence"))
sys.path.insert(0, str(ROOT / "signal-processing"))
sys.path.insert(0, str(ROOT / "shared" / "mocks"))

from cleaning import StreamingCleaner, clean_pcm, clean_signal_in_store
from fixtures import noisy
from mock_signal import mock_audio_bytes
from notes import extract_pitch_contour
from storage import SignalStore


def _room_tone(n, seed=1, amp=0.08):
    rng = np.random.default_rng(seed)
    return rng.standard_normal(n).astype(np.float32) * amp


def _tone(sr=16000, hz=440.0, seconds=0.8):
    t = np.arange(int(sr * seconds)) / sr
    return (0.3 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def _median_pitch(pcm, sr, expect_hz):
    pts = extract_pitch_contour(pcm, sr)
    voiced = [p["pitch_hz"] for p in pts if p["pitch_hz"] and p["confidence"] > 0.4]
    assert voiced, "expected voiced pitch"
    med = float(np.median(voiced))
    cents = 1200 * np.log2(med / expect_hz)
    return med, cents


def test_batch_clean_reduces_noise_floor():
    sr = 16000
    # Room-tone prefix so the noise PSD is not estimated from the held note.
    pad = _room_tone(int(0.25 * sr), seed=1)
    clean = _tone(sr)
    dirty = np.concatenate([pad, noisy(clean, snr_db=6.0, seed=2)])
    out = clean_pcm(dirty, sr)
    noise_before = float(np.mean(dirty[: pad.size] ** 2))
    noise_after = float(np.mean(out[: pad.size] ** 2))
    assert noise_after < noise_before * 0.9, (noise_before, noise_after)


def test_cleaning_does_not_destroy_pitch():
    sr = 16000
    pad = _room_tone(int(0.2 * sr), seed=4, amp=0.07)
    clean = _tone(sr, hz=220.0, seconds=1.0)
    dirty = np.concatenate([pad, noisy(clean, snr_db=8.0, seed=3)])
    out = clean_pcm(dirty, sr)[pad.size :]
    _med, cents = _median_pitch(out, sr, 220.0)
    assert abs(cents) < 40, cents


def test_streaming_cleaner_emits_within_budget():
    sr = 16000
    cleaner = StreamingCleaner(sample_rate=sr)
    pcm = _tone(sr, seconds=1.0)
    hop = int(0.02 * sr)  # 20 ms chunks
    emitted = 0
    for i in range(0, pcm.size, hop):
        y = cleaner.push(pcm[i : i + hop])
        emitted += y.size
    # Algorithmic delay is one STFT window (~32ms at 16k) — well under 1–2s.
    assert emitted > sr * 0.5
    lag_samples = pcm.size - emitted
    lag_s = lag_samples / sr
    assert lag_s < 0.08


def test_auto_clean_on_stored_signal(tmp_path):
    audio = mock_audio_bytes(duration_ms=400, frequency_hz=330.0)
    with SignalStore(root=tmp_path / "signals") as store:
        signal = store.save(
            participant_id="p1",
            room_id="r1",
            start_time=0.0,
            end_time=400.0,
            sample_rate=44100,
            audio_bytes=audio,
        )
        updated = clean_signal_in_store(store, signal.id)
        assert updated["metadata"]["cleaned"] is True
        sidecar = tmp_path / "signals" / updated["metadata"]["cleaned_audio_ref"]
        assert sidecar.exists()
        assert sidecar.stat().st_size > 44
        # Original blob untouched (Task 3 replay stays bit-faithful).
        assert store.get_audio(signal.id) == audio
