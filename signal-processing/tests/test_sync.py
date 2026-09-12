import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jsonschema
import json
import numpy as np
import pytest
import librosa

from sync import AlignmentResult, align_audio, align_signals, render_aligned_playback, StreamingAligner
from sync.features import to_wav_bytes, load_audio_mono
from storage import SignalStore

SAMPLE_RATE = 22050

_ALIGNMENT_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "shared" / "schemas" / "alignment_result.schema.json").read_text()
)

# A short melody (distinct pitches per segment) so chroma actually varies
# frame-to-frame -- a single sustained tone would make every frame's chroma
# identical and DTW's alignment ambiguous.
_MELODY_HZ = [261.63, 293.66, 329.63, 349.23, 392.00]  # C D E F G
# A second, harmonically unrelated melody: pitch classes chosen to sit
# roughly a semitone away from _MELODY_HZ's (C# D# F# G# A#), so chroma
# vectors have little bin overlap -- stands in for "two people singing
# different material," not just a different tune built on the same notes.
_UNRELATED_MELODY_HZ = [277.18, 311.13, 369.99, 415.30, 466.16]
_NOTE_MS = 300


def _melody_wave(
    sample_rate: int = SAMPLE_RATE, note_ms: int = _NOTE_MS, hz_list=None
) -> np.ndarray:
    hz_list = hz_list or _MELODY_HZ
    segments = []
    for hz in hz_list:
        n = int(sample_rate * note_ms / 1000)
        t = np.arange(n) / sample_rate
        segments.append(0.5 * np.sin(2 * np.pi * hz * t).astype(np.float32))
    return np.concatenate(segments)


def _melody_wav_bytes(sample_rate: int = SAMPLE_RATE, note_ms: int = _NOTE_MS, hz_list=None) -> bytes:
    return to_wav_bytes(_melody_wave(sample_rate, note_ms, hz_list), sample_rate)


