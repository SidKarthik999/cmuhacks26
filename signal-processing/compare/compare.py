"""Task 5 -- compare difference between two aligned musical signals.

Resolved scoring formula (ROADMAP.md): overall_similarity_score =
0.7 * pitch_accuracy + 0.3 * timing_accuracy. The 70/30 weighting is fixed;
the per-metric normalization curve is documented here as an implementation
detail:

- pitch_accuracy = clamp(100 - mean_abs_pitch_deviation_cents, 0, 100) --
  cents map 1:1 to points lost, so a full semitone (100 cents) off zeroes
  the score. Typical natural pitch wobble (a few cents) barely costs
  anything; a clearly wrong note (50+ cents) costs proportionally.
- timing_accuracy = clamp(100 - mean_abs_timing_deviation_ms / 2, 0, 100) --
  200ms of onset drift (a very noticeable rush/drag) zeroes the score.

Reuses Person B's pitch-tracking module (audio-intelligence/notes/pitch.py)
as the shared building block, per ROADMAP.md's cross-cutting note -- pitch
tracking is implemented once (Task 8) and shared, not duplicated here.
"""
from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "audio-intelligence"))

from notes.pitch import extract_pitch_contour, notes_from_contour  # noqa: E402

PITCH_VOICED_CONFIDENCE_MIN = 0.3
NOTE_MATCH_TOLERANCE_MS = 300.0


@dataclass
class DeviationStats:
    mean_abs: float
    max_abs: float
    sample_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mean_abs": self.mean_abs,
            "max_abs": self.max_abs,
            "sample_count": self.sample_count,
        }


@dataclass
class ComparisonResult:
    signal_ids: Tuple[str, str]
    reference_signal_id: str
    alignment_method: str
    content_matched: bool
    pitch_deviation_cents: Optional[DeviationStats]
    timing_deviation_ms: Optional[DeviationStats]
    pitch_accuracy: Optional[float]
    timing_accuracy: Optional[float]
    overall_similarity_score: Optional[float]
    id: str = field(default_factory=lambda: f"cmp_{uuid.uuid4()}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "signal_ids": list(self.signal_ids),
            "reference_signal_id": self.reference_signal_id,
            "alignment_method": self.alignment_method,
            "content_matched": self.content_matched,
            "pitch_deviation_cents": self.pitch_deviation_cents.to_dict()
            if self.pitch_deviation_cents
            else None,
            "timing_deviation_ms": self.timing_deviation_ms.to_dict()
            if self.timing_deviation_ms
            else None,
            "pitch_accuracy": self.pitch_accuracy,
            "timing_accuracy": self.timing_accuracy,
            "overall_similarity_score": self.overall_similarity_score,
        }


def _time_map_ref_to_other(warp_path, hop_length_ms: float):
    warp_path = np.asarray(warp_path)
    ref_times_ms = warp_path[:, 0] * hop_length_ms
    other_times_ms = warp_path[:, 1] * hop_length_ms
    return ref_times_ms, other_times_ms


def _pitch_deviation_cents(
    contour_ref: List[Dict[str, Any]],
    contour_other: List[Dict[str, Any]],
    ref_times_ms: np.ndarray,
    other_times_ms: np.ndarray,
) -> DeviationStats:
    other_point_times = np.array([p["time_ms"] for p in contour_other], dtype=np.float64)
    deviations = []
    for p in contour_ref:
        if p["pitch_hz"] is None or p["confidence"] < PITCH_VOICED_CONFIDENCE_MIN:
            continue
        t_other = float(np.interp(p["time_ms"], ref_times_ms, other_times_ms))
        idx = int(np.argmin(np.abs(other_point_times - t_other)))
        match = contour_other[idx]
        if match["pitch_hz"] is None or match["confidence"] < PITCH_VOICED_CONFIDENCE_MIN:
            continue
        cents = 1200.0 * np.log2(match["pitch_hz"] / p["pitch_hz"])
        deviations.append(cents)

    if not deviations:
        return DeviationStats(mean_abs=0.0, max_abs=0.0, sample_count=0)
    abs_dev = np.abs(deviations)
    return DeviationStats(
        mean_abs=float(np.mean(abs_dev)),
        max_abs=float(np.max(abs_dev)),
        sample_count=len(deviations),
    )


