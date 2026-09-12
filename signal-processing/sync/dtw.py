"""Batch DTW alignment between two Signals (Task 4).

Resolved design decision from ROADMAP.md: full tempo-drift handling is
required, not just a constant offset, so this uses dynamic time warping
over chroma features as the primary method. A fast cross-correlation
constant-offset estimate could be layered in later to seed/bound the DTW
search, but the warp path itself is what this module must produce.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import librosa
import numpy as np

from .features import chroma_features, load_audio_mono, resample_to_match

DEFAULT_HOP_LENGTH = 2048


@dataclass
class AlignmentResult:
    signal_ids: List[str]
    reference_signal_id: str
    warp_path: List[Tuple[int, int]]
    confidence: float
    hop_length_ms: float
    method: str = "dtw_chroma"
    streaming: bool = False
    id: str = field(default_factory=lambda: f"align_{uuid.uuid4()}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "signal_ids": self.signal_ids,
            "reference_signal_id": self.reference_signal_id,
            "warp_path": [[int(a), int(b)] for a, b in self.warp_path],
            "confidence": self.confidence,
            "hop_length_ms": self.hop_length_ms,
            "method": self.method,
            "streaming": self.streaming,
        }

    def offset_ms_at(self, reference_frame: int) -> Optional[float]:
        """Convenience: the other signal's time offset (ms) aligned to a
        given reference frame, via nearest warp-path anchor."""
        if not self.warp_path:
            return None
        idx = min(range(len(self.warp_path)), key=lambda i: abs(self.warp_path[i][0] - reference_frame))
        ref_frame, other_frame = self.warp_path[idx]
        return (other_frame - ref_frame) * self.hop_length_ms


def _dtw_chroma(
    chroma_ref: np.ndarray, chroma_other: np.ndarray
) -> Tuple[np.ndarray, float]:
    """Run librosa's DTW over two chroma matrices. Returns (warp_path
    ascending [ref_frame, other_frame], confidence in [0, 1])."""
    D, wp = librosa.sequence.dtw(X=chroma_ref, Y=chroma_other, metric="cosine")
    wp = wp[::-1]  # librosa returns end-to-start; flip to ascending order

    # Confidence: average per-step cosine-distance cost along the optimal
    # path, mapped into [0, 1] (lower cost -> higher confidence). This is a
    # heuristic, not a calibrated probability -- good enough to rank/flag
    # alignments, not to claim statistical certainty.
    total_cost = float(D[wp[-1, 0], wp[-1, 1]])
    avg_cost_per_step = total_cost / max(len(wp), 1)
    confidence = float(np.clip(1.0 - avg_cost_per_step, 0.0, 1.0))
    return wp, confidence


def align_audio(
    audio_ref: bytes,
    audio_other: bytes,
    hop_length: int = DEFAULT_HOP_LENGTH,
) -> Tuple[np.ndarray, float, float]:
    """Align two raw audio buffers directly (no Signal/store dependency —
    used by both the Signal-level API below and the streaming aligner).

    Returns (warp_path, confidence, hop_length_ms).
    """
    y_ref, sr_ref = load_audio_mono(audio_ref)
    y_other, sr_other = load_audio_mono(audio_other)
    y_other = resample_to_match(y_other, sr_other, sr_ref)

    if len(y_ref) < hop_length or len(y_other) < hop_length:
        raise ValueError("audio too short to extract chroma frames for DTW")

    chroma_ref, hop_length_ms = chroma_features(y_ref, sr_ref, hop_length)
    chroma_other, _ = chroma_features(y_other, sr_ref, hop_length)

    warp_path, confidence = _dtw_chroma(chroma_ref, chroma_other)
    return warp_path, confidence, hop_length_ms


def align_signals(signal_ref, signal_other, store) -> AlignmentResult:
    """Task 4's Signal-level entry point: fetch both Signals' audio from the
    store, align them, and return the full AlignmentResult contract.

    `signal_ref`/`signal_other` are Signal objects (signal-processing.storage.Signal);
    `store` is a SignalStore.
    """
    audio_ref = store.get_audio(signal_ref.id)
    audio_other = store.get_audio(signal_other.id)

    warp_path, confidence, hop_length_ms = align_audio(audio_ref, audio_other)

    return AlignmentResult(
        signal_ids=[signal_ref.id, signal_other.id],
        reference_signal_id=signal_ref.id,
        warp_path=[tuple(p) for p in warp_path],
        confidence=confidence,
        hop_length_ms=hop_length_ms,
        streaming=False,
    )
