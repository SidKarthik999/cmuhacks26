"""Pitch tracking, vectorised.

The team's `audio-intelligence/yin.py` is the reference implementation and
this module is checked against it frame by frame (see
`swarlink.checks.check_pitch`). The difference is only in shape: that one
scores a single frame at a time, which is the right interface for a streaming
cleaner, while a scoring pass wants the whole take at once and the validation
sweep wants thousands of takes. Framing the signal and running one batched
FFT autocorrelation instead of a Python loop over `np.correlate` is what makes
the sweep finish in minutes rather than hours.

The algorithm is YIN (de Cheveigne & Kawahara 2002):

1. difference function d(tau) from autocorrelation,
2. cumulative mean normalisation, which is what stops the tracker from
   choosing an octave below the true period,
3. first dip under a threshold rather than the global minimum, which is what
   stops it from choosing an octave above,
4. parabolic interpolation for sub-sample precision, because a semitone at
   the top of a soprano's range is only a couple of samples of period.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np

SAMPLE_RATE = 22050
HOP_MS = 10.0
FRAME_MS = 40.0
FMIN = 70.0
FMAX = 1100.0
YIN_THRESHOLD = 0.15
VOICED_CONF = 0.45
VOICED_FLOOR_DB = -48.0


@dataclass
class Contour:
    """A pitch track on a fixed time grid.

    `hz` is NaN wherever the frame is unvoiced, so arithmetic on it propagates
    "we do not know" instead of silently scoring silence as a wrong note.
    """

    time_ms: np.ndarray
    hz: np.ndarray
    confidence: np.ndarray
    rms_db: np.ndarray
    sample_rate: int = SAMPLE_RATE

    @property
    def voiced(self) -> np.ndarray:
        return np.isfinite(self.hz)

    @property
    def voiced_fraction(self) -> float:
        return float(self.voiced.mean()) if self.hz.size else 0.0

    @property
    def hop_ms(self) -> float:
        return float(self.time_ms[1] - self.time_ms[0]) if self.time_ms.size > 1 else HOP_MS

    def midi(self) -> np.ndarray:
        with np.errstate(invalid="ignore", divide="ignore"):
            return 69.0 + 12.0 * np.log2(self.hz / 440.0)

    def at(self, times_ms: Sequence[float]) -> np.ndarray:
        """Sample the contour, keeping gaps as gaps.

        Interpolating across an unvoiced gap would invent a glide between two
        notes that were actually separated by a breath, so voicing is resolved
        by nearest neighbour and only the pitch inside a voiced run is
        interpolated.
        """
        times = np.asarray(times_ms, dtype=np.float64)
        if self.time_ms.size == 0:
            return np.full(times.shape, np.nan)
        idx = np.clip(
            np.searchsorted(self.time_ms, times).astype(int), 0, self.time_ms.size - 1
        )
        out = self.hz[idx].astype(np.float64)
        voiced = self.voiced
        if voiced.sum() >= 2:
            interp = np.interp(times, self.time_ms[voiced], self.hz[voiced])
            keep = np.isfinite(out)
            out[keep] = interp[keep]
        return out

    def as_points(self) -> List[Dict[str, object]]:
        """The team's pitch-contour document shape, for cross-module use."""
        return [
            {
                "time_ms": round(float(t), 2),
                "pitch_hz": None if not np.isfinite(h) else round(float(h), 3),
                "confidence": round(float(c), 4),
            }
            for t, h, c in zip(self.time_ms, self.hz, self.confidence)
        ]


def _frame(x: np.ndarray, frame: int, hop: int) -> np.ndarray:
    if x.size < frame:
        x = np.pad(x, (0, frame - x.size))
    n = 1 + (x.size - frame) // hop
    idx = np.arange(frame)[None, :] + hop * np.arange(n)[:, None]
    return x[idx]


