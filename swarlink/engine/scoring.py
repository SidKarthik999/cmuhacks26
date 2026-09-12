"""Comparing a student's take against a teacher's.

The headline is a 70/30 weighting of pitch against volume, and the weighting
is applied exactly once, at the end, on two sub-scores that are each derived
from a single measurement. That is a deliberate constraint. It would be easy
to blend six quantities with six coefficients and get a number that ranks
takes plausibly, and impossible to explain to the student why theirs went
down. Here, every number on the screen is either one of the two things being
scored or a diagnostic that is explicitly not scored.

What is scored:

* pitch, from the mean absolute deviation in cents, mapped through the
  perceptual landmarks in `metrics.PITCH_CURVE`;
* volume, from the error in the *shape* of the loudness curve after the
  overall level difference is removed.

What is measured and reported but not scored, with reasons:

* overall level -- mostly microphone distance, not musicianship;
* timing -- the user asked for a pitch/volume split, and timing is handled by
  aligning the takes rather than by marking the student down for it;
* voiced fraction and confidence -- these bound how much the other numbers
  are worth, so hiding them would let a high score on a near-silent take
  pass without comment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np

from . import align, dsp, metrics, pitch

IN_TUNE_CENTS = 25.0
MIN_SCORED_FRAMES = 8
ENVELOPE_FLOOR_DB = -45.0
# One cycle of vibrato at a typical 5 Hz. Both contours are smoothed over
# this before being compared, for two reasons that happen to agree.
#
# Musically: vibrato is not intonation. A teacher and a student can both be
# perfectly in tune on the same note and still be 50 cents apart at any given
# instant, because their vibrato is not phase-locked -- nobody's is, and a
# scorer that penalised it would be measuring coincidence.
#
# Numerically: comparing instantaneous pitch makes the score sensitive to the
# time map, because during vibrato the pitch is moving fast. A 20 ms error in
# where the student's moment is read from turns into a 10 cent pitch error
# that has nothing to do with the singing.
VIBRATO_SMOOTH_MS = 200.0


@dataclass
class NoteComparison:
    """One teacher note and what the student did with it."""

    index: int
    note: str
    start_ms: float
    duration_ms: float
    teacher_hz: float
    student_hz: Optional[float]
    cents: Optional[float]
    level_db: Optional[float]
    verdict: str
    covered: bool

    def as_dict(self) -> Dict[str, object]:
        return {
            "index": self.index,
            "note": self.note,
            "start_ms": round(self.start_ms, 1),
            "duration_ms": round(self.duration_ms, 1),
            "teacher_hz": round(self.teacher_hz, 2),
            "student_hz": None if self.student_hz is None else round(self.student_hz, 2),
            "cents": None if self.cents is None else round(self.cents, 1),
            "level_db": None if self.level_db is None else round(self.level_db, 2),
            "verdict": self.verdict,
            "covered": self.covered,
        }


@dataclass
class Score:
    overall: float
    pitch_score: float
    volume_score: float
    values: Dict[str, Optional[float]] = field(default_factory=dict)
    notes: List[NoteComparison] = field(default_factory=list)
    cents_series: List[Dict[str, float]] = field(default_factory=list)
    level_series: List[Dict[str, float]] = field(default_factory=list)
    caveats: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {
            "overall": round(self.overall, 1),
            "pitch_score": round(self.pitch_score, 1),
            "volume_score": round(self.volume_score, 1),
            "weighting": {
                "pitch": metrics.PITCH_WEIGHT,
                "volume": metrics.VOLUME_WEIGHT,
                "note": (
                    f"overall = {metrics.PITCH_WEIGHT:.2f} x pitch + "
                    f"{metrics.VOLUME_WEIGHT:.2f} x volume"
                ),
            },
            "metrics": metrics.describe(self.values),
            "notes": [n.as_dict() for n in self.notes],
            "cents_series": self.cents_series,
            "level_series": self.level_series,
            "caveats": self.caveats,
            "headline": self.headline(),
        }

    def headline(self) -> str:
        """One sentence a student can act on, chosen by what dominates."""
        bias = self.values.get("median_signed_cents") or 0.0
        spread = self.values.get("cents_spread") or 0.0
        dyn = self.values.get("dynamics_error_db") or 0.0
        if abs(bias) > 20.0 and spread < abs(bias):
            return (
                f"Consistently {metrics.cents_in_words(bias)} -- the shape is "
                "right, the anchor is not."
            )
        if spread > 35.0:
            return "The pitch wanders rather than sitting in one place; breath support before tuning."
        if dyn > 5.0:
            return "Pitch is solid; the phrase is not being shaped the way the teacher shaped it."
        if self.overall >= 90.0:
            return "Close to the teacher on both pitch and dynamics."
        return "Broadly together, with room on both pitch and phrasing."


LEVEL_WIN_MS = 80.0


def _frame_level_db(x: np.ndarray, sr: int, hop_ms: float) -> np.ndarray:
    """Loudness over time, on the timescale of phrasing rather than attacks.

    An 80 ms window, not 30. The quantity being scored is how the singer
    shapes a line, and the steep edge of a note attack carries almost no
    information about that while dominating everything nearby: level changes
    by 20 dB in 30 ms there, so reading the student's envelope one
    millisecond off the teacher's produces most of a decibel of error out of
    nothing. A window closer to a syllable measures the thing we mean.
    """
    env = dsp.frame_rms(x, sr=sr, hop_ms=hop_ms, win_ms=max(hop_ms * 3.0, LEVEL_WIN_MS))
    with np.errstate(divide="ignore"):
        return 20.0 * np.log10(np.maximum(env, 1e-9))


def _pitch_part(
    teacher: pitch.Contour, student_at_teacher_grid: np.ndarray
) -> Dict[str, Optional[float]]:
    dev = pitch.cents_between(student_at_teacher_grid, teacher.hz)
    usable = np.isfinite(dev)
    if usable.sum() < MIN_SCORED_FRAMES:
        return {
            "mean_abs_cents": None,
            "median_signed_cents": None,
            "cents_spread": None,
            "in_tune_percent": None,
        }
    d = dev[usable]
    bias = float(np.median(d))
    return {
        "mean_abs_cents": float(np.mean(np.abs(d))),
        "median_signed_cents": bias,
        # Spread about the bias, not about zero: this separates "sings the
        # shape steadily but in the wrong place" from "cannot hold a pitch".
        "cents_spread": float(np.percentile(np.abs(d - bias), 68.0)),
        "in_tune_percent": float(100.0 * np.mean(np.abs(d) <= IN_TUNE_CENTS)),
    }


def _sample_series(values: np.ndarray, hop_ms: float, times_ms: np.ndarray) -> np.ndarray:
    """Read a per-frame series at arbitrary times, holding the edges."""
    if values.size == 0:
        return np.full(times_ms.shape, np.nan)
    grid = np.arange(values.size) * hop_ms
    return np.interp(times_ms, grid, values, left=values[0], right=values[-1])


def _volume_part(
    teacher_level: np.ndarray, student_level: np.ndarray, teacher: np.ndarray, student: np.ndarray, sr: int
) -> Dict[str, Optional[float]]:
    a, b = teacher_level, student_level
    n = min(a.size, b.size)
    a, b = a[:n], b[:n]
    # Only compare where the teacher is actually singing. Including the
    # silence between phrases would let a noisy room dominate a quantity that
    # is supposed to describe phrasing.
    live = a > (np.max(a) + ENVELOPE_FLOOR_DB if a.size else 0.0)
    if live.sum() < MIN_SCORED_FRAMES:
        return {
            "level_offset_db": None,
            "dynamics_error_db": None,
            "dynamic_range_db": None,
            "envelope_correlation": None,
        }
    diff = b[live] - a[live]
    offset = float(np.median(diff))
    shaped = diff - offset
    return {
        "level_offset_db": float(
            dsp.loudness_lufs(student, sr=sr) - dsp.loudness_lufs(teacher, sr=sr)
        ),
        "dynamics_error_db": float(np.sqrt(np.mean(shaped ** 2))),
        "dynamic_range_db": float(np.percentile(a[live], 95) - np.percentile(a[live], 5)),
        "envelope_correlation": dsp.correlation(a[live], b[live]),
    }


def _note_rows(
    teacher_notes: Sequence[pitch.NoteSpan],
    teacher: pitch.Contour,
    student_hz: np.ndarray,
    teacher_level: np.ndarray,
    student_level: np.ndarray,
    hop_ms: float,
) -> List[NoteComparison]:
    rows: List[NoteComparison] = []
    for i, span in enumerate(teacher_notes):
        # Trim the edges of each note: the first and last moments are the
        # attack and the slide into the next note, where "the pitch of this
        # note" is not a well-defined quantity.
        trim = min(0.2 * span.duration_ms, 80.0)
        lo = int((span.start_ms + trim) / hop_ms)
        hi = int((span.end_ms - trim) / hop_ms)
        hi = max(hi, lo + 1)
        seg_ref = teacher.hz[lo:hi]
        seg_stu = student_hz[lo:hi]
        both = np.isfinite(seg_ref) & np.isfinite(seg_stu)
        covered = bool(both.sum() >= 3)

        cents = stu_hz = level = None
        verdict = "not sung"
        if covered:
            cents = float(np.median(pitch.cents_between(seg_stu[both], seg_ref[both])))
            stu_hz = float(np.median(seg_stu[both]))
            verdict = metrics.cents_in_words(cents)
            la = teacher_level[lo:hi]
            lb = student_level[lo:hi]
            m = min(la.size, lb.size)
            if m > 0:
                level = float(np.mean(lb[:m]) - np.mean(la[:m]))
        rows.append(
            NoteComparison(
                index=i,
                note=span.note,
                start_ms=span.start_ms,
                duration_ms=span.duration_ms,
                teacher_hz=span.hz,
                student_hz=stu_hz,
                cents=cents,
                level_db=level,
                verdict=verdict,
                covered=covered,
            )
        )
    return rows


def compare(
    teacher: np.ndarray,
    student: np.ndarray,
    sr: int = 22050,
    hop_ms: float = pitch.HOP_MS,
    alignment: Optional[align.Alignment] = None,
    student_aligned: Optional[np.ndarray] = None,
    series_points: int = 240,
) -> Score:
    """Score a student take against a teacher take.

    The student is time-aligned onto the teacher's clock first. That is not a
    convenience: comparing pitch frame by frame between takes that are 150 ms
    apart measures the delay, not the intonation, and it would report a
    perfect student who came in late as being a semitone out on every note.
    Timing is corrected, then reported separately as its own diagnostic.
    """
    if student_aligned is None:
        student_aligned, alignment = align.align_and_warp(teacher, student, sr=sr)
    elif alignment is None:
        alignment = align.align(teacher, student, sr=sr)

    # Measure on the *original* student audio, read through the alignment's
    # time map, rather than on the resampled copy. The resampled copy exists
    # so the two takes can be played together, and a phase vocoder is good
    # enough for that; it is not good enough to be measured. Its pitch
    # reconstruction is accurate to a few cents rather than exactly, so
    # scoring the resampled audio charged students for the aligner's own
    # rounding -- up to 27 cents on a take whose only fault was an uneven
    # microphone level. Asking "what was the student doing at the moment that
    # corresponds to this moment of the teacher's" needs no resampling at
    # all, only the map that the resampling was going to use anyway.
    t_contour = pitch.track(teacher, sr=sr, hop_ms=hop_ms)
    s_contour = pitch.track(student, sr=sr, hop_ms=hop_ms)
    width = max(int(round(VIBRATO_SMOOTH_MS / hop_ms)) | 1, 3)
    t_smooth = pitch.Contour(
        t_contour.time_ms, pitch.median_filter(t_contour.hz, width),
        t_contour.confidence, t_contour.rms_db, sr,
    )
    s_smooth = pitch.Contour(
        s_contour.time_ms, pitch.median_filter(s_contour.hz, width),
        s_contour.confidence, s_contour.rms_db, sr,
    )
    src_times = alignment.inverse_ms(t_contour.time_ms)
    student_hz = s_smooth.at(src_times)

    t_level = _frame_level_db(teacher, sr, hop_ms)
    s_level_own = _frame_level_db(student, sr, hop_ms)
    s_level = _sample_series(s_level_own, hop_ms, src_times)
    n_level = min(t_level.size, s_level.size, t_contour.time_ms.size)

    values: Dict[str, Optional[float]] = {}
    values.update(_pitch_part(t_smooth, student_hz))
    values.update(_volume_part(t_level, s_level, teacher, student, sr))

    # Report the entry offset the aligner settled on, not a fresh
    # cross-correlation of the two raw takes. On repetitive material the raw
    # estimate is genuinely ambiguous -- a scale is eight similar attacks at
    # even spacing, so locking one note early or two notes late is almost as
    # cheap as locking correctly, and it read -840 ms on a take that was
    # 160 ms late. The aligner's offset comes from a joint offset-and-tempo
    # scan followed by a banded path, which resolves that ambiguity. The raw
    # figure is kept only to notice when it happens.
    raw_offset, raw_conf = align.estimate_offset_ms(teacher, student, sr=sr)
    resid, _ = align.residual_offset_ms(teacher, student_aligned, sr=sr)
    drift = alignment.drift_series(13, span_ms=teacher.size * 1000.0 / sr)
    values.update(
        {
            "onset_offset_ms": alignment.offset_ms,
            "residual_offset_ms": resid,
            "worst_window_ms": align._worst_window_ms(teacher, student_aligned, sr=sr),
            "tempo_ratio": alignment.tempo_ratio,
            "drift_spread_ms": max(abs(d["drift_ms"]) for d in drift) if drift else 0.0,
            "confidence": max(alignment.confidence, raw_conf),
            "voiced_percent": 100.0 * t_contour.voiced_fraction,
        }
    )

    pitch_score = (
        metrics.score_curve(values["mean_abs_cents"], metrics.PITCH_CURVE)
        if values["mean_abs_cents"] is not None
        else 0.0
    )
    volume_score = (
        metrics.score_curve(values["dynamics_error_db"], metrics.VOLUME_CURVE)
        if values["dynamics_error_db"] is not None
        else 0.0
    )
    overall = metrics.PITCH_WEIGHT * pitch_score + metrics.VOLUME_WEIGHT * volume_score
    values["pitch_score"] = pitch_score
    values["volume_score"] = volume_score
    values["overall_score"] = overall

    rows = _note_rows(
        pitch.segment_notes(t_contour), t_contour, student_hz, t_level, s_level, hop_ms
    )

    caveats: List[str] = []
    if (values.get("voiced_percent") or 0.0) < 55.0:
        caveats.append(
            f"Only {values['voiced_percent']:.0f}% of the teacher's take had a "
            "trackable pitch, so the pitch score rests on less than half of it."
        )
    if (values.get("confidence") or 0.0) < 0.45:
        caveats.append(
            "Few clear attacks to align on, so the timing numbers are weak "
            "evidence rather than measurements."
        )
    if abs(raw_offset - alignment.offset_ms) > 120.0:
        caveats.append(
            "The take is repetitive enough that a plain onset match put the "
            f"entry at {raw_offset:+.0f} ms where the full alignment puts it at "
            f"{alignment.offset_ms:+.0f} ms. The score uses the latter."
        )
    uncovered = sum(1 for r in rows if not r.covered)
    if uncovered:
        caveats.append(
            f"{uncovered} of the teacher's {len(rows)} notes were not sung back "
            "and are excluded from the per-note table."
        )

    return Score(
        overall=overall,
        pitch_score=pitch_score,
        volume_score=volume_score,
        values=values,
        notes=rows,
        cents_series=_series(
            t_contour.time_ms, pitch.cents_between(student_hz, t_contour.hz), series_points, "cents"
        ),
        level_series=_series(
            t_contour.time_ms[:n_level],
            s_level[:n_level] - t_level[:n_level],
            series_points,
            "db",
        ),
        caveats=caveats,
    )


def _series(times: np.ndarray, vals: np.ndarray, points: int, key: str) -> List[Dict[str, float]]:
    """Downsample a per-frame series for plotting, keeping gaps as nulls."""
    n = min(times.size, vals.size)
    if n == 0:
        return []
    step = max(int(np.ceil(n / max(points, 1))), 1)
    out = []
    for i in range(0, n, step):
        v = vals[i : i + step]
        finite = v[np.isfinite(v)]
        out.append(
            {
                "t_ms": round(float(times[i]), 1),
                key: None if finite.size == 0 else round(float(np.median(finite)), 2),
            }
        )
    return out
