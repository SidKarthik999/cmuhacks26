"""Timing alignment between two takes.

Three stages, in order, because each one fixes what the next cannot:

1. `estimate_offset_ms` -- cross-correlate onset envelopes to find a constant
   lag. Cheap, robust, and the only stage the concert feature needs.
2. `scan_offset_tempo` -- search (offset, tempo) jointly. A student who
   starts late *and* drifts faster is not describable by a single lag.
3. `align` -- banded DTW centred on the line found in stage 2, giving a full
   warp path for takes whose tempo wanders within the phrase.

Feature choice matters more than the algorithm. Two singers in thirds share
rhythm but not pitch classes, so chroma/mel features mislead the warp while
onset envelopes do not. `feature="rhythm"` selects onsets; `feature="mel"`
selects log-mel, for takes of the same line.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

from . import dsp

ONSET_HOP_MS = 5.0
MAX_OFFSET_MS = 1500.0
TEMPO_RANGE = (0.78, 1.28)


CORR_SMOOTH_MS = 25.0


def _smooth_env(env: np.ndarray, hop_ms: float = ONSET_HOP_MS, width_ms: float = CORR_SMOOTH_MS) -> np.ndarray:
    """Widen onset spikes before correlating them.

    A raw onset envelope is nearly a spike train. Correlating two spike
    trains is all-or-nothing: when the takes differ in tempo, note three
    lines up and note six is 30 ms out, contributing nothing, and the peak
    that wins can be a coincidence between unrelated notes. Blurring each
    onset over a couple of frames makes a near miss count as a near miss, and
    it is also the honest model of the question being asked -- a singer's
    attack is not an instant.
    """
    sigma = max(width_ms / hop_ms, 0.5)
    half = max(int(round(3 * sigma)), 1)
    t = np.arange(-half, half + 1)
    kernel = np.exp(-0.5 * (t / sigma) ** 2)
    kernel /= kernel.sum()
    if env.size < kernel.size:
        return env
    return np.convolve(env, kernel, mode="same")


def _xcorr_peak(
    a: np.ndarray, b: np.ndarray, max_lag: int, min_lag: Optional[int] = None
) -> Tuple[int, float]:
    """Lag (in frames) that best aligns b onto a, plus a 0..1 confidence.

    Positive lag means b happens *later* than a.
    """
    if a.size == 0 or b.size == 0:
        return 0, 0.0
    if min_lag is None:
        min_lag = -max_lag
    a = a - a.mean()
    b = b - b.mean()
    n = max(a.size, b.size)
    a = np.pad(a, (0, n - a.size))
    b = np.pad(b, (0, n - b.size))
    size = int(2 ** np.ceil(np.log2(2 * n)))
    fa = np.fft.rfft(a, size)
    fb = np.fft.rfft(b, size)
    # conj(fa), not conj(fb): this orientation puts the peak at +D when b is
    # a delayed by D, matching the "positive lag means b is later" contract.
    cc = np.fft.irfft(fb * np.conj(fa), size)
    cc = np.concatenate([cc[-max_lag:], cc[: max_lag + 1]])
    lags = np.arange(-max_lag, max_lag + 1)
    keep = (lags >= min_lag) & (lags <= max_lag)
    if not np.any(keep):
        return 0, 0.0
    cc = cc[keep]
    lags = lags[keep]
    best = int(np.argmax(cc))
    denom = np.sqrt((a ** 2).sum() * (b ** 2).sum())
    conf = float(cc[best] / denom) if denom > 0 else 0.0
    return int(lags[best]), float(np.clip(conf, 0.0, 1.0))


def estimate_offset_ms(
    ref: np.ndarray,
    other: np.ndarray,
    sr: int = 22050,
    max_offset_ms: float = MAX_OFFSET_MS,
    min_offset_ms: Optional[float] = None,
) -> Tuple[float, float]:
    """Constant lag of `other` relative to `ref`, in ms, plus confidence.

    Positive means `other` is late. This is the measurement the concert
    feature's lead-advance control is derived from.
    """
    env_a = dsp.onset_envelope(ref, sr=sr, hop_ms=ONSET_HOP_MS)
    env_b = dsp.onset_envelope(other, sr=sr, hop_ms=ONSET_HOP_MS)
    max_lag = int(max_offset_ms / ONSET_HOP_MS)
    min_lag = None if min_offset_ms is None else int(min_offset_ms / ONSET_HOP_MS)
    lag, conf = _xcorr_peak(env_a, env_b, max_lag, min_lag)
    return lag * ONSET_HOP_MS, conf


def scan_offset_tempo(
    ref: np.ndarray,
    other: np.ndarray,
    sr: int = 22050,
    max_offset_ms: float = MAX_OFFSET_MS,
    tempo_range: Tuple[float, float] = TEMPO_RANGE,
    tempo_steps: int = 41,
) -> Tuple[float, float, float]:
    """Joint (offset_ms, tempo_ratio, confidence) search.

    `tempo_ratio` > 1 means `other` is *faster* than `ref`. For each candidate
    tempo the other take is resampled in the onset domain and the best lag is
    found by cross-correlation; the pair with the highest correlation wins.
    """
    env_a = _smooth_env(dsp.onset_envelope(ref, sr=sr, hop_ms=ONSET_HOP_MS))
    env_b = _smooth_env(dsp.onset_envelope(other, sr=sr, hop_ms=ONSET_HOP_MS))
    if env_a.size < 4 or env_b.size < 4:
        return 0.0, 1.0, 0.0

    max_lag = int(max_offset_ms / ONSET_HOP_MS)
    best = (0.0, 1.0, -1.0)
    for ratio in np.linspace(tempo_range[0], tempo_range[1], tempo_steps):
        n = max(int(round(env_b.size * ratio)), 4)
        warped = np.interp(
            np.linspace(0, env_b.size - 1, n), np.arange(env_b.size), env_b
        )
        lag, conf = _xcorr_peak(env_a, warped, max_lag)
        if conf > best[2]:
            best = (lag * ONSET_HOP_MS, float(ratio), conf)
    return best


@dataclass
class Alignment:
    """How to map `other`'s timeline onto `ref`'s."""

    offset_ms: float
    tempo_ratio: float
    confidence: float
    method: str
    path: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), dtype=int))
    hop_ms: float = ONSET_HOP_MS
    # Knots are the canonical form of the warp: reference time -> source time,
    # monotone and piecewise linear. The raw `path` is kept for display, but
    # knots are what gets evaluated, inverted, and composed.
    knot_ref_ms: np.ndarray = field(default_factory=lambda: np.zeros(0))
    knot_src_ms: np.ndarray = field(default_factory=lambda: np.zeros(0))

    @property
    def span_ms(self) -> float:
        if self.knot_ref_ms.size:
            return float(self.knot_ref_ms[-1])
        return 0.0

    def inverse_ms(self, times_ms: Sequence[float]) -> np.ndarray:
        """Where each time on `ref`'s clock came from in `other`."""
        times = np.asarray(times_ms, dtype=np.float64)
        if self.knot_ref_ms.size < 2:
            return times / max(self.tempo_ratio, 1e-6) + self.offset_ms
        kx, ky = self.knot_ref_ms, self.knot_src_ms
        out = np.interp(times, kx, ky)
        slope = float(
            np.clip((ky[-1] - ky[0]) / max(kx[-1] - kx[0], 1e-6), 0.5, 2.0)
        )
        lo, hi = times < kx[0], times > kx[-1]
        out[lo] = ky[0] + (times[lo] - kx[0]) * slope
        out[hi] = ky[-1] + (times[hi] - kx[-1]) * slope
        return out

    def map_ms(self, times_ms: Sequence[float]) -> np.ndarray:
        """Where each time in `other` lands on `ref`'s clock."""
        times = np.asarray(times_ms, dtype=np.float64)
        if self.knot_ref_ms.size < 2:
            return (times - self.offset_ms) * self.tempo_ratio
        return np.interp(times, self.knot_src_ms, self.knot_ref_ms)

    def compose(self, second: "Alignment") -> "Alignment":
        """Chain a refinement pass onto this one.

        `self` maps reference time to a place in the raw take; `second` maps
        reference time to a place in the *already warped* take. Composing them
        gives one mapping straight from reference time into the raw take, so
        the refinement costs an extra measurement but not an extra resampling
        pass -- audio only ever gets vocoded once.
        """
        kx = self.knot_ref_ms if self.knot_ref_ms.size >= 2 else second.knot_ref_ms
        if kx.size < 2:
            return self
        composed = self.inverse_ms(second.inverse_ms(kx))
        return Alignment(
            offset_ms=float(composed[0] - kx[0]),
            tempo_ratio=self.tempo_ratio,
            confidence=min(self.confidence, second.confidence),
            method=self.method + "+refine",
            path=self.path,
            hop_ms=self.hop_ms,
            knot_ref_ms=kx,
            knot_src_ms=np.maximum.accumulate(composed),
        )

    def drift_series(self, points: int = 16, span_ms: Optional[float] = None) -> List[dict]:
        """Residual timing error sampled across the take.

        Drift is what the duet UI plots. The constant part is just "one singer
        came in late", which the offset already reports, so it is removed here:
        what is left is the part a fixed delay cannot fix.
        """
        if span_ms is None:
            span_ms = self.span_ms or 4000.0
        grid = np.linspace(0.0, max(span_ms, 1.0), points)
        mapped = self.inverse_ms(grid) - grid
        mapped = mapped - mapped[0]
        return [
            {"t_ms": round(float(t), 1), "drift_ms": round(float(m), 2)}
            for t, m in zip(grid, mapped)
        ]