def track(
    x: np.ndarray,
    sr: int = SAMPLE_RATE,
    hop_ms: float = HOP_MS,
    frame_ms: float = FRAME_MS,
    fmin: float = FMIN,
    fmax: float = FMAX,
    threshold: float = YIN_THRESHOLD,
    voiced_conf: float = VOICED_CONF,
) -> Contour:
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    hop = max(int(round(sr * hop_ms / 1000.0)), 1)
    frame = max(int(round(sr * frame_ms / 1000.0)), 4 * hop)
    if x.size < frame:
        return Contour(np.zeros(0), np.zeros(0), np.zeros(0), np.zeros(0), sr)

    # Normalise before analysis. Pitch does not depend on level, but a
    # voicing decision made against an absolute dBFS floor does: the same
    # singer recorded 18 dB quieter would have their soft notes declared
    # unvoiced and dropped from the score. Framing on a peak-normalised copy
    # makes the whole tracker gain-invariant, and the levels reported back
    # come from the original signal.
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    frames_raw = _frame(x, frame, hop)
    frames = frames_raw / peak if peak > 0 else frames_raw
    n_frames, w = frames.shape
    tau_min = max(2, int(sr / fmax))
    # One tau of headroom past the search range: finding the bottom of a dip
    # needs to look at the value after the last candidate.
    tau_hi = min(int(sr / fmin) + 2, w // 2)
    tau_max = tau_hi - 1
    if tau_max <= tau_min + 2:
        return Contour(np.zeros(0), np.zeros(0), np.zeros(0), np.zeros(0), sr)

    # Batched autocorrelation. One rfft over all frames at once.
    size = int(2 ** np.ceil(np.log2(2 * w)))
    spec = np.fft.rfft(frames, size, axis=1)
    corr = np.fft.irfft(spec * np.conj(spec), size, axis=1)[:, : tau_hi + 1]

    energy = np.concatenate(
        [np.zeros((n_frames, 1)), np.cumsum(frames ** 2, axis=1)], axis=1
    )
    taus = np.arange(tau_hi + 1)
    e0 = energy[:, w - taus] - energy[:, :1]
    e1 = energy[:, w : w + 1] - energy[:, taus]
    d = np.maximum(e0 + e1 - 2.0 * corr, 0.0)

    # Cumulative mean normalised difference. d'(0) is defined as 1 so that a
    # frame with no periodicity at all cannot win by default.
    cum = np.cumsum(d[:, 1:], axis=1)
    denom = cum / np.arange(1, d.shape[1])[None, :]
    cmnd = np.ones_like(d)
    with np.errstate(invalid="ignore", divide="ignore"):
        cmnd[:, 1:] = np.where(denom > 0, d[:, 1:] / denom, 1.0)
    cmnd = np.nan_to_num(cmnd, nan=1.0, posinf=1.0)

    # Absolute threshold, then descend to the bottom of that dip. Both halves
    # matter and they fail in opposite directions: taking the global minimum
    # instead of the first dip reports an octave too low on a voice with a
    # strong second harmonic, while taking the threshold crossing itself --
    # a point on the descending slope, not the minimum -- reports sharp and
    # makes parabolic interpolation meaningless, because there is no local
    # parabola to interpolate.
    window = cmnd[:, tau_min : tau_max + 2]
    search = window[:, :-1]
    below = search < threshold
    has = below.any(axis=1)
    first = np.argmax(below, axis=1)

    bottom = window[:, 1:] >= search
    order = np.arange(search.shape[1])[None, :]
    reachable = bottom & (order >= first[:, None])
    descended = np.where(
        reachable.any(axis=1), np.argmax(reachable, axis=1), search.shape[1] - 1
    )
    best = np.where(has, descended, np.argmin(search, axis=1)) + tau_min

    rows = np.arange(n_frames)
    lo = np.clip(best - 1, 1, tau_max)
    hi = np.clip(best + 1, 1, tau_max)
    a, b, c = cmnd[rows, lo], cmnd[rows, best], cmnd[rows, hi]
    denom_p = a - 2.0 * b + c
    shift = np.where(np.abs(denom_p) > 1e-12, 0.5 * (a - c) / denom_p, 0.0)
    period = best + np.clip(shift, -1.0, 1.0)

    conf = np.clip(1.0 - b, 0.0, 1.0)
    rms_db = 10.0 * np.log10(np.maximum((frames_raw ** 2).mean(axis=1), 1e-20))
    # Voicing is judged on the normalised copy, so the floor is relative to
    # the loudest moment of this take rather than to full scale.
    rel_db = 10.0 * np.log10(np.maximum((frames ** 2).mean(axis=1), 1e-20))

    hz = np.where(period > 0, sr / np.maximum(period, 1e-9), np.nan)
    hz = _subharmonic_guard(hz, frames, sr, fmin)
    unvoiced = (conf < voiced_conf) | (rel_db < VOICED_FLOOR_DB) | (hz < fmin) | (hz > fmax)
    hz = np.where(unvoiced, np.nan, hz)

    time_ms = np.arange(n_frames) * hop * 1000.0 / sr
    return Contour(time_ms, hz, conf, rms_db, sr)


SUBHARMONIC_FLOOR = 0.06
SUBHARMONIC_FFT = 4096


def _subharmonic_guard(
    hz: np.ndarray, frames: np.ndarray, sr: int, fmin: float
) -> np.ndarray:
    """Halve a reported pitch when the spectrum says the real fundamental is lower.

    YIN reports the period of the waveform, which is the honest answer and
    sometimes the wrong one: a vowel whose first formant sits on the second
    harmonic can leave the fundamental so weak that the waveform genuinely
    repeats at half the intended period. Sopranos do this for real, not only
    in synthesis.

    The test is spectral and does not need a tuned constant to be believable:
    if f is truly the fundamental, there is nothing at f/2 or 3f/2, because
    those are not harmonics of f. Finding real energy there means f is the
    second harmonic of something lower. The floor is set at about -24 dB
    relative to the reported partial, well above any window leakage.
    """
    if hz.size == 0:
        return hz
    n = SUBHARMONIC_FFT
    window = np.hanning(frames.shape[1])
    mag = np.abs(np.fft.rfft(frames * window, n, axis=1))
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    rows = np.arange(frames.shape[0])

    def at(f: np.ndarray) -> np.ndarray:
        idx = np.clip(np.round(f * n / sr).astype(int), 0, freqs.size - 1)
        # Take the strongest of three adjacent bins so a slightly mistuned
        # partial is not missed on a bin boundary.
        lo = np.clip(idx - 1, 0, freqs.size - 1)
        hi = np.clip(idx + 1, 0, freqs.size - 1)
        return np.maximum(np.maximum(mag[rows, lo], mag[rows, idx]), mag[rows, hi])

    out = hz.copy()
    for _ in range(2):
        cand = out / 2.0
        testable = np.isfinite(out) & (cand >= fmin)
        if not np.any(testable):
            break
        safe = np.where(testable, out, fmin * 4.0)
        strength = at(safe)
        sub = (at(safe / 2.0) + at(safe * 1.5)) / 2.0
        halve = testable & (sub > SUBHARMONIC_FLOOR * np.maximum(strength, 1e-12))
        if not np.any(halve):
            break
        out = np.where(halve, out / 2.0, out)
    return out


def median_filter(hz: np.ndarray, width: int = 5) -> np.ndarray:
    """Remove isolated octave slips without smearing real note boundaries.

    A voice tracker's errors are spiky -- one frame an octave out -- while its
    real movement is gradual except at note changes. A short median keeps the
    steps and drops the spikes; a mean would round off both.
    """
    if hz.size < width or width < 3:
        return hz
    pad = width // 2
    padded = np.pad(hz, pad, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(padded, width)
    with np.errstate(invalid="ignore"):
        out = np.nanmedian(win, axis=1)
    # A frame the tracker called unvoiced stays unvoiced; the filter is only
    # allowed to correct pitch, never to invent voicing.
    return np.where(np.isfinite(hz), out, np.nan)


def cents_between(a_hz: np.ndarray, b_hz: np.ndarray) -> np.ndarray:
    """Signed interval in cents from b to a. NaN wherever either is unvoiced."""
    a = np.asarray(a_hz, dtype=np.float64)
    b = np.asarray(b_hz, dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        return 1200.0 * np.log2(a / b)


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def hz_to_note_name(hz: float) -> Optional[str]:
    if not np.isfinite(hz) or hz <= 0:
        return None
    midi = int(round(69 + 12 * np.log2(hz / 440.0)))
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


@dataclass
class NoteSpan:
    """A sung note, as detected rather than as written."""

    note: str
    midi: int
    start_ms: float
    end_ms: float
    hz: float
    confidence: float

    @property
    def duration_ms(self) -> float:
        return self.end_ms - self.start_ms

    def as_dict(self) -> Dict[str, object]:
        return {
            "note": self.note,
            "midi": self.midi,
            "start_ms": round(self.start_ms, 1),
            "duration_ms": round(self.duration_ms, 1),
            "hz": round(self.hz, 2),
            "confidence": round(self.confidence, 3),
        }


def segment_notes(
    contour: Contour,
    min_duration_ms: float = 90.0,
    merge_gap_ms: float = 70.0,
    smooth_ms: float = 70.0,
) -> List[NoteSpan]:
    """Group a contour into discrete notes by rounding to the nearest semitone.

    Rounding frame by frame does not survive a real voice. Vibrato of a third
    of a semitone sits right on the rounding boundary, so a single sustained
    note flickers between two names and comes out as a dozen notes; and a
    breath or a consonant drops a frame out mid-note, splitting it in two.
    So the semitone decision is median-filtered over about a vibrato period,
    runs separated by short gaps are re-joined, and fragments too short to be
    a note are dropped -- a 30 ms excursion through the semitone above is a
    portamento passing through, not a note.
    """
    if contour.hz.size == 0:
        return []
    hop = contour.hop_ms
    hz = median_filter(contour.hz, width=max(int(round(smooth_ms / hop)) | 1, 3))
    midi = np.full(hz.shape, np.nan)
    voiced = np.isfinite(hz)
    with np.errstate(invalid="ignore"):
        midi[voiced] = np.round(69 + 12 * np.log2(hz[voiced] / 440.0))
    midi = median_filter(midi, width=max(int(round(smooth_ms / hop)) | 1, 3))
    midi = np.where(np.isfinite(midi), np.round(midi), np.nan)

    runs: List[List[int]] = []
    n = midi.size
    i = 0
    while i < n:
        if not np.isfinite(midi[i]):
            i += 1
            continue
        j = i
        while j + 1 < n and midi[j + 1] == midi[i]:
            j += 1
        runs.append([i, j])
        i = j + 1

    merged: List[List[int]] = []
    for run in runs:
        if merged and midi[run[0]] == midi[merged[-1][0]]:
            gap = contour.time_ms[run[0]] - (contour.time_ms[merged[-1][1]] + hop)
            if gap <= merge_gap_ms:
                merged[-1][1] = run[1]
                continue
        merged.append(run)

    spans: List[NoteSpan] = []
    for a, b in merged:
        start, end = float(contour.time_ms[a]), float(contour.time_ms[b] + hop)
        if end - start < min_duration_ms:
            continue
        seg = hz[a : b + 1]
        if not np.any(np.isfinite(seg)):
            continue
        centre = float(np.nanmedian(seg))
        spans.append(
            NoteSpan(
                note=hz_to_note_name(centre) or "?",
                midi=int(midi[a]),
                start_ms=start,
                end_ms=end,
                hz=centre,
                confidence=float(np.nanmean(contour.confidence[a : b + 1])),
            )
        )
    return spans
