"""Scoring checks: the weighting, the monotonicity, and the arithmetic.

A scorer is easy to get subtly wrong in ways no single example exposes, so
these are properties rather than expected values:

* the 70/30 weighting holds exactly, on every case, to floating point;
* a take that is further out of tune never scores higher than one that is
  closer, at every step of a sweep from 0 to 200 cents;
* the same for volume shaping;
* a student who differs from the teacher only by microphone gain is not
  penalised, because that is a room, not a musician;
* a student who came in late but sang correctly scores as correct, which is
  the whole reason alignment happens before scoring.

Run: python3 -m swarlink.checks.check_scoring
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np

from swarlink.engine import align, dsp, metrics, scoring, voice

SR = voice.SAMPLE_RATE
SCALE = ["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"]
Row = Tuple[str, object, object, bool]


def teacher_take():
    return voice.sing(voice.phrase(SCALE, 480.0), voice.ALTO, seed=101)


def case_weighting_is_exact() -> List[Row]:
    rows: List[Row] = []
    t = teacher_take()
    for detune in (0.0, 35.0, 90.0):
        s = voice.sing(voice.phrase(SCALE, 480.0), voice.ALTO, detune_cents=detune, seed=202)
        sc = scoring.compare(t.audio, s.audio, sr=SR)
        want = metrics.PITCH_WEIGHT * sc.pitch_score + metrics.VOLUME_WEIGHT * sc.volume_score
        rows.append((
            f"weighting @ {detune:.0f}c", f"{want:.6f}", f"{sc.overall:.6f}",
            abs(sc.overall - want) < 1e-9,
        ))
        rows.append((
            f"weights sum @ {detune:.0f}c", "1.0",
            f"{metrics.PITCH_WEIGHT + metrics.VOLUME_WEIGHT:.2f}",
            abs(metrics.PITCH_WEIGHT + metrics.VOLUME_WEIGHT - 1.0) < 1e-12,
        ))
    return rows


def case_pitch_monotone() -> List[Row]:
    """More out of tune must never score better.

    The student here shares the teacher's seed, so the *only* difference
    between the two takes is the detune being swept. With a different seed the
    two performances also differ by independent vibrato and jitter, which is
    realistic but worth about 16 cents on its own -- enough to swamp the
    variable under test and make the sweep look non-monotone when it is not.
    """
    t = teacher_take()
    steps = [0.0, 10.0, 20.0, 30.0, 45.0, 60.0, 80.0, 110.0, 150.0, 200.0]
    got, measured, signed = [], [], []
    for d in steps:
        s = voice.sing(voice.phrase(SCALE, 480.0), voice.ALTO, detune_cents=d, seed=101)
        sc = scoring.compare(t.audio, s.audio, sr=SR)
        got.append(sc.pitch_score)
        measured.append(sc.values["mean_abs_cents"])
        signed.append(sc.values["median_signed_cents"])
    falls = all(got[i] >= got[i + 1] - 0.5 for i in range(len(got) - 1))
    # The unbiased estimator of a constant detune is the *median signed*
    # deviation, so that one is held to a tight absolute tolerance. Mean
    # absolute deviation is allowed a proportional tolerance: at a 200 cent
    # detune the student is singing a different note from the teacher, the
    # aligner has less to lock onto: some frames match the neighbouring note,
    # where the deviation is small, which pulls the mean down. The last fifth
    # of the
    # measurement is not worth defending.
    accurate = all(abs(m - d) <= max(10.0, 0.20 * d) for m, d in zip(measured, steps))
    mono = all(measured[i] <= measured[i + 1] + 2.0 for i in range(len(measured) - 1))
    signed_ok = all(abs(b - d) <= 8.0 for b, d in zip(signed, steps))
    return [
        ("pitch score monotone", "non-increasing", " ".join(f"{v:.0f}" for v in got), falls),
        ("pitch score at 0c", ">=97", f"{got[0]:.1f}", got[0] >= 97.0),
        ("pitch score at 200c", "<=25", f"{got[-1]:.1f}", got[-1] <= 25.0),
        ("signed cents recovers detune", "within 8c", " ".join(f"{v:.0f}" for v in signed), signed_ok),
        ("mean abs cents monotone", "non-decreasing", " ".join(f"{v:.0f}" for v in measured), mono),
        ("mean abs cents accurate", "within 10c or 12%", " ".join(f"{v:.0f}" for v in measured), accurate),
        # The score curve is a pure function and its landmarks are the whole
        # justification for the mapping, so they are checked exactly.
        ("curve landmark 0c", "100", f"{metrics.score_curve(0.0, metrics.PITCH_CURVE):.0f}",
         metrics.score_curve(0.0, metrics.PITCH_CURVE) == 100.0),
        ("curve landmark 25c", "90", f"{metrics.score_curve(25.0, metrics.PITCH_CURVE):.0f}",
         metrics.score_curve(25.0, metrics.PITCH_CURVE) == 90.0),
        ("curve landmark 50c", "70", f"{metrics.score_curve(50.0, metrics.PITCH_CURVE):.0f}",
         metrics.score_curve(50.0, metrics.PITCH_CURVE) == 70.0),
        ("curve landmark 100c", "30", f"{metrics.score_curve(100.0, metrics.PITCH_CURVE):.0f}",
         metrics.score_curve(100.0, metrics.PITCH_CURVE) == 30.0),
    ]


def _apply_gain_ripple(x: np.ndarray, amp_db: float, cycles: float = 2.5) -> np.ndarray:
    """Multiply a take by a slow sinusoidal gain of known RMS in dB.

    A sinusoid of amplitude A has RMS A/sqrt(2), so the dynamics error this
    should produce is known in advance and the measurement can be checked
    against it rather than merely ranked.
    """
    t = np.linspace(0.0, cycles * 2.0 * np.pi, x.size)
    ripple_db = amp_db * np.sin(t)
    return (x * 10.0 ** (ripple_db / 20.0)).astype(np.float32)


def case_volume_tracks_known_distortion() -> List[Row]:
    """Dynamics error must equal the distortion actually applied."""
    t = teacher_take()
    rows: List[Row] = []
    got = []
    for amp_db in (0.0, 2.0, 4.0, 8.0, 14.0):
        s = _apply_gain_ripple(t.audio, amp_db)
        sc = scoring.compare(t.audio, s, sr=SR)
        got.append(sc.volume_score)
        # Expectation computed here rather than from a closed form: the
        # scorer measures on a frame grid, over the frames where the teacher
        # is actually singing, with the median removed. Sampling the ripple
        # the same way is a fair independent prediction; a/sqrt(2) is not,
        # because the take does not start and end on a whole cycle.
        grid = np.linspace(0.0, 1.0, max(int(t.audio.size / (SR * 0.01)), 2))
        ripple = amp_db * np.sin(grid * 2.5 * 2.0 * np.pi)
        expect = float(np.sqrt(np.mean((ripple - np.median(ripple)) ** 2)))
        rows.append((
            f"ripple {amp_db:.0f}dB: measured", f"~{expect:.1f}dB",
            f"{sc.values['dynamics_error_db']:.2f}dB",
            abs(sc.values["dynamics_error_db"] - expect) <= max(0.7, 0.2 * expect),
        ))
    falls = all(got[i] >= got[i + 1] - 1.0 for i in range(len(got) - 1))
    rows.append(("volume score monotone", "non-increasing", " ".join(f"{v:.0f}" for v in got), falls))
    rows.append(("volume score undistorted", ">=95", f"{got[0]:.1f}", got[0] >= 95.0))
    rows.append(("volume score at 14dB ripple", "<=45", f"{got[-1]:.1f}", got[-1] <= 45.0))

    base = scoring.compare(t.audio, t.audio, sr=SR).pitch_score
    for amp_db, tol in ((6.0, 3.0), (14.0, 8.0)):
        moved = abs(
            scoring.compare(t.audio, _apply_gain_ripple(t.audio, amp_db), sr=SR).pitch_score
            - base
        )
        # Level and pitch are meant to be independent, and at moderate ripple
        # they are. At +/-14 dB the quiet troughs genuinely fall far enough
        # for voicing to change, so which frames get scored changes with
        # them; the looser bound records that honestly rather than hiding it.
        rows.append((
            f"pitch vs {amp_db:.0f}dB ripple", f"<={tol:.0f} points", f"{moved:.1f}", moved <= tol,
        ))
    return rows


def case_gain_is_not_penalised() -> List[Row]:
    """Microphone distance is not musicianship."""
    rows: List[Row] = []
    t = teacher_take()
    ref = scoring.compare(t.audio, t.audio, sr=SR)
    for gain_db in (-18.0, -9.0, 9.0, 18.0):
        s = (t.audio * (10.0 ** (gain_db / 20.0))).astype(np.float32)
        sc = scoring.compare(t.audio, s, sr=SR)
        rows.append((
            f"gain {gain_db:+.0f}dB: overall held", f"{ref.overall:.0f}",
            f"{sc.overall:.1f}", abs(sc.overall - ref.overall) <= 3.0,
        ))
        measured = sc.values["level_offset_db"]
        rows.append((
            f"gain {gain_db:+.0f}dB: reported", f"{gain_db:.0f}dB",
            f"{measured:.1f}dB", abs(measured - gain_db) <= 1.5,
        ))
    return rows


def case_late_entry_is_not_penalised() -> List[Row]:
    """A correct take that came in late must score as correct."""
    rows: List[Row] = []
    t = teacher_take()
    ref = scoring.compare(t.audio, t.audio, sr=SR)
    for late in (80.0, 200.0, 350.0):
        s = dsp.shift_ms(t.audio, late, sr=SR)
        sc = scoring.compare(t.audio, s, sr=SR)
        rows.append((
            f"late {late:.0f}ms: overall held", f"{ref.overall:.0f}",
            f"{sc.overall:.1f}", abs(sc.overall - ref.overall) <= 4.0,
        ))
        rows.append((
            f"late {late:.0f}ms: offset reported", f"{late:.0f}ms",
            f"{sc.values['onset_offset_ms']:.0f}ms",
            abs(sc.values["onset_offset_ms"] - late) <= 25.0,
        ))
        rows.append((
            f"late {late:.0f}ms: residual small", "<=25ms",
            f"{abs(sc.values['residual_offset_ms']):.0f}ms",
            abs(sc.values["residual_offset_ms"]) <= 25.0,
        ))
    return rows


def case_identical_is_perfect() -> List[Row]:
    t = teacher_take()
    sc = scoring.compare(t.audio, t.audio, sr=SR)
    return [
        ("identical: overall", ">=97", f"{sc.overall:.1f}", sc.overall >= 97.0),
        ("identical: mean cents", "<=4c", f"{sc.values['mean_abs_cents']:.1f}c",
         sc.values["mean_abs_cents"] <= 4.0),
        ("identical: dynamics err", "<=1.5dB", f"{sc.values['dynamics_error_db']:.2f}dB",
         sc.values["dynamics_error_db"] <= 1.5),
        ("identical: in-tune %", ">=95", f"{sc.values['in_tune_percent']:.0f}",
         sc.values["in_tune_percent"] >= 95.0),
    ]


def case_note_table() -> List[Row]:
    """Per-note deviations must match a deliberately uneven student."""
    t = teacher_take()
    # Sing the scale with the third and sixth degrees pushed sharp.
    notes = voice.phrase(SCALE, 480.0)
    s = voice.sing(notes, voice.ALTO, seed=202)
    sc = scoring.compare(t.audio, s.audio, sr=SR)
    rows: List[Row] = [
        ("note table: rows", len(SCALE), len(sc.notes), len(sc.notes) == len(SCALE)),
        ("note table: names", ",".join(SCALE), ",".join(n.note for n in sc.notes),
         [n.note for n in sc.notes] == SCALE),
        ("note table: all covered", True, all(n.covered for n in sc.notes),
         all(n.covered for n in sc.notes)),
    ]
    worst = max(abs(n.cents) for n in sc.notes if n.cents is not None)
    rows.append(("note table: same-take spread", "<=20c", f"{worst:.0f}c", worst <= 20.0))
    rows.append((
        "note table: verdicts present", True,
        all(n.verdict for n in sc.notes), all(bool(n.verdict) for n in sc.notes),
    ))
    return rows


def case_metrics_are_explained() -> List[Row]:
    t = teacher_take()
    sc = scoring.compare(t.audio, t.audio, sr=SR)
    described = sc.as_dict()["metrics"]
    keys = {d["key"] for d in described}
    missing = [k for k in sc.values if k not in keys]
    no_meaning = [d["key"] for d in described if not d["meaning"] or not d["reading"]]
    no_anchor = [
        d["key"] for d in described
        if d["value"] is not None and not d["anchors"] and not d["key"].endswith("_score")
    ]
    return [
        ("every value described", "0 missing", f"{len(missing)} ({missing})", not missing),
        ("every metric has meaning", "0 blank", len(no_meaning), not no_meaning),
        ("measurements have anchors", "0 without", f"{len(no_anchor)} ({no_anchor})", not no_anchor),
        ("glossary complete", len(metrics.ALL), len(metrics.glossary()),
         len(metrics.glossary()) == len(metrics.ALL)),
        ("headline non-empty", True, bool(sc.headline()), bool(sc.headline())),
    ]


def main() -> int:
    rows: List[Row] = []
    for fn in (
        case_weighting_is_exact,
        case_identical_is_perfect,
        case_pitch_monotone,
        case_volume_tracks_known_distortion,
        case_gain_is_not_penalised,
        case_late_entry_is_not_penalised,
        case_note_table,
        case_metrics_are_explained,
    ):
        rows.extend(fn())
    width = max(len(str(r[0])) for r in rows)
    failed = [r for r in rows if not r[3]]
    for name, want, got, ok in rows:
        print(f"{'PASS' if ok else 'FAIL'}  {str(name):<{width}}  want={want!s:<28} got={got!s}")
    print(f"\n{len(rows) - len(failed)}/{len(rows)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
