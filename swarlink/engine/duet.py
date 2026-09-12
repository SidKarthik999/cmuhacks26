"""Feature 2: two people who could not hear each other, mixed into one take.

On a call, two singers cannot sing together. Every conferencing tool suppresses
one side while the other talks, because that is the right behaviour for
speech, and the tens of milliseconds of network latency that nobody notices in
conversation are a quarter of a beat. So the honest way to rehearse remotely is
to stop pretending: both people sing alone, at their own pace, into their own
microphone, and the two takes are put together afterwards.

Which turns a real-time problem into a measurement problem, and measurement is
tractable. This module does four things:

1. **Matches loudness.** One singer close to a laptop and one across a room
   differ by 15 dB, and the quieter one simply vanishes in a plain sum. Both
   takes are normalised to a common loudness (K-weighted, BS.1770 shape)
   before mixing, so the balance is musical rather than architectural.
2. **Aligns the second singer to the first**, recovering their entry
   difference and their tempo difference and warping one onto the other's
   clock without transposing it.
3. **Mixes both ways.** The corrected mix is the product; the uncorrected sum
   is kept deliberately, because "here is what you would have got" is the
   only way to show what the correction did.
4. **Reports whether they were actually in sync** -- which is the question the
   singers care about, and it is not the same as whether the mix sounds
   aligned. The mix always sounds aligned; that is what the aligner is for.

The distinction that makes the sync report meaningful: **a different starting
time is not a rhythm fault, and a different tempo is.** Two people recording
alone have no shared downbeat, so the entry offset is an artefact of the
arrangement and is reported separately. What is left after removing it -- the
tempo difference, and the wobble that remains after removing that too -- is
the part that says something about their time-keeping.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import align, dsp, lesson, metrics, pitch

SAMPLE_RATE = 22050
MIX_TARGET_LUFS = -20.0
DISPLAY_POINTS = 900

# Intervals a duet is plausibly singing, for naming the harmonic relationship.
# Tolerance is deliberately wide: a real singer holds a third to within a
# quarter tone, and the point of the label is to say "you were in thirds",
# not to grade the thirds.
INTERVALS: Tuple[Tuple[float, str], ...] = (
    (0.0, "unison"),
    (100.0, "a semitone apart"),
    (200.0, "a tone apart"),
    (300.0, "a minor third apart"),
    (400.0, "a major third apart"),
    (500.0, "a fourth apart"),
    (700.0, "a fifth apart"),
    (800.0, "a minor sixth apart"),
    (900.0, "a major sixth apart"),
    (1200.0, "an octave apart"),
    (1900.0, "an octave and a fifth apart"),
    (2400.0, "two octaves apart"),
)
INTERVAL_TOLERANCE_CENTS = 50.0
# Above this 90th-percentile spread the pair are not holding one interval, so
# the report stops naming one.
INTERVAL_STEADY_CENTS = 60.0
# Beyond this the pitch track is too scattered for the interval to mean
# anything, and the report says so instead of naming one.
INTERVAL_UNRELIABLE_CENTS = 300.0


def name_interval(cents: float) -> Dict[str, Any]:
    """Name the interval between two voices, with how close it was to true."""
    best, label = min(INTERVALS, key=lambda a: abs(a[0] - abs(cents)))
    error = abs(cents) - best if cents >= 0 else -(abs(cents) - best)
    return {
        "cents": round(float(cents), 1),
        "nearest_interval_cents": best,
        "label": label,
        "error_cents": round(float(error), 1),
        "in_tune": bool(abs(abs(cents) - best) <= INTERVAL_TOLERANCE_CENTS),
    }


@dataclass
class DuetResult:
    singers: List[lesson.TakeView]
    mix: np.ndarray
    naive_mix: np.ndarray
    aligned: List[np.ndarray]
    gains_db: List[float]
    sync: Dict[str, Any] = field(default_factory=dict)
    harmony: Dict[str, Any] = field(default_factory=dict)
    alignment: Optional[align.Alignment] = None
    sample_rate: int = SAMPLE_RATE

    def as_dict(self, glossary: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "mode": "duet",
            "singers": [
                {
                    **view.as_dict(),
                    "match_gain_db": round(gain, 2),
                    "aligned_wave": dsp.downsample_envelope(a, DISPLAY_POINTS),
                }
                for view, gain, a in zip(self.singers, self.gains_db, self.aligned)
            ],
            "mix": {
                "wave": dsp.downsample_envelope(self.mix, DISPLAY_POINTS),
                "duration_ms": round(1000.0 * len(self.mix) / self.sample_rate, 1),
                "lufs": round(dsp.loudness_lufs(self.mix, self.sample_rate), 2),
            },
            "naive_mix": {
                "wave": dsp.downsample_envelope(self.naive_mix, DISPLAY_POINTS),
                "duration_ms": round(1000.0 * len(self.naive_mix) / self.sample_rate, 1),
                "note": (
                    "The two takes summed as recorded, with no correction. Kept "
                    "so the corrected mix can be compared against something "
                    "rather than taken on trust."
                ),
            },
            "sync": self.sync,
            "harmony": self.harmony,
        }
        if glossary:
            out["glossary"] = metrics.glossary()
        return out

    def report(self) -> str:
        s = self.sync
        lines = [
            f"Duet    {self.singers[0].name} + {self.singers[1].name}",
            f"  verdict        {s['verdict']}",
            f"  entry gap      {s['entry_offset_ms']:+.0f} ms "
            f"(not a fault -- neither singer could hear the other)",
            f"  pace           {s['tempo_percent']:+.1f}% "
            f"({metrics.timing_in_words(s['drift_ms'])} by the end)",
            f"  tightness      {s['tightness_before_ms']:.0f} ms uncorrected "
            f"-> {s['tightness_after_ms']:.0f} ms aligned",
            f"  rubato spread  {s['rubato_ms']:.0f} ms "
            f"(how much the correction had to bend, beyond a shift and a stretch)",
            f"  harmony        {self.harmony['label']}, "
            f"{self.harmony['error_cents']:+.0f} cents from true",
            f"  levels matched {self.gains_db[0]:+.1f} dB / {self.gains_db[1]:+.1f} dB "
            f"to {MIX_TARGET_LUFS:.0f} LUFS",
        ]
        for c in s.get("caveats", []):
            lines.append(f"  ! {c}")
        return "\n".join(lines)


def _worst_live_window(
    ref: np.ndarray, other: np.ndarray, sr: int
) -> Tuple[float, float]:
    """Largest windowed lag between two takes, ignoring low-confidence windows.

    A window covering a breath in one take and a vowel in the other produces a
    confident-looking number about nothing. Windows the offset estimator is
    not sure about are dropped rather than averaged in.
    """
    windows = align.window_residuals(ref, other, sr=sr)
    live = [(abs(r), c) for _t, r, c in windows if c >= align.WINDOW_MIN_CONF]
    if not live:
        resid, conf = align.estimate_offset_ms(ref, other, sr=sr, max_offset_ms=400.0)
        return abs(resid), conf
    worst = max(live, key=lambda p: p[0])
    return worst[0], float(np.mean([c for _r, c in live]))


def _rubato_ms(alignment: align.Alignment, span_ms: float) -> float:
    """How far the warp departed from a plain shift-and-stretch.

    A constant offset is two people starting at different moments and a
    constant tempo ratio is one singing faster throughout. Anything the
    aligner had to do *beyond* those two is the part where one singer
    stretched a phrase and the other did not -- which is the only component
    of the timing that is about interpretation rather than arithmetic.

    Measured against the best-fit line through the map itself rather than
    against the reported offset and tempo. Those two are the *initial*
    estimate; refinement then adjusts the map without rewriting them, so
    subtracting them leaves a residual slope that is bookkeeping rather than
    musical, and reading its range reported 122 ms of rubato on takes
    generated at a rigidly constant tempo.
    """
    _slope, _intercept, resid = _fit_line(alignment, span_ms)
    return float(np.max(resid) - np.min(resid)) if resid.size else 0.0


def _fit_line(
    alignment: align.Alignment, span_ms: float
) -> Tuple[float, float, np.ndarray]:
    """Least-squares line through the alignment's time map, and its residual.

    The reported `offset_ms` and `tempo_ratio` are the aligner's *initial*
    estimate; refinement then adjusts the map without rewriting them. So the
    honest linear summary of what the map ended up doing is a fit to the map,
    and it is also the more accurate one -- on takes generated at a fixed
    1.050x, the initial estimate reads 1.054 to 1.056 while the fitted slope
    recovers 1.050.
    """
    # Fit over the stretch the aligner actually measured. Outside the knots the
    # map is a linear extrapolation with no evidence behind it, and including
    # the silent lead-in and tail pulled the recovered tempo from 5.3% to 6.1%
    # on a take whose true figure was 5.0%.
    lo, hi = 0.0, max(span_ms, 1.0)
    if alignment.knot_ref_ms.size >= 2:
        lo = float(alignment.knot_ref_ms[0])
        hi = float(alignment.knot_ref_ms[-1])
    grid = np.linspace(lo, hi, 96)
    actual = alignment.inverse_ms(grid)
    if grid.size < 3 or hi - lo < 1.0:
        return 1.0 / max(alignment.tempo_ratio, 1e-6), alignment.offset_ms, np.zeros(0)
    design = np.vstack([grid, np.ones_like(grid)]).T
    slope, intercept = np.linalg.lstsq(design, actual, rcond=None)[0]
    return float(slope), float(intercept), actual - (slope * grid + intercept)


def run(
    audio_a: np.ndarray,
    audio_b: np.ndarray,
    sr: int = SAMPLE_RATE,
    clean: bool = True,
    name_a: str = "Singer A",
    name_b: str = "Singer B",
    target_lufs: float = MIX_TARGET_LUFS,
) -> DuetResult:
    """Align, level-match and mix two independently recorded takes."""
    a_view = lesson.prepare(audio_a, name_a, "singer", sr, clean)
    b_view = lesson.prepare(audio_b, name_b, "singer", sr, clean)

    a_norm, gain_a = dsp.normalize_to_lufs(a_view.audio, target_lufs, sr)
    b_norm, gain_b = dsp.normalize_to_lufs(b_view.audio, target_lufs, sr)

    warped, alignment = align.align_and_warp(a_norm, b_norm, sr=sr)
    length = max(a_norm.size, warped.size)
    a_pad = dsp.pad_to(a_norm, length)
    b_pad = dsp.pad_to(warped, length)

    mix = dsp.mix([a_pad, b_pad])
    naive_len = max(a_norm.size, b_norm.size)
    naive_mix = dsp.mix([dsp.pad_to(a_norm, naive_len), dsp.pad_to(b_norm, naive_len)])

    # "Before" is measured after removing the entry offset only, because the
    # offset is an artefact of two people pressing record at different times.
    # Leaving it in would make the correction look good for the wrong reason.
    shifted = dsp.pad_to(dsp.shift_ms(b_norm, -alignment.offset_ms, sr), a_norm.size)
    before_ms, _ = _worst_live_window(a_norm, shifted, sr)
    after_ms, after_conf = _worst_live_window(a_pad, b_pad, sr)

    span_ms = 1000.0 * a_norm.size / sr
    drift = alignment.drift_series(points=48, span_ms=span_ms)
    drift_ms = max((abs(d["drift_ms"]) for d in drift), default=0.0)
    slope, entry_ms, resid = _fit_line(alignment, span_ms)
    effective_tempo = 1.0 / max(slope, 1e-6)
    rubato = float(np.max(resid) - np.min(resid)) if resid.size else 0.0

    caveats: List[str] = []
    if alignment.confidence < 0.45:
        caveats.append(
            "Few clear attacks to lock onto, so the sync numbers are weak "
            "evidence rather than measurements."
        )
    if min(a_view.contour.voiced_fraction, b_view.contour.voiced_fraction) < 0.5:
        caveats.append(
            "One take is under half voiced, so the harmony reading covers only "
            "part of it."
        )

    sync = {
        "entry_offset_ms": round(entry_ms, 1),
        "tempo_ratio": round(effective_tempo, 4),
        "tempo_percent": round(100.0 * (effective_tempo - 1.0), 2),
        "scan_tempo_ratio": round(alignment.tempo_ratio, 4),
        "drift_ms": round(drift_ms, 1),
        "rubato_ms": round(rubato, 1),
        "tightness_before_ms": round(before_ms, 1),
        "tightness_after_ms": round(after_ms, 1),
        "improvement_ms": round(before_ms - after_ms, 1),
        "confidence": round(max(alignment.confidence, after_conf), 3),
        "verdict": _verdict(drift_ms, rubato, effective_tempo),
        "drift": drift,
        "warp": alignment.warp_series(points=48, span_ms=span_ms),
        "windows": [
            {"t": round(float(t), 1), "residual_ms": round(float(r), 1),
             "confidence": round(float(c), 3)}
            for t, r, c in align.window_residuals(a_pad, b_pad, sr=sr)
        ],
        "caveats": caveats,
        "note": (
            "Starting at different times is not a rhythm fault -- neither "
            "singer could hear the other, so there was no downbeat to share. "
            "The tempo difference and the rubato spread are the parts that say "
            "something about time-keeping."
        ),
    }

    harmony = _harmony(a_view.contour, b_view.contour, alignment)

    return DuetResult(
        singers=[a_view, b_view],
        mix=mix,
        naive_mix=naive_mix,
        aligned=[a_pad, b_pad],
        gains_db=[gain_a, gain_b],
        sync=sync,
        harmony=harmony,
        alignment=alignment,
        sample_rate=sr,
    )


def _verdict(drift_ms: float, rubato_ms: float, tempo_ratio: float) -> str:
    """One sentence on whether the two were actually together."""
    pct = abs(100.0 * (tempo_ratio - 1.0))
    if drift_ms < 40.0 and rubato_ms < 40.0:
        return "In sync -- same pace, same phrasing, only the start times differed."
    if pct >= 3.0 and rubato_ms < 60.0:
        faster = "second" if tempo_ratio > 1.0 else "first"
        return (
            f"Steady but at different paces: the {faster} singer was {pct:.0f}% "
            f"quicker throughout, drifting {drift_ms:.0f} ms apart by the end."
        )
    if rubato_ms >= 60.0:
        return (
            f"Pace broadly matched, but the phrasing did not: {rubato_ms:.0f} ms "
            "of give and take that a single tempo cannot explain."
        )
    return "Loosely together -- close enough to mix, not yet close enough to feel like one part."


def _harmony(
    a: pitch.Contour, b: pitch.Contour, alignment: align.Alignment
) -> Dict[str, Any]:
    """The interval the two voices actually sang, read through the time map."""
    src = alignment.inverse_ms(a.time_ms)
    b_hz = b.at(src)
    cents = pitch.cents_between(b_hz, a.hz)
    # Require both voices to have been tracked confidently. Without this, the
    # occasional octave slip on a low line dominates the spread -- a tenor
    # octave part read a median interval of -2 cents, correctly, alongside a
    # spread of 1032 cents contributed by a handful of bad frames.
    confident = (a.confidence >= pitch.VOICED_CONF) & (
        np.interp(src, b.time_ms, b.confidence) >= pitch.VOICED_CONF
    )
    live = np.isfinite(cents) & confident
    if not np.any(live):
        return {
            **name_interval(0.0),
            "label": "not enough voiced overlap to say",
            "steady": False,
            "spread_cents": None,
            "coverage_percent": 0.0,
        }
    median = float(np.median(cents[live]))
    spread = float(np.percentile(np.abs(cents[live] - median), 90))
    out = name_interval(median)
    steady = spread <= INTERVAL_STEADY_CENTS
    if spread > INTERVAL_UNRELIABLE_CENTS:
        # Beyond a couple of whole tones of scatter the pitch track itself is
        # the problem, not the singing. A low voice at 20 dB SNR under mains
        # hum produces confident octave slips -- confident, so no confidence
        # filter removes them -- and describing that as "a moving interval of
        # 11.7 to 19.9 semitones" dresses a measurement failure up as a
        # musical observation.
        out["label"] = (
            "pitch too unreliable to name the interval -- a low voice in a "
            "noisy room slips octaves, and the reading inherits it"
        )
        out["in_tune"] = False
        out["reliable"] = False
    elif not steady:
        # Naming one interval when the gap between the voices keeps moving is
        # worse than declining to name it. Two singers in diatonic thirds
        # alternate between a major and a minor third by design, so the median
        # lands 46 cents from either and "a major third, 46 cents out" reads as
        # two people singing badly rather than as harmony.
        low, high = np.percentile(cents[live], [10, 90])
        out["label"] = (
            f"a moving interval, {abs(low) / 100.0:.1f} to "
            f"{abs(high) / 100.0:.1f} semitones -- harmony rather than a "
            "fixed gap"
        )
        out["in_tune"] = False
        out["range_cents"] = [round(float(low), 1), round(float(high), 1)]
    out.setdefault("reliable", True)
    out.update(
        {
            "spread_cents": round(spread, 1),
            "steady": bool(steady),
            "coverage_percent": round(100.0 * float(np.mean(live)), 1),
            "reading": (
                "The median interval between the two voices, measured through "
                "the alignment. A tight spread means they held one interval; a "
                "wide one means either that they are singing real harmony, "
                "where the interval is supposed to move, or that one of them "
                "drifted relative to the other."
            ),
        }
    )
    return out


def run_scene(scene: Any, clean: bool = True) -> DuetResult:
    """Run a fixture scene, for the checks and the demo's canned duets."""
    a, b = scene.parts[0], scene.parts[1]
    return run(
        a.audio,
        b.audio,
        sr=scene.sample_rate,
        clean=clean,
        name_a=f"{a.name} ({a.profile})",
        name_b=f"{b.name} ({b.profile})",
    )