ONSET_WEIGHT = 0.6


def _features(x: np.ndarray, sr: int, feature: str) -> np.ndarray:
    """Frame features at ONSET_HOP_MS resolution, L2-normalised per frame.

    Three choices, because no single one covers the cases the product has:

    ``mel``
        Spectral shape. Sharpest when the two takes are the same line in the
        same range -- a teacher and a student of the same voice type.
    ``chroma``
        Pitch class, octave-invariant. A bass singing the teacher's alto line
        an octave and a half down has almost no mel overlap with it but
        near-identical chroma.
    ``hybrid`` (default)
        Chroma plus the onset envelope. Chroma says *which note*, onsets say
        *when it started*; a sustained note is ambiguous under chroma alone
        and the onset channel breaks the tie. This is the only one of the
        three that does not fail on some case in the check suite.
    ``rhythm``
        Onsets only. For takes that share rhythm but not pitch content, such
        as two singers deliberately in thirds.
    """
    hop = max(int(sr * ONSET_HOP_MS / 1000.0), 1)
    env = dsp.onset_envelope(x, sr=sr, hop_ms=ONSET_HOP_MS)

    if feature == "rhythm":
        # Stack the envelope with a smoothed copy so the DTW cost has some
        # local shape to lock onto rather than a single scalar per frame.
        smooth = np.convolve(env, np.ones(5) / 5.0, mode="same")
        feats = np.stack([env, smooth], axis=1)
    elif feature == "mel":
        feats = dsp.melspectrogram(x, sr=sr, n_fft=1024, hop=hop, n_mels=24)
    else:
        chroma = dsp.chromagram(x, sr=sr, n_fft=2048, hop=hop)
        if feature == "chroma":
            feats = chroma
        else:
            n = min(chroma.shape[0], env.size)
            feats = np.concatenate(
                [chroma[:n], (ONSET_WEIGHT * env[:n])[:, None]], axis=1
            )

    norm = np.linalg.norm(feats, axis=1, keepdims=True)
    return feats / np.maximum(norm, 1e-8)


