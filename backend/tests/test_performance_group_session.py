"""Regression tests for PerformanceGroupSession's emit-cursor fix.

Before the fix, push() always returned a fixed ~0.25s trailing slice of the
current mix buffer regardless of how much genuinely new audio had arrived
that call. Since audio_chunk messages aren't guaranteed to match that
duration, this either dropped audio (chunk larger than 0.25s -- the case
this test exercises) or repeated it (chunk smaller than 0.25s, mirroring
Practice mode's bug in modes/practice/pipeline.py). See
docs/integration-contracts.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "audio-intelligence"))
sys.path.insert(0, str(ROOT / "signal-processing"))

from worker import PerformanceGroupSession  # noqa: E402

SAMPLE_RATE = 22050


def _melody(hz_list, note_ms=300, sample_rate=SAMPLE_RATE):
    segs = []
    for hz in hz_list:
        n = int(sample_rate * note_ms / 1000)
        t = np.arange(n) / sample_rate
        segs.append((0.4 * np.sin(2 * np.pi * hz * t)).astype(np.float32))
    return np.concatenate(segs)


def test_emitted_duration_tracks_wall_clock_duration_not_a_fixed_tail():
    lead = _melody([261.63, 293.66, 329.63, 349.23, 392.00, 440.00, 493.88, 523.25])
    other = np.concatenate(
        [np.zeros(int(SAMPLE_RATE * 0.35), dtype=np.float32), lead]
    )

    session = PerformanceGroupSession(room_id="r_test", sample_rate=SAMPLE_RATE)
    chunk = SAMPLE_RATE // 2  # 0.5s per audio_chunk -- larger than the old
    # fixed 0.25s output tail, which used to silently drop about half of
    # every chunk's audio.

    total_emitted = 0
    for start in range(0, max(len(lead), len(other)), chunk):
        for pid, buf in (("lead", lead), ("other", other)):
            piece = buf[start : start + chunk]
            if piece.size == 0:
                continue
            out = session.push(pid, piece)
            if out is not None:
                total_emitted += out.size

    wall_clock_samples = max(len(lead), len(other))
    ratio = total_emitted / wall_clock_samples
    # Real behavior after the fix comes out around 0.7-0.9 (observed
    # ~0.79): slightly under 1.0 from the margin held back at the trailing
    # edge plus warm-up latency, both expected. The old bug dropped roughly
    # half of every chunk (0.25s emitted per 0.5s of new audio), so it
    # would land near 0.5 or below; this range is a meaningful guard.
    assert 0.6 <= ratio <= 1.1


def test_emitted_audio_is_not_silence():
    lead = _melody([261.63, 293.66, 329.63, 349.23, 392.00])
    other = np.concatenate(
        [np.zeros(int(SAMPLE_RATE * 0.2), dtype=np.float32), lead]
    )
    session = PerformanceGroupSession(room_id="r_test2", sample_rate=SAMPLE_RATE)
    chunk = SAMPLE_RATE // 2

    emitted = []
    for start in range(0, max(len(lead), len(other)), chunk):
        for pid, buf in (("lead", lead), ("other", other)):
            piece = buf[start : start + chunk]
            if piece.size == 0:
                continue
            out = session.push(pid, piece)
            if out is not None and out.size:
                emitted.append(out)

    assert emitted
    full = np.concatenate(emitted)
    assert np.max(np.abs(full)) > 0.0


def test_chunk_boundaries_are_smooth_not_clicky():
    """Regression test for the click artifact described in
    docs/integration-contracts.md: each committed chunk comes from an
    independent re-render of the pairwise-composed mix, so consecutive
    chunks' waveform values weren't guaranteed to connect -- audible as a
    click at every chunk boundary. The boundary jump should be the same
    order of magnitude as jumps within a chunk, not roughly 10x larger
    (observed before the crossfade fix)."""
    lead = _melody([261.63, 293.66, 329.63, 349.23, 392.00, 440.00, 493.88, 523.25])
    other = np.concatenate(
        [np.zeros(int(SAMPLE_RATE * 0.35), dtype=np.float32), lead]
    )
    session = PerformanceGroupSession(room_id="r_test3", sample_rate=SAMPLE_RATE)
    chunk = SAMPLE_RATE // 2

    emitted = []
    for start in range(0, max(len(lead), len(other)), chunk):
        for pid, buf in (("lead", lead), ("other", other)):
            piece = buf[start : start + chunk]
            if piece.size == 0:
                continue
            out = session.push(pid, piece)
            if out is not None and out.size:
                emitted.append(out)

    assert len(emitted) >= 3, "need several chunks to check boundaries"

    interior_deltas = np.concatenate([np.abs(np.diff(p)) for p in emitted if p.size > 1])
    boundary_jumps = np.array(
        [abs(emitted[i][-1] - emitted[i + 1][0]) for i in range(len(emitted) - 1)]
    )
    # Median, not max: the very first crossfade-eligible boundary can still
    # be a hard cut (no held-back audio exists yet for the first chunk),
    # which is expected and not what this test targets.
    ratio = np.median(boundary_jumps) / np.median(interior_deltas)
    # Observed ~11.1x before the crossfade fix, ~1.3x after.
    assert ratio < 3.0