def _silence(ms: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    return np.zeros(int(sample_rate * ms / 1000), dtype=np.float32)


def test_identical_signals_align_with_near_zero_offset_and_high_confidence():
    melody = _melody_wav_bytes()
    warp_path, confidence, hop_ms = align_audio(melody, melody)

    assert confidence > 0.9

    offsets_ms = [(b - a) * hop_ms for a, b in warp_path]
    assert max(abs(o) for o in offsets_ms) < hop_ms * 2  # within ~1 frame of zero


def test_constant_offset_is_recovered_by_warp_path():
    melody = _melody_wave()
    offset_ms = 500.0
    shifted = np.concatenate([_silence(offset_ms), melody])

    ref_bytes = to_wav_bytes(melody, SAMPLE_RATE)
    other_bytes = to_wav_bytes(shifted, SAMPLE_RATE)

    warp_path, confidence, hop_ms = align_audio(ref_bytes, other_bytes)

    # Sample the offset in the middle of the path (avoid edge effects) and
    # check it's close to the true injected 500ms shift.
    mid = warp_path[len(warp_path) // 2]
    recovered_offset_ms = (mid[1] - mid[0]) * hop_ms
    assert abs(recovered_offset_ms - offset_ms) < hop_ms * 2


def test_tempo_drift_produces_non_constant_warp_not_just_a_shift():
    """Resolved design requirement: a signal that gradually drifts tempo
    relative to the other must still track alignment throughout, not just
    at the start -- this only works with a full warp path, not a constant
    offset. Verify the recovered mapping actually changes slope across the
    path when one signal is stretched relative to the other.
    """
    melody = _melody_wave()
    stretched = librosa.effects.time_stretch(melody, rate=0.75)  # 25% slower

    ref_bytes = to_wav_bytes(melody, SAMPLE_RATE)
    other_bytes = to_wav_bytes(stretched, SAMPLE_RATE)

    warp_path, confidence, hop_ms = align_audio(ref_bytes, other_bytes)

    early = warp_path[len(warp_path) // 10]
    late = warp_path[-(len(warp_path) // 10 + 1)]

    early_offset_ms = (early[1] - early[0]) * hop_ms
    late_offset_ms = (late[1] - late[0]) * hop_ms

    # A constant-offset model would predict early_offset == late_offset.
    # With real tempo drift the offset should grow substantially over the
    # course of the path.
    assert abs(late_offset_ms - early_offset_ms) > hop_ms * 3


def test_align_audio_rejects_too_short_clips():
    tiny = to_wav_bytes(np.zeros(10, dtype=np.float32), SAMPLE_RATE)
    melody = _melody_wav_bytes()
    with pytest.raises(ValueError):
        align_audio(melody, tiny)


def test_aligned_playback_produces_matching_length_and_sample_rate():
    melody = _melody_wave()
    offset_ms = 200.0
    shifted = np.concatenate([_silence(offset_ms), melody])

    ref_bytes = to_wav_bytes(melody, SAMPLE_RATE)
    other_bytes = to_wav_bytes(shifted, SAMPLE_RATE)

    warp_path, confidence, hop_ms = align_audio(ref_bytes, other_bytes)
    result = render_aligned_playback(ref_bytes, other_bytes, warp_path, hop_ms)

    assert result.sample_rate == SAMPLE_RATE

    ref_samples, _ = load_audio_mono(result.reference_wav)
    synced_samples, _ = load_audio_mono(result.synced_other_wav)
    mixed_samples, _ = load_audio_mono(result.mixed_wav)

    assert len(synced_samples) == len(ref_samples)
    assert len(mixed_samples) == len(ref_samples)
    assert np.max(np.abs(mixed_samples)) <= 1.0 + 1e-6


def test_streaming_aligner_recovers_similar_offset_to_batch():
    melody = _melody_wave()
    offset_ms = 300.0
    shifted = np.concatenate([_silence(offset_ms), melody])

    ref_bytes = to_wav_bytes(melody, SAMPLE_RATE)
    other_bytes = to_wav_bytes(shifted, SAMPLE_RATE)
    batch_warp_path, _, batch_hop_ms = align_audio(ref_bytes, other_bytes)
    batch_mid_offset_ms = (
        (batch_warp_path[len(batch_warp_path) // 2][1] - batch_warp_path[len(batch_warp_path) // 2][0])
        * batch_hop_ms
    )

    aligner = StreamingAligner(signal_ids=("sig_a", "sig_b"), window_frames=50)
    chunk_samples = SAMPLE_RATE // 2  # 500ms chunks, as if streaming live audio
    result = None
    for start in range(0, max(len(melody), len(shifted)), chunk_samples):
        end = start + chunk_samples
        chunk_ref = melody[start:end] if start < len(melody) else np.zeros(0, dtype=np.float32)
        chunk_other = shifted[start:end] if start < len(shifted) else np.zeros(0, dtype=np.float32)
        if len(chunk_ref) == 0 or len(chunk_other) == 0:
            continue
        r = aligner.push(to_wav_bytes(chunk_ref, SAMPLE_RATE), to_wav_bytes(chunk_other, SAMPLE_RATE))
        if r is not None:
            result = r

    assert result is not None
    assert result.streaming is True
    mid = result.warp_path[len(result.warp_path) // 2]
    streaming_offset_ms = (mid[1] - mid[0]) * result.hop_length_ms
    assert abs(streaming_offset_ms - batch_mid_offset_ms) < result.hop_length_ms * 4


def test_alignment_result_matches_shared_schema():
    melody = _melody_wav_bytes()
    warp_path, confidence, hop_ms = align_audio(melody, melody)
    result = AlignmentResult(
        signal_ids=["sig_a", "sig_b"],
        reference_signal_id="sig_a",
        warp_path=[tuple(p) for p in warp_path],
        confidence=confidence,
        hop_length_ms=hop_ms,
        streaming=False,
    )
    jsonschema.validate(instance=result.to_dict(), schema=_ALIGNMENT_SCHEMA)


def test_unrelated_melodies_get_low_dtw_confidence():
    """Sanity check for the fallback trigger condition: two signals built
    from disjoint pitch classes (standing in for two people singing
    different material) should score meaningfully lower than two identical
    signals, ideally below the fallback threshold."""
    melody_a = _melody_wav_bytes(hz_list=_MELODY_HZ)
    melody_b = _melody_wav_bytes(hz_list=_UNRELATED_MELODY_HZ)

    _, same_content_confidence, _ = align_audio(melody_a, melody_a)
    _, different_content_confidence, _ = align_audio(melody_a, melody_b)

    assert different_content_confidence < same_content_confidence


def test_align_signals_falls_back_to_timestamp_offset_for_unrelated_content(tmp_path):
    """When DTW confidence is too low to trust (different material, not a
    tempo-drifted version of the same melody), align_signals must fall back
    to a constant offset derived from the Signals' captured start_time
    (Task 1's shared room clock), not return a meaningless warp path."""
    melody_a = _melody_wave(hz_list=_MELODY_HZ)
    melody_b = _melody_wave(hz_list=_UNRELATED_MELODY_HZ)

    with SignalStore(root=tmp_path / "signals") as store:
        signal_ref = store.save(
            participant_id="p1", room_id="room1",
            start_time=1000.0, end_time=1000.0 + len(melody_a) / SAMPLE_RATE * 1000,
            sample_rate=SAMPLE_RATE, audio_bytes=to_wav_bytes(melody_a, SAMPLE_RATE),
        )
        true_offset_ms = 750.0
        signal_other = store.save(
            participant_id="p2", room_id="room1",
            start_time=signal_ref.start_time + true_offset_ms,
            end_time=signal_ref.start_time + true_offset_ms + len(melody_b) / SAMPLE_RATE * 1000,
            sample_rate=SAMPLE_RATE, audio_bytes=to_wav_bytes(melody_b, SAMPLE_RATE),
        )

        # Force the fallback deterministically regardless of how low this
        # particular synthetic pair's DTW confidence happens to be.
        result = align_signals(signal_ref, signal_other, store, confidence_threshold=1.1)

    assert result.method == "timestamp_offset"
    recovered_offset_ms = (result.warp_path[0][1] - result.warp_path[0][0]) * result.hop_length_ms
    assert abs(recovered_offset_ms - true_offset_ms) < result.hop_length_ms


def test_align_signals_uses_dtw_for_shared_content(tmp_path):
    """The common case (Teach/Practice/Performance as scoped: same melody)
    should still use content-based DTW, not always fall back."""
    melody = _melody_wave()

    with SignalStore(root=tmp_path / "signals") as store:
        signal_ref = store.save(
            participant_id="p1", room_id="room1",
            start_time=0.0, end_time=len(melody) / SAMPLE_RATE * 1000,
            sample_rate=SAMPLE_RATE, audio_bytes=to_wav_bytes(melody, SAMPLE_RATE),
        )
        signal_other = store.save(
            participant_id="p2", room_id="room1",
            start_time=0.0, end_time=len(melody) / SAMPLE_RATE * 1000,
            sample_rate=SAMPLE_RATE, audio_bytes=to_wav_bytes(melody, SAMPLE_RATE),
        )

        result = align_signals(signal_ref, signal_other, store)

    assert result.method == "dtw_chroma"
    assert result.confidence > 0.9


def test_streaming_aligner_falls_back_when_content_differs_but_clock_known():
    melody_a = _melody_wave(hz_list=_MELODY_HZ)
    true_offset_ms = 400.0
    melody_b = np.concatenate([_silence(true_offset_ms), _melody_wave(hz_list=_UNRELATED_MELODY_HZ)])

    aligner = StreamingAligner(
        signal_ids=("sig_a", "sig_b"),
        window_frames=50,
        clock_offset_ms=true_offset_ms,
        confidence_threshold=1.1,  # force fallback regardless of actual DTW confidence
    )
    chunk_samples = SAMPLE_RATE // 2
    result = None
    for start in range(0, max(len(melody_a), len(melody_b)), chunk_samples):
        end = start + chunk_samples
        chunk_a = melody_a[start:end] if start < len(melody_a) else np.zeros(0, dtype=np.float32)
        chunk_b = melody_b[start:end] if start < len(melody_b) else np.zeros(0, dtype=np.float32)
        if len(chunk_a) == 0 or len(chunk_b) == 0:
            continue
        r = aligner.push(to_wav_bytes(chunk_a, SAMPLE_RATE), to_wav_bytes(chunk_b, SAMPLE_RATE))
        if r is not None:
            result = r

    assert result is not None
    assert result.method == "timestamp_offset"
    recovered_offset_ms = (result.warp_path[0][1] - result.warp_path[0][0]) * result.hop_length_ms
    assert abs(recovered_offset_ms - true_offset_ms) < result.hop_length_ms