START_PENALTY = 0.02


def _banded_dtw(
    a: np.ndarray,
    b: np.ndarray,
    centre_slope: float,
    centre_offset: float,
    band: int,
    start_penalty: float = START_PENALTY,
) -> Tuple[np.ndarray, float]:
    """DTW restricted to a band around j = slope*i + offset.

    Restricting the search is not only a speed trick: an unconstrained warp
    on near-periodic material happily matches note 3 to note 7. The band comes
    from stage 2's global estimate, so the path can only bend locally.
    """
    n, m = a.shape[0], b.shape[0]
    if n == 0 or m == 0:
        return np.zeros((0, 2), dtype=int), 1.0

    cost = np.full((n + 1, m + 1), np.inf)
    dist = 1.0 - (a @ b.T)  # cosine distance, features are unit-norm

    lo_all = np.clip((centre_slope * np.arange(n) + centre_offset - band).astype(int), 0, m - 1)
    hi_all = np.clip((centre_slope * np.arange(n) + centre_offset + band).astype(int), 0, m - 1)

    # Free start: the path may begin anywhere in the first row's band. With a
    # fixed cost[0, 0] = 0 origin the band is unreachable whenever the takes
    # start at different times, which is precisely the case we are solving.
    #
    # The start is not free of charge, though. Inside one sustained note every
    # frame looks alike, so a truly free start will happily enter halfway
    # through the first note -- the pitches still match, the cost is the same,
    # and the resampler then has to cram the remainder of that note into a
    # whole beat. A penalty growing with distance from the globally estimated
    # offset breaks that tie toward the answer stage 2 already found, while
    # still letting real evidence override it. Scaling by path length keeps
    # the penalty commensurate with the total cost it competes against.
    js = np.arange(lo_all[0] + 1, min(hi_all[0] + 2, m + 1))
    if js.size == 0:
        js = np.array([1])
    cost[0, js] = (
        start_penalty * n * np.abs((js - 1) - centre_offset) / max(band, 1)
    )

    for i in range(n):
        lo, hi = lo_all[i], hi_all[i]
        for j in range(lo, hi + 1):
            d = dist[i, j]
            prev = min(cost[i, j], cost[i, j + 1], cost[i + 1, j])
            if prev == np.inf:
                continue
            cost[i + 1, j + 1] = d + prev

    # Backtrack from the cheapest end cell inside the band.
    j_end = int(np.argmin(cost[n, 1:])) + 1
    if not np.isfinite(cost[n, j_end]):
        return np.zeros((0, 2), dtype=int), 1.0

    path = []
    i, j = n, j_end
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        step = int(np.argmin([cost[i - 1, j - 1], cost[i - 1, j], cost[i, j - 1]]))
        if step == 0:
            i, j = i - 1, j - 1
        elif step == 1:
            i -= 1
        else:
            j -= 1
    path.reverse()
    arr = np.array(path, dtype=int) if path else np.zeros((0, 2), dtype=int)
    if arr.size == 0:
        return arr, 1.0
    # Mean of the distances actually traversed, not the accumulated cost: the
    # latter carries the start penalty, which would show up as a worse match.
    mean_cost = float(dist[arr[:, 0], arr[:, 1]].mean())
    return arr, mean_cost


