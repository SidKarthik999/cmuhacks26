import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jsonschema
import json
import numpy as np
import pytest
import librosa

from compare import compare_audio, compare_signals
from sync import align_audio, align_signals
from sync.features import to_wav_bytes
from storage import SignalStore

SAMPLE_RATE = 22050
NOTE_MS = 300

_COMPARISON_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "shared" / "schemas" / "comparison_result.schema.json").read_text()
)

_MELODY_HZ = [261.63, 293.66, 329.63, 349.23, 392.00, 440.00, 493.88, 523.25]  # C major scale
_UNRELATED_MELODY_HZ = [277.18, 311.13, 369.99, 415.30, 466.16]


def _melody_wave(hz_list, note_ms=NOTE_MS, sample_rate=SAMPLE_RATE) -> np.ndarray:
    segments = []
    for hz in hz_list:
        n = int(sample_rate * note_ms / 1000)
        t = np.arange(n) / sample_rate
        segments.append((0.5 * np.sin(2 * np.pi * hz * t)).astype(np.float32))
    return np.concatenate(segments)


def _melody_wav_bytes(hz_list, note_ms=NOTE_MS, sample_rate=SAMPLE_RATE) -> bytes:
    return to_wav_bytes(_melody_wave(hz_list, note_ms, sample_rate), sample_rate)


def test_identical_signals_score_near_perfect():
    audio = _melody_wav_bytes(_MELODY_HZ)
    warp_path, confidence, hop_ms = align_audio(audio, audio)

    pitch_dev, timing_dev, pitch_acc, timing_acc, overall = compare_audio(
        audio, audio, warp_path, hop_ms
    )

    assert pitch_dev.mean_abs < 5.0  # a few cents of numerical noise at most
    assert timing_dev.mean_abs < 20.0
    assert pitch_acc > 90
    assert timing_acc > 90
    assert overall > 90


def test_pitch_shifted_signal_lowers_pitch_accuracy_not_timing():
    """Same note timing, every note shifted +50 cents -- pitch should read
    as clearly deviated while timing stays essentially perfect."""
    shift_ratio = 2.0 ** (50.0 / 1200.0)
    shifted_hz = [hz * shift_ratio for hz in _MELODY_HZ]

    audio_ref = _melody_wav_bytes(_MELODY_HZ)
    audio_shifted = _melody_wav_bytes(shifted_hz)

    warp_path, confidence, hop_ms = align_audio(audio_ref, audio_shifted)
    pitch_dev, timing_dev, pitch_acc, timing_acc, overall = compare_audio(
        audio_ref, audio_shifted, warp_path, hop_ms
    )

    assert pitch_dev.mean_abs > 30.0  # should detect close to the true 50-cent shift
    assert pitch_acc < 80
    assert timing_acc > 85  # timing wasn't touched


def test_correct_pitch_rushed_timing_scores_higher_than_wrong_pitch_correct_timing():
    """Acceptance criterion from ROADMAP.md Task 5: correct pitch + rushed
    timing must score noticeably higher than wrong pitch + correct timing."""
    audio_ref = _melody_wav_bytes(_MELODY_HZ)

    # Case A: correct pitch, tempo rushed (25% faster).
    rushed_wave = librosa.effects.time_stretch(_melody_wave(_MELODY_HZ), rate=1.25)
    audio_rushed = to_wav_bytes(rushed_wave, SAMPLE_RATE)
    warp_a, _, hop_a = align_audio(audio_ref, audio_rushed)
    _, _, _, _, score_a = compare_audio(audio_ref, audio_rushed, warp_a, hop_a)

    # Case B: wrong pitch (a full semitone off, +100 cents), correct timing.
    wrong_pitch_hz = [hz * (2.0 ** (100.0 / 1200.0)) for hz in _MELODY_HZ]
    audio_wrong_pitch = _melody_wav_bytes(wrong_pitch_hz)
    warp_b, _, hop_b = align_audio(audio_ref, audio_wrong_pitch)
    _, _, _, _, score_b = compare_audio(audio_ref, audio_wrong_pitch, warp_b, hop_b)

    assert score_a > score_b


def test_comparison_result_matches_shared_schema():
    audio = _melody_wav_bytes(_MELODY_HZ)
    warp_path, confidence, hop_ms = align_audio(audio, audio)
    pitch_dev, timing_dev, pitch_acc, timing_acc, overall = compare_audio(
        audio, audio, warp_path, hop_ms
    )

    from compare import ComparisonResult

    result = ComparisonResult(
        signal_ids=("sig_a", "sig_b"),
        reference_signal_id="sig_a",
        alignment_method="dtw_chroma",
        content_matched=True,
        pitch_deviation_cents=pitch_dev,
        timing_deviation_ms=timing_dev,
        pitch_accuracy=pitch_acc,
        timing_accuracy=timing_acc,
        overall_similarity_score=overall,
    )
    jsonschema.validate(instance=result.to_dict(), schema=_COMPARISON_SCHEMA)


def test_compare_signals_reports_not_content_matched_for_unrelated_material(tmp_path):
    """When Task 4 falls back to timestamp_offset (different material), Task
    5 must not fabricate a pitch/timing score -- content_matched should be
    false and every deviation/score field null."""
    melody_a = _melody_wave(_MELODY_HZ)
    melody_b = _melody_wave(_UNRELATED_MELODY_HZ)

    with SignalStore(root=tmp_path / "signals") as store:
        signal_ref = store.save(
            participant_id="p1", room_id="room1",
            start_time=0.0, end_time=len(melody_a) / SAMPLE_RATE * 1000,
            sample_rate=SAMPLE_RATE, audio_bytes=to_wav_bytes(melody_a, SAMPLE_RATE),
        )
        signal_other = store.save(
            participant_id="p2", room_id="room1",
            start_time=0.0, end_time=len(melody_b) / SAMPLE_RATE * 1000,
            sample_rate=SAMPLE_RATE, audio_bytes=to_wav_bytes(melody_b, SAMPLE_RATE),
        )

        alignment = align_signals(signal_ref, signal_other, store, confidence_threshold=1.1)
        assert alignment.method == "timestamp_offset"

        result = compare_signals(signal_ref, signal_other, alignment, store)

    assert result.content_matched is False
    assert result.pitch_deviation_cents is None
    assert result.timing_deviation_ms is None
    assert result.pitch_accuracy is None
    assert result.timing_accuracy is None
    assert result.overall_similarity_score is None
    jsonschema.validate(instance=result.to_dict(), schema=_COMPARISON_SCHEMA)


def test_compare_signals_end_to_end_with_real_dtw_alignment(tmp_path):
    melody = _melody_wave(_MELODY_HZ)
    with SignalStore(root=tmp_path / "signals") as store:
        signal_ref = store.save(
            participant_id="teacher", room_id="room1",
            start_time=0.0, end_time=len(melody) / SAMPLE_RATE * 1000,
            sample_rate=SAMPLE_RATE, audio_bytes=to_wav_bytes(melody, SAMPLE_RATE),
        )
        signal_other = store.save(
            participant_id="student", room_id="room1",
            start_time=0.0, end_time=len(melody) / SAMPLE_RATE * 1000,
            sample_rate=SAMPLE_RATE, audio_bytes=to_wav_bytes(melody, SAMPLE_RATE),
        )

        alignment = align_signals(signal_ref, signal_other, store)
        assert alignment.method == "dtw_chroma"

        result = compare_signals(signal_ref, signal_other, alignment, store)

    assert result.content_matched is True
    assert result.overall_similarity_score is not None
    assert result.overall_similarity_score > 90  # identical performances
