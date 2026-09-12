"""Aligned playback: render two Signals' audio so they play together with
the computed warp path applied (Task 4's second output).

Approach: time-warp the "other" signal's waveform onto the reference
signal's timeline using the warp path as a piecewise time-mapping, via
linear interpolation/resampling. This keeps both a standalone "synced other"
track and a mixed "both together" track available, since different modes
need different playback (e.g. Teach mode compares tracks separately;
Practice mode wants a blended feed).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .features import load_audio_mono, resample_to_match, to_wav_bytes


@dataclass
class AlignedPlayback:
    sample_rate: int
    reference_wav: bytes
    synced_other_wav: bytes
    mixed_wav: bytes


def render_aligned_playback(
    audio_ref: bytes,
    audio_other: bytes,
    warp_path,
    hop_length_ms: float,
) -> AlignedPlayback:
    """Build playback-ready WAV bytes for a reference Signal and an "other"
    Signal time-warped to match it, plus a mixed-down combination of both.
    """
    y_ref, sr_ref = load_audio_mono(audio_ref)
    y_other, sr_other = load_audio_mono(audio_other)
    y_other = resample_to_match(y_other, sr_other, sr_ref)

    warp_path = np.asarray(warp_path)
    hop_length_samples = hop_length_ms / 1000.0 * sr_ref

    ref_frame_times = warp_path[:, 0] * hop_length_samples / sr_ref
    other_frame_times = warp_path[:, 1] * hop_length_samples / sr_ref

    ref_sample_times = np.arange(len(y_ref)) / sr_ref
    # For each moment in the reference timeline, find the corresponding
    # moment in the other signal via the warp-path anchors.
    other_time_at_ref = np.interp(
        ref_sample_times, ref_frame_times, other_frame_times,
        left=other_frame_times[0] if len(other_frame_times) else 0.0,
        right=other_frame_times[-1] if len(other_frame_times) else 0.0,
    )
    other_sample_times = np.arange(len(y_other)) / sr_ref
    synced_other = np.interp(
        other_time_at_ref, other_sample_times, y_other, left=0.0, right=0.0
    ).astype(np.float32)

    mixed = 0.5 * y_ref + 0.5 * synced_other
    peak = float(np.max(np.abs(mixed))) if len(mixed) else 0.0
    if peak > 1.0:
        mixed = mixed / peak

    return AlignedPlayback(
        sample_rate=sr_ref,
        reference_wav=to_wav_bytes(y_ref, sr_ref),
        synced_other_wav=to_wav_bytes(synced_other, sr_ref),
        mixed_wav=to_wav_bytes(mixed.astype(np.float32), sr_ref),
    )