def align(
    ref: np.ndarray,
    other: np.ndarray,
    sr: int = 22050,
    feature: str = "mel",
    band_ms: float = 260.0,
) -> Alignment:
    """Full alignment: global (offset, tempo) then a banded DTW refinement."""
    offset_ms, tempo, conf = scan_offset_tempo(ref, other, sr=sr)

    fa = _features(ref, sr, feature)
    fb = _features(other, sr, feature)
    if fa.shape[0] < 4 or fb.shape[0] < 4:
        return Alignment(offset_ms, tempo, conf, "offset_tempo")

    # other_frame = ref_frame / tempo + offset_frames. Using `tempo` as the
    # slope instead of its reciprocal walks the band off the true path by
    # (tempo - 1/tempo) * duration, which at 1.07 over four seconds is half a
    # second -- far outside any sane band, so the path gets dragged.
    offset_frames = offset_ms / ONSET_HOP_MS
    band = max(int(band_ms / ONSET_HOP_MS), 8)
    slope = 1.0 / max(tempo, 1e-6)
    path, mean_cost = _banded_dtw(fa, fb, slope, offset_frames, band)
    dtw_conf = float(np.clip(1.0 - mean_cost, 0.0, 1.0))

    if path.shape[0] < 4:
        return Alignment(offset_ms, tempo, conf, "offset_tempo")
    al = Alignment(
        offset_ms=offset_ms,
        tempo_ratio=tempo,
        confidence=max(conf, dtw_conf),
        method="banded_dtw",
        path=path,
        hop_ms=ONSET_HOP_MS,
    )
    kx, ky = warp_knots(al)
    al.knot_ref_ms, al.knot_src_ms = kx, ky
    return al