def _timing_deviation_ms(
    contour_ref: List[Dict[str, Any]],
    contour_other: List[Dict[str, Any]],
    ref_times_ms: np.ndarray,
    other_times_ms: np.ndarray,
) -> DeviationStats:
    notes_ref = notes_from_contour(contour_ref)
    notes_other = notes_from_contour(contour_other)
    if not notes_ref or not notes_other:
        return DeviationStats(mean_abs=0.0, max_abs=0.0, sample_count=0)

    other_onsets = np.array([n["start_ms"] for n in notes_other], dtype=np.float64)
    deviations = []
    for n in notes_ref:
        # Where the smooth DTW warp trend predicts this ref onset should
        # land in the other signal's timeline...
        predicted_other_ms = float(np.interp(n["start_ms"], ref_times_ms, other_times_ms))
        # ...vs. where the other performer's actual matching note onset is.
        idx = int(np.argmin(np.abs(other_onsets - predicted_other_ms)))
        actual_other_ms = float(other_onsets[idx])
        if abs(actual_other_ms - predicted_other_ms) > NOTE_MATCH_TOLERANCE_MS:
            continue  # no plausible corresponding note; skip rather than guess
        deviations.append(actual_other_ms - predicted_other_ms)

    if not deviations:
        return DeviationStats(mean_abs=0.0, max_abs=0.0, sample_count=0)
    abs_dev = np.abs(deviations)
    return DeviationStats(
        mean_abs=float(np.mean(abs_dev)),
        max_abs=float(np.max(abs_dev)),
        sample_count=len(deviations),
    )


def score_from_deviations(
    pitch_dev: DeviationStats, timing_dev: DeviationStats
) -> Tuple[float, float, float]:
    pitch_accuracy = float(np.clip(100.0 - pitch_dev.mean_abs, 0.0, 100.0))
    timing_accuracy = float(np.clip(100.0 - timing_dev.mean_abs / 2.0, 0.0, 100.0))
    overall = 0.7 * pitch_accuracy + 0.3 * timing_accuracy
    return pitch_accuracy, timing_accuracy, overall


def compare_audio(
    audio_ref: bytes,
    audio_other: bytes,
    warp_path,
    hop_length_ms: float,
) -> Tuple[DeviationStats, DeviationStats, float, float, float]:
    """Compare two raw audio buffers given a Task 4 warp path between them.
    Returns (pitch_dev, timing_dev, pitch_accuracy, timing_accuracy, overall).
    """
    contour_ref = extract_pitch_contour(audio_ref)
    contour_other = extract_pitch_contour(audio_other)
    ref_times_ms, other_times_ms = _time_map_ref_to_other(warp_path, hop_length_ms)

    pitch_dev = _pitch_deviation_cents(contour_ref, contour_other, ref_times_ms, other_times_ms)
    timing_dev = _timing_deviation_ms(contour_ref, contour_other, ref_times_ms, other_times_ms)
    pitch_accuracy, timing_accuracy, overall = score_from_deviations(pitch_dev, timing_dev)
    return pitch_dev, timing_dev, pitch_accuracy, timing_accuracy, overall


def compare_signals(signal_ref, signal_other, alignment, store) -> ComparisonResult:
    """Task 5's Signal-level entry point.

    `signal_ref`/`signal_other` are Signal objects; `alignment` is the
    Task 4 AlignmentResult produced by align_signals(signal_ref,
    signal_other, store); `store` is a SignalStore.
    """
    if not alignment.warp_path or alignment.method == "timestamp_offset":
        # Fallback alignment: the two signals don't share melodic content
        # (see sync/dtw.py's confidence-gated fallback), so per-note pitch
        # and timing comparison isn't meaningful -- there's no real
        # correspondence to measure deviation against.
        return ComparisonResult(
            signal_ids=(signal_ref.id, signal_other.id),
            reference_signal_id=signal_ref.id,
            alignment_method=alignment.method,
            content_matched=False,
            pitch_deviation_cents=None,
            timing_deviation_ms=None,
            pitch_accuracy=None,
            timing_accuracy=None,
            overall_similarity_score=None,
        )

    audio_ref = store.get_audio(signal_ref.id)
    audio_other = store.get_audio(signal_other.id)

    pitch_dev, timing_dev, pitch_accuracy, timing_accuracy, overall = compare_audio(
        audio_ref, audio_other, alignment.warp_path, alignment.hop_length_ms
    )

    return ComparisonResult(
        signal_ids=(signal_ref.id, signal_other.id),
        reference_signal_id=signal_ref.id,
        alignment_method=alignment.method,
        content_matched=True,
        pitch_deviation_cents=pitch_dev,
        timing_deviation_ms=timing_dev,
        pitch_accuracy=pitch_accuracy,
        timing_accuracy=timing_accuracy,
        overall_similarity_score=overall,
    )
