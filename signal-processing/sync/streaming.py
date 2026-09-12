"""Incremental/windowed DTW variant for the <1-2s latency modes (Practice,
Performance) per ROADMAP.md's cross-cutting streaming requirement.

Tasks 4/6/7 can't be purely batch/offline — Practice/Performance need
clean/sync/mix to update on a rolling buffer as audio arrives, not only once
a Signal is fully closed. StreamingAligner re-runs DTW over a bounded
trailing window of chroma frames each time new audio arrives, so cost stays
roughly constant per update instead of growing with total call duration.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from .dtw import DEFAULT_HOP_LENGTH, AlignmentResult, _dtw_chroma
from .features import chroma_features, load_audio_mono, resample_to_match

DEFAULT_WINDOW_FRAMES = 200  # ~9s of context at hop_length=2048, sr=44100


class StreamingAligner:
    """Feed rolling audio chunks for two participants; get back an updated
    AlignmentResult after each chunk, computed over a bounded trailing
    window rather than the full history.
    """

    def __init__(
        self,
        signal_ids: Tuple[str, str],
        hop_length: int = DEFAULT_HOP_LENGTH,
        window_frames: int = DEFAULT_WINDOW_FRAMES,
    ):
        self.signal_ids = list(signal_ids)
        self.hop_length = hop_length
        self.window_frames = window_frames
        self._sr: Optional[int] = None
        self._chroma_ref: Optional[np.ndarray] = None
        self._chroma_other: Optional[np.ndarray] = None
        self._frame_offset = 0  # frames dropped so far, to keep warp_path indices global
        self.latest: Optional[AlignmentResult] = None

    def push(self, chunk_ref: bytes, chunk_other: bytes) -> Optional[AlignmentResult]:
        """Append newly-arrived audio for both streams and recompute the
        alignment over the trailing window. Returns None until both streams
        have enough audio for at least one chroma frame.
        """
        y_ref, sr = load_audio_mono(chunk_ref)
        y_other, sr_other = load_audio_mono(chunk_other)
        y_other = resample_to_match(y_other, sr_other, sr)
        if self._sr is None:
            self._sr = sr
        elif self._sr != sr:
            raise ValueError("sample rate changed mid-stream")

        if len(y_ref) < self.hop_length or len(y_other) < self.hop_length:
            return None  # not enough new audio yet for a chroma frame

        new_chroma_ref, hop_length_ms = chroma_features(y_ref, sr, self.hop_length)
        new_chroma_other, _ = chroma_features(y_other, sr, self.hop_length)

        self._chroma_ref = (
            new_chroma_ref
            if self._chroma_ref is None
            else np.concatenate([self._chroma_ref, new_chroma_ref], axis=1)
        )
        self._chroma_other = (
            new_chroma_other
            if self._chroma_other is None
            else np.concatenate([self._chroma_other, new_chroma_other], axis=1)
        )

        # Bound cost: keep only the trailing window, tracking how many
        # leading frames were dropped so warp_path indices stay meaningful
        # relative to the full stream, not just the window.
        n_frames = self._chroma_ref.shape[1]
        if n_frames > self.window_frames:
            drop = n_frames - self.window_frames
            self._chroma_ref = self._chroma_ref[:, drop:]
            self._chroma_other = self._chroma_other[:, drop:]
            self._frame_offset += drop

        if self._chroma_ref.shape[1] < 2 or self._chroma_other.shape[1] < 2:
            return None

        warp_path, confidence = _dtw_chroma(self._chroma_ref, self._chroma_other)
        global_warp_path: List[Tuple[int, int]] = [
            (int(a) + self._frame_offset, int(b) + self._frame_offset) for a, b in warp_path
        ]

        self.latest = AlignmentResult(
            signal_ids=self.signal_ids,
            reference_signal_id=self.signal_ids[0],
            warp_path=global_warp_path,
            confidence=confidence,
            hop_length_ms=hop_length_ms,
            streaming=True,
        )
        return self.latest