def align_and_warp(
    ref: np.ndarray,
    other: np.ndarray,
    sr: int = 22050,
    feature: str = "mel",
    passes: int = 5,
    tol_ms: float = 15.0,
) -> Tuple[np.ndarray, Alignment]:
    """Align, then warp `other` onto `ref`'s clock. The pair most callers want.

    Each pass measures the warp against the *output* of the previous one and
    composes the two mappings, so refinement costs an extra measurement but
    never an extra resampling: the audio that ships is vocoded exactly once,
    from the composed mapping. A single pass lands within a few frames mid
    phrase and drifts at the edges, where there is context on only one side;
    measuring the residual is the cheapest way to see that.

    Passes stop as soon as one fails to improve the worst windowed residual,
    and the best result so far is what gets returned -- a low-confidence pass
    on a take with little onset content can otherwise undo a good one.
    """
    al = align(ref, other, sr=sr, feature=feature)
    best = warp_to_reference(other, al, ref.size, sr=sr)
    best_al = al
    best_err = _worst_window_ms(ref, best, sr=sr)

    for _ in range(max(passes - 1, 0)):
        # Below tolerance there is nothing left to measure: the onset hop is
        # 5 ms and the windows are coarse, so another pass would be fitting
        # measurement noise, and fitting noise costs real audio quality.
        if best_err <= tol_ms:
            break
        stepped = _correct_from_residual(ref, best, best_al, sr=sr)
        if stepped is None:
            break
        candidate = warp_to_reference(other, stepped, ref.size, sr=sr)
        err = _worst_window_ms(ref, candidate, sr=sr)
        if err >= best_err - 1e-6:
            break
        best, best_al, best_err = candidate, stepped, err
    return best, best_al


RESIDUAL_WIN_MS = 600.0
RESIDUAL_HOP_MS = 200.0
RESIDUAL_CLAMP_MS = 180.0


def _correct_from_residual(
    ref: np.ndarray,
    warped: np.ndarray,
    alignment: Alignment,
    sr: int = 22050,
    min_conf: float = 0.25,
) -> Optional[Alignment]:
    """Nudge a warp by the timing error its own output still shows.

    Re-running the DTW on the warped take is the obvious refinement and the
    wrong one: it re-derives the whole mapping from scratch, so a pass can
    lose ground the previous pass had won. Measuring the residual lag in
    overlapping windows and adding it back to the knots corrects exactly the
    quantity being complained about, and only where the measurement is
    confident enough to believe.
    """
    if alignment.knot_ref_ms.size < 2:
        return None

    win = int(RESIDUAL_WIN_MS * sr / 1000.0)
    hop = max(int(RESIDUAL_HOP_MS * sr / 1000.0), 1)
    if ref.size < win:
        return None

    centres, lags, weights = [], [], []
    for a in range(0, ref.size - win, hop):
        d, c = estimate_offset_ms(
            ref[a : a + win], warped[a : a + win], sr=sr, max_offset_ms=250.0
        )
        centres.append((a + win / 2.0) * 1000.0 / sr)
        lags.append(float(np.clip(d, -RESIDUAL_CLAMP_MS, RESIDUAL_CLAMP_MS)))
        weights.append(float(c))
    if len(centres) < 2:
        return None

    cx = np.asarray(centres)
    cy = np.asarray(lags)
    cw = np.asarray(weights)
    keep = cw >= min_conf
    if keep.sum() < 2:
        return None
    # Confidence-weighted blend toward zero: an unsure window should move the
    # knots a little, not not at all, and never more than it measured.
    cy = cy * np.clip(cw, 0.0, 1.0)
    cx, cy = cx[keep], cy[keep]

    kx = alignment.knot_ref_ms
    delta = np.interp(kx, cx, cy, left=cy[0], right=cy[-1])
    src = np.maximum.accumulate(alignment.knot_src_ms + delta)
    return Alignment(
        offset_ms=float(src[0] - kx[0]),
        tempo_ratio=alignment.tempo_ratio,
        confidence=alignment.confidence,
        method=alignment.method if "+resid" in alignment.method else alignment.method + "+resid",
        path=alignment.path,
        hop_ms=alignment.hop_ms,
        knot_ref_ms=kx,
        knot_src_ms=src,
    )


def _worst_window_ms(
    ref: np.ndarray, other: np.ndarray, sr: int = 22050, win_ms: float = 800.0
) -> float:
    """Largest local timing error, which is what an ensemble actually notices.

    A global offset can read as zero while bar three is a beat out, so the
    refinement loop is steered by the worst window rather than the mean.
    """
    win = int(win_ms * sr / 1000.0)
    if ref.size < win:
        return abs(estimate_offset_ms(ref, other, sr=sr, max_offset_ms=300.0)[0])
    worst = 0.0
    for a in range(0, ref.size - win, max(win // 2, 1)):
        d, _ = estimate_offset_ms(
            ref[a : a + win], other[a : a + win], sr=sr, max_offset_ms=300.0
        )
        worst = max(worst, abs(d))
    return worst


WARP_N_FFT = 1024
WARP_HOP = 256


KNOT_MS = 250.0


def warp_knots(alignment: Alignment, knot_ms: float = KNOT_MS) -> Tuple[np.ndarray, np.ndarray]:
    """Reduce the DTW path to a monotone piecewise-linear curve.

    The raw path is a staircase: horizontal runs mean "several reference
    frames matched one source frame", which read literally is a local rate of
    zero -- a resampler turns that into a held buffer, and the held buffers
    alternating with catch-up runs are heard as tremolo. Averaging the path
    does not help much either, because the staircase noise is not symmetric.

    Taking the *median* source time inside each reference bin does help: it
    ignores the dwell length and reports where the bin actually matched. Bins
    of a quarter second give two knots per note at typical tempos, which is
    enough to follow a real rubato but too coarse to chase frame noise.
    """
    ref_ms = alignment.path[:, 0].astype(np.float64) * alignment.hop_ms
    oth_ms = alignment.path[:, 1].astype(np.float64) * alignment.hop_ms
    span = ref_ms[-1] - ref_ms[0]
    n_bins = max(int(round(span / knot_ms)), 2)
    edges = np.linspace(ref_ms[0], ref_ms[-1], n_bins + 1)
    which = np.clip(np.searchsorted(edges, ref_ms, side="right") - 1, 0, n_bins - 1)

    knot_x, knot_y = [], []
    for b in range(n_bins):
        sel = which == b
        if not np.any(sel):
            continue
        knot_x.append(float(np.median(ref_ms[sel])))
        knot_y.append(float(np.median(oth_ms[sel])))
    if len(knot_x) < 2:
        return ref_ms[[0, -1]], oth_ms[[0, -1]]
    kx = np.asarray(knot_x)
    ky = np.maximum.accumulate(np.asarray(knot_y))
    return kx, ky


def source_time_ms(alignment: Alignment, ref_times_ms: np.ndarray) -> np.ndarray:
    """For each reference time, the time in `other` that belongs there."""
    return alignment.inverse_ms(ref_times_ms)


def warp_to_reference(
    other: np.ndarray, alignment: Alignment, target_len: int, sr: int = 22050
) -> np.ndarray:
    """Resample `other` onto the reference timeline without transposing.

    One variable-rate phase-vocoder pass over the whole take. The earlier
    piecewise version stretched segments independently and concatenated them;
    every seam became a step in phase, and an onset detector read those steps
    as extra notes (18 onsets where the line has 8). A single overlap-add pass
    has no seams.
    """
    if other.size == 0:
        return np.zeros(target_len, dtype=np.float32)

    hop, n_fft = WARP_HOP, WARP_N_FFT
    n_out = max(int(np.ceil(target_len / hop)) + 1, 4)
    ref_ms = np.arange(n_out, dtype=np.float64) * hop * 1000.0 / sr
    src_ms = source_time_ms(alignment, ref_ms)

    # A resampler needs a monotone path with a bounded rate; clamp the local
    # slope rather than the values so bends survive but reversals cannot. The
    # first sample is carried over untouched -- folding it into the cumulative
    # sum shifts the whole take by half a frame, which is enough to land every
    # read halfway between two analysis frames and smear every attack.
    nominal = hop * 1000.0 / sr
    step = np.clip(np.diff(src_ms), 0.5 * nominal, 2.0 * nominal)
    src_ms = np.concatenate([src_ms[:1], src_ms[0] + np.cumsum(step)])

    src_frames = src_ms * sr / 1000.0 / hop
    warped = dsp.time_warp(other, src_frames, n_fft=n_fft, hop=hop)

    # Reference positions that predate the source recording are silence, not a
    # held first frame.
    out = dsp.pad_to(warped, target_len)
    lead = int(np.searchsorted(src_ms, 0.0) * hop)
    if lead > 0:
        out[: min(lead, out.size)] = 0.0
    return out


def residual_offset_ms(ref: np.ndarray, aligned: np.ndarray, sr: int = 22050) -> Tuple[float, float]:
    """Offset still remaining after warping -- the honest 'after' number."""
    return estimate_offset_ms(ref, aligned, sr=sr, max_offset_ms=400.0)
