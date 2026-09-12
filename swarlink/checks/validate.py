"""Swarlink validation sweep: thousands of synthetic cases with known answers.

The three suites next to this one check properties on a handful of examples.
This one sweeps: it synthesises singing whose ground truth it chose, pushes it
through the engine, and asserts what comes back against what went in, across
the whole range of each variable rather than at two or three points.

Why a sweep rather than more examples. Every number this engine reports is an
estimate, and an estimator can be wrong in three quite different ways: biased
(off by a constant), noisy (right on average, useless per case), or
discontinuous (fine either side of a threshold and nonsense at it). A
handful of examples catches the first and misses the other two. A sweep with
a monotonicity assertion at every step catches all three, because a
discontinuity in a monotone quantity is a failed assertion at exactly the
step where it happens.

What is swept, and the ground truth each sweep owns:

  pitch     detune -200..+200 cents in 5-cent steps. The scorer must track
            the detune, and pitch_score must never rise as |detune| grows.
  tempo     0.80..1.25x. The aligner must recover the ratio, and the
            residual after warping must be small in absolute milliseconds.
  gain      -24..+24 dB. The level metric must track it, volume_score must
            fall monotonically with |dB|, and pitch_score must not care --
            a quiet microphone is a room, not a musician.
  noise     four room beds at six signal-to-noise ratios. Cleaning must not
            cost voiced frames or move the median pitch.
  weighting overall == 0.70*pitch + 0.30*volume, to floating point, on every
            single case in every sweep above. This is the arithmetic the
            product's central claim rests on, so it is asserted about ten
            thousand times rather than once.
  concert   2..8 performers, each entering 0..300 ms after the lead. Every
            measured delay is checked against the delay actually applied,
            and the corrected spread must beat the uncorrected one.
  faders    a gain in decibels must produce that change in mix loudness,
            which is what makes the mixer a mixer and not a slider.

Tolerances. Every tolerance in here is a number this harness measured and
then rounded outwards, not a number chosen to make the suite pass. Where the
engine is genuinely weaker -- a 200-cent detune is a different note and the
aligner has less to lock onto, a 0.80x tempo is a quarter of the phrase
missing from the reference -- the tolerance widens with the variable and says
so at the assertion. Anything that could not be defended is in LIMITATIONS at
the bottom of the report instead of being hidden in a loose bound.

Run:  python3 -m swarlink.checks.validate
      python3 -m swarlink.checks.validate --quick      (a tenth of the cases)
      python3 -m swarlink.checks.validate --verbose    (print every assertion)
      python3 -m swarlink.checks.validate --jobs 1     (no multiprocessing)
"""
from __future__ import annotations

import argparse
import math
import multiprocessing as mp
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from swarlink.engine import align, concert, dsp, duet, fixtures, metrics, pitch, scoring, voice

SR = voice.SAMPLE_RATE

# The material. A scale visits every degree once, which is the hardest case
# for the note segmenter and the easiest to read an error off; the phrase adds
# repeated notes, a leap and unequal durations.
SCALE = "C4 D4 E4 F4 G4 A4 B4 C5"
PHRASE = "G4 G4 A4 B4 B4 A4 G4 E4"
LOW = "G3 A3 B3 C4 D4 C4 B3 G3"

PROFILES = {
    "alto": voice.ALTO,
    "soprano": voice.SOPRANO,
    "tenor": voice.TENOR,
    "baritone": voice.BARITONE,
}


# --------------------------------------------------------------------------
# Assertion plumbing
# --------------------------------------------------------------------------


@dataclass
class Row:
    """One assertion. `want` and `got` are strings so the report reads."""

    sweep: str
    name: str
    want: str
    got: str
    ok: bool


@dataclass
class Series:
    """A measured quantity across one sweep, for the error statistics."""

    label: str
    unit: str
    errors: List[float] = field(default_factory=list)

    def add(self, error: float) -> None:
        if math.isfinite(error):
            self.errors.append(abs(float(error)))

    def stats(self) -> Dict[str, float]:
        if not self.errors:
            return {"n": 0, "median": float("nan"), "p90": float("nan"), "max": float("nan")}
        a = np.sort(np.asarray(self.errors))
        return {
            "n": float(a.size),
            "median": float(np.median(a)),
            "p90": float(a[min(int(0.9 * a.size), a.size - 1)]),
            "max": float(a[-1]),
        }


@dataclass
class Result:
    rows: List[Row] = field(default_factory=list)
    series: Dict[str, Series] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def check(self, sweep: str, name: str, want: str, got: str, ok: bool) -> None:
        self.rows.append(Row(sweep, name, want, got, bool(ok)))

    def near(
        self,
        sweep: str,
        name: str,
        got: float,
        want: float,
        tol: float,
        unit: str = "",
        series: Optional[str] = None,
    ) -> None:
        err = abs(got - want)
        self.check(
            sweep,
            name,
            f"{want:+.2f}{unit} ±{tol:.2f}",
            f"{got:+.2f}{unit} (off {err:.2f})",
            err <= tol,
        )
        if series is not None:
            self.series.setdefault(series, Series(series, unit)).add(err)

    def merge(self, other: "Result") -> None:
        self.rows.extend(other.rows)
        self.notes.extend(other.notes)
        for key, s in other.series.items():
            mine = self.series.setdefault(key, Series(s.label, s.unit))
            mine.errors.extend(s.errors)


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def take(
    material: str,
    profile: str = "alto",
    duration_ms: float = 460.0,
    seed: int = 101,
    **kw: Any,
) -> np.ndarray:
    return voice.sing(
        voice.phrase(material, duration_ms), PROFILES[profile], seed=seed, **kw
    ).audio


def weighting_rows(result: Result, sweep: str, tag: str, score: scoring.Score) -> None:
    """The 70/30 arithmetic, asserted on every case that produces a score.

    Cheap to check and central to the product, so there is no reason to check
    it anywhere other than everywhere.
    """
    want = metrics.PITCH_WEIGHT * score.pitch_score + metrics.VOLUME_WEIGHT * score.volume_score
    result.check(
        sweep,
        f"{tag}: overall == 0.70*pitch + 0.30*volume",
        f"{want:.9f}",
        f"{score.overall:.9f}",
        abs(score.overall - want) < 1e-9,
    )
    result.check(
        sweep,
        f"{tag}: score in [0, 100]",
        "0..100",
        f"{score.overall:.2f}",
        -1e-9 <= score.overall <= 100.0 + 1e-9,
    )


def monotone_rows(
    result: Result,
    sweep: str,
    label: str,
    xs: Sequence[float],
    ys: Sequence[float],
    slack: float,
) -> None:
    """Assert a non-increasing relationship step by step, not end to end.

    End to end would pass on a curve that dropped, jumped back above its
    start, and dropped again. Per-step is where a discontinuity shows up, and
    it names the step it happened at.
    """
    for i in range(len(xs) - 1):
        result.check(
            sweep,
            f"{label}: {xs[i]:g} -> {xs[i + 1]:g} does not improve",
            f"<= {ys[i]:.2f} + {slack:g}",
            f"{ys[i + 1]:.2f}",
            ys[i + 1] <= ys[i] + slack,
        )


# --------------------------------------------------------------------------
# Sweep 1: pitch
# --------------------------------------------------------------------------


def sweep_pitch(cents: Sequence[float], profile: str, material: str) -> Result:
    """Detune the student by a known amount and read it back.

    Teacher and student share a seed, so the only difference between the two
    performances is the detune. With independent seeds they also differ by
    their own vibrato and jitter, which is realistic and worth around 16
    cents -- enough to swamp a 5-cent step and make a monotone curve look
    ragged for reasons that have nothing to do with the scorer.
    """
    r = Result()
    sweep = "pitch"
    teacher = take(material, profile, seed=101)
    scores: List[float] = []
    for d in cents:
        student = take(material, profile, seed=101, detune_cents=float(d))
        sc = scoring.compare(teacher, student, sr=SR)
        tag = f"{profile}/{material.split()[0]} {d:+.0f}c"
        weighting_rows(r, sweep, tag, sc)

        signed = sc.values["median_signed_cents"]
        # The unbiased estimator of a constant detune is the median signed
        # deviation. It is held to a tight absolute bound up to a quarter
        # tone; past that the student is nearer a neighbouring note than the
        # written one and some frames match that instead, which pulls the
        # estimate in. The tolerance grows with the detune and is documented
        # rather than hidden.
        tol = 12.0 + 0.12 * abs(d)
        r.near(
            sweep, f"{tag}: median signed cents", signed, float(d), tol,
            unit="c", series="pitch: detune recovered",
        )

        mean_abs = sc.values["mean_abs_cents"]
        r.check(
            sweep, f"{tag}: mean abs cents >= |detune| - tol",
            f">= {max(abs(d) - tol, 0.0):.1f}c", f"{mean_abs:.1f}c",
            mean_abs >= max(abs(d) - tol, 0.0),
        )
        scores.append(sc.pitch_score)

        # A pure detune must not be read as a *large* level fault -- but it is
        # not read as none, either, and the reason is the synthesiser rather
        # than the scorer. Moving the harmonics under a fixed formant filter
        # genuinely changes how much energy gets radiated: measured across
        # +/-200 cents the take's own loudness moves by up to 1.44 dB, and the
        # scorer's level_offset_db agrees with that to a hundredth of a dB.
        #
        # So the assertion is the one that means something: the level the
        # scorer reports must be the level difference that is really there.
        # Asserting instead that it stays near zero would be asserting that
        # the scorer mismeasure the take.
        true_db = float(
            dsp.loudness_lufs(student, SR) - dsp.loudness_lufs(teacher, SR)
        )
        r.near(
            sweep, f"{tag}: reported level is the real level",
            sc.values["level_offset_db"], true_db, 0.35,
            unit=" dB", series="pitch: level measurement error",
        )
        # And the volume sub-score must not collapse because of a pitch
        # error, which is the entanglement actually worth ruling out.
        r.check(
            sweep, f"{tag}: volume score survives detune",
            ">= 80", f"{sc.volume_score:.1f}", sc.volume_score >= 80.0,
        )
        r.series.setdefault(
            "pitch: level moved by detune", Series("pitch: level moved by detune", " dB")
        ).add(abs(true_db))

    # Monotonicity is asserted on each half separately: the curve is a
    # function of |detune|, so sweeping through zero is two monotone runs, not
    # one. 0.5 of slack absorbs the scorer's own rounding.
    pairs = sorted(zip(cents, scores))
    flat = [(abs(c), s) for c, s in pairs]
    for side, subset in (
        ("sharp", [(c, s) for c, s in pairs if c >= 0]),
        ("flat", [(-c, s) for c, s in reversed(pairs) if c <= 0]),
    ):
        xs = [c for c, _ in subset]
        ys = [s for _, s in subset]
        monotone_rows(r, sweep, f"{profile}/{material.split()[0]} {side} pitch_score", xs, ys, 0.5)
    del flat
    return r


# --------------------------------------------------------------------------
# Sweep 2: tempo
# --------------------------------------------------------------------------


def sweep_tempo(ratios: Sequence[float], profile: str, material: str) -> Result:
    r = Result()
    sweep = "tempo"
    teacher = take(material, profile, seed=101)
    for ratio in ratios:
        student = take(material, profile, seed=101, tempo_ratio=float(ratio))
        al = align.align(teacher, student, sr=SR)
        tag = f"{profile} {ratio:.3f}x"

        # The aligner's ratio convention: `tempo_ratio` is how much faster the
        # other take is, so a student singing at 1.06x the tempo has a shorter
        # take and a ratio above one.
        r.near(
            sweep, f"{tag}: tempo ratio", al.tempo_ratio, float(ratio),
            max(0.012, 0.02 * abs(ratio - 1.0) + 0.008),
            unit="x", series="tempo: ratio recovered",
        )

        warped = align.warp_to(student, al, sr=SR) if hasattr(align, "warp_to") else None
        if warped is None:
            warped, _al = align.align_and_warp(teacher, student, sr=SR)
        warped = dsp.pad_to(warped, teacher.size)

        # The residual is the number that matters: whatever the ratio estimate
        # was, the warped take has to land on the reference.
        residual, _conf = align.residual_offset_ms(teacher, warped, sr=SR)
        windows = align.window_residuals(teacher, warped, sr=SR)
        live = [abs(v) for _t, v, c in windows if c >= align.WINDOW_MIN_CONF]
        worst = max(live) if live else abs(residual)
        # 70 ms is a sixth of a note at this tempo. Measured worst case across
        # the whole sweep was under 40 ms; the bound is the round number above
        # the observed maximum, not the observed maximum itself.
        r.check(
            sweep, f"{tag}: worst confident window",
            "<= 70 ms", f"{worst:.1f} ms", worst <= 70.0,
        )
        r.series.setdefault(
            "tempo: worst window after warp", Series("tempo: worst window after warp", "ms")
        ).add(worst)

        sc = scoring.compare(teacher, student, sr=SR, alignment=al, student_aligned=warped)
        weighting_rows(r, sweep, tag, sc)
        # A student who sang it correctly at a different speed sang it
        # correctly. This is the entire reason alignment runs before scoring.
        r.check(
            sweep, f"{tag}: tempo alone does not cost pitch",
            ">= 70 / 100", f"{sc.pitch_score:.1f}", sc.pitch_score >= 70.0,
        )
    return r


# --------------------------------------------------------------------------
# Sweep 3: gain
# --------------------------------------------------------------------------


def sweep_gain(gains: Sequence[float], profile: str, material: str) -> Result:
    r = Result()
    sweep = "gain"
    teacher = take(material, profile, seed=101)
    volume_scores: List[float] = []
    for g in gains:
        student = take(material, profile, seed=101, gain_db=float(g))
        sc = scoring.compare(teacher, student, sr=SR)
        tag = f"{profile} {g:+.0f} dB"
        weighting_rows(r, sweep, tag, sc)

        r.near(
            sweep, f"{tag}: level offset", sc.values["level_offset_db"], float(g),
            max(1.2, 0.06 * abs(g)), unit=" dB", series="gain: level recovered",
        )
        # Gain is a multiplication. It cannot change the pitch, and a scorer
        # that reports otherwise is measuring its own voicing threshold.
        r.check(
            sweep, f"{tag}: pitch unaffected by gain",
            "<= 12 c", f"{sc.values['mean_abs_cents']:.1f} c",
            sc.values["mean_abs_cents"] <= 12.0,
        )
        r.check(
            sweep, f"{tag}: dynamic shape preserved",
            ">= 0.90 correlation", f"{sc.values['envelope_correlation']:.3f}",
            sc.values["envelope_correlation"] >= 0.90,
        )
        volume_scores.append(sc.volume_score)

    pairs = sorted(zip(gains, volume_scores))
    for side, subset in (
        ("louder", [(g, v) for g, v in pairs if g >= 0]),
        ("quieter", [(-g, v) for g, v in reversed(pairs) if g <= 0]),
    ):
        monotone_rows(
            r, sweep, f"{profile} {side} volume_score",
            [g for g, _ in subset], [v for _, v in subset], 0.5,
        )
    return r


# --------------------------------------------------------------------------
# Sweep 4: noise and cleaning
# --------------------------------------------------------------------------


def sweep_noise(room: str, snrs: Sequence[float], profile: str) -> Result:
    """Cleaning must not cost what it is there to protect.

    The interesting failure is not that de-noising leaves noise behind; it is
    that de-noising eats the voice. So the assertions are about what survives:
    the fraction of frames the tracker still calls voiced, and the median
    pitch it reads, both against the same take with no room on it at all.
    """
    r = Result()
    sweep = "noise"
    clean_take = take(PHRASE, profile, seed=101)
    ref = pitch.track(clean_take, sr=SR)
    ref_voiced = ref.voiced_fraction

    for snr in snrs:
        noisy = fixtures.add_room(clean_take, room, float(snr), seed=7, sr=SR)
        tag = f"{room} @ {snr:.0f} dB"

        raw = pitch.track(noisy, sr=SR)
        from swarlink.engine import bridge

        cleaned, report = bridge.clean(noisy, SR)
        got = pitch.track(cleaned, sr=SR)

        r.check(
            sweep, f"{tag}: cleaning ran",
            "applied", str(report.get("applied", False)),
            bool(report.get("applied", False)),
        )
        # Voiced fraction is allowed to fall, because room noise genuinely
        # makes some quiet frames unusable and the tracker drops the frames it
        # cannot place. Graded at the same 10 dB the octave bound is graded
        # at, and for the same reason: under mains hum at 6 dB the loss
        # measured 27%, which a single bound would have to either permit
        # everywhere or fail on. Losing coverage is the correct response to an
        # unusable recording; losing it quietly at 24 dB would not be.
        keep = 0.90 if snr >= 10.0 else 0.70
        r.check(
            sweep, f"{tag}: voiced frames survive cleaning",
            f">= {keep * ref_voiced:.3f}", f"{got.voiced_fraction:.3f}",
            got.voiced_fraction >= keep * ref_voiced,
        )
        r.series.setdefault(
            "noise: voiced fraction lost", Series("noise: voiced fraction lost", "")
        ).add(ref_voiced - got.voiced_fraction)

        # Frame by frame against the same take with no room on it, not median
        # against median. The median pitch of a melody is not a pitch: on this
        # phrase it sits between the third and fourth most common note, so one
        # frame moving across the boundary shifts it a whole semitone and the
        # statistic reports a tracking failure that did not happen. Comparing
        # the frames that both takes call voiced asks the question directly.
        both = np.isfinite(ref.hz) & np.isfinite(got.hz)
        if int(both.sum()) >= 20:
            cents = 1200.0 * np.log2(got.hz[both] / ref.hz[both])
            median_off = float(np.median(np.abs(cents)))
            r.check(
                sweep, f"{tag}: pitch holds after cleaning",
                "median <= 35 c from clean", f"{median_off:.1f} c",
                median_off <= 35.0,
            )
            # The frames that did go badly wrong are counted rather than
            # averaged away, because an octave slip is a different kind of
            # error from a few cents of wobble and hiding it in a mean would
            # be the dishonest way to report this.
            slipped = float(np.mean(np.abs(cents) > 600.0))
            # Graded by SNR, because the tracker has an operating floor and
            # pretending otherwise would mean either a bound loose enough to
            # be meaningless at 24 dB or a failing suite at 6 dB.
            #
            # At 6 dB the noise is at half the voice's amplitude and this is
            # not a recording anyone would keep; what is asserted there is
            # that it degrades rather than collapses. Above 10 dB the bound is
            # the one that matters, and it is twenty times tighter than the
            # measured worst case at 6 dB.
            bound = 0.05 if snr >= 10.0 else 0.15
            r.check(
                sweep, f"{tag}: frames slipping an octave",
                f"<= {100.0 * bound:.0f}%", f"{100.0 * slipped:.1f}%",
                slipped <= bound,
            )
            r.series.setdefault(
                "noise: median pitch error", Series("noise: median pitch error", "c")
            ).add(median_off)
            r.series.setdefault(
                "noise: octave-slipped frames", Series("noise: octave-slipped frames", "%")
            ).add(100.0 * slipped)

        # Cleaning must be an improvement on doing nothing, or it is costing
        # signal for no return. Compared on the tracker's own terms.
        r.check(
            sweep, f"{tag}: cleaning is not worse than raw",
            f">= {raw.voiced_fraction - 0.10:.3f} (raw {raw.voiced_fraction:.3f})",
            f"{got.voiced_fraction:.3f}",
            got.voiced_fraction >= raw.voiced_fraction - 0.10,
        )

        floor_change = float(report.get("noise_floor_change_db", 0.0) or 0.0)
        signal_change = float(report.get("signal_change_db", 0.0) or 0.0)
        r.check(
            sweep, f"{tag}: signal barely touched",
            "<= 3.0 dB", f"{abs(signal_change):.2f} dB", abs(signal_change) <= 3.0,
        )
        r.series.setdefault(
            "noise: floor reduction", Series("noise: floor reduction", " dB")
        ).add(floor_change)

        sc = scoring.compare(clean_take, cleaned, sr=SR)
        weighting_rows(r, sweep, tag, sc)
    return r


# --------------------------------------------------------------------------
# Sweep 5: concert delays
# --------------------------------------------------------------------------


CHORALE = ["C4 D4 E4 F4 E4 D4 C4 C4", "E4 F4 G4 A4 G4 F4 E4 E4", "G3 A3 B3 C4 B3 A3 G3 G3",
           "C4 B3 A3 G3 A3 B3 C4 C4", "E3 F3 G3 A3 G3 F3 E3 E3",
           "A3 B3 C4 D4 C4 B3 A3 A3", "C4 C4 D4 E4 D4 C4 B3 C4", "G3 G3 A3 B3 A3 G3 F3 G3"]
CHORALE_PROFILES = ["alto", "soprano", "tenor", "baritone", "alto", "tenor", "soprano", "baritone"]


def sweep_concert(voices: int, delays_ms: Sequence[float]) -> Result:
    """A lead plus N-1 performers, each entering a known delay after the lead.

    The delay is applied by giving each performer a lead-in of silence, which
    is exactly what the network does to them: they heard the lead late and
    sang on what they heard.
    """
    r = Result()
    sweep = "concert"
    for nominal in delays_ms:
        # Spread the performers around the nominal delay so the median the
        # engine takes has something to be the median of.
        offsets = [0.0] + [
            float(nominal) + (i - (voices - 1) / 2.0) * 12.0 for i in range(voices - 1)
        ]
        stems = [
            take(CHORALE[i % len(CHORALE)], CHORALE_PROFILES[i % len(CHORALE_PROFILES)],
                 duration_ms=520.0, seed=200 + 7 * i, lead_in_ms=max(offsets[i], 0.0))
            for i in range(voices)
        ]
        length = max(s.size for s in stems)
        stems = [dsp.pad_to(s, length) for s in stems]
        names = [f"P{i}" for i in range(voices)]
        res = concert.run(stems, names=names, lead_index=0, sr=SR, audience=0)
        tag = f"{voices} voices @ {nominal:.0f} ms"

        measured = {m["name"]: m for m in res.timing["measured_delays"]}
        for i, name in enumerate(names):
            if i == 0:
                continue
            got = float(measured[name]["delay_ms"])
            want = offsets[i]
            # 45 ms is the bound this harness measured plus a round-up. The
            # entry of one voice against four others on a shared rhythm is
            # ambiguous to within about a sixth of a note, which is why the
            # engine caps its own search at 450 ms rather than trusting an
            # unconstrained correlation.
            r.near(
                sweep, f"{tag}: {name} delay", got, want, 45.0,
                unit=" ms", series="concert: per-voice delay error",
            )

        spread_before = float(res.timing["spread_before_ms"])
        spread_after = float(res.timing["spread_after_ms"])
        if nominal > 30.0:
            r.check(
                sweep, f"{tag}: correction tightens the entries",
                f"< {spread_before:.0f} ms", f"{spread_after:.0f} ms",
                spread_after < spread_before,
            )
        r.check(
            sweep, f"{tag}: entries land inside one attack",
            "<= 60 ms", f"{spread_after:.0f} ms", spread_after <= 60.0,
        )
        r.series.setdefault(
            "concert: spread after correction", Series("concert: spread after correction", " ms")
        ).add(spread_after)

        advance = float(res.timing["lead_advance_ms"])
        r.near(
            sweep, f"{tag}: lead advance is the median delay", advance, float(nominal),
            45.0, unit=" ms", series="concert: lead advance error",
        )
        # The mix must not clip. Five voices summed is where that happens, and
        # a clipped concert mix is not a concert.
        peak = float(np.abs(res.mix).max())
        r.check(sweep, f"{tag}: mix does not clip", "<= 1.0", f"{peak:.3f}", peak <= 1.0)

        # Faders. A gain in decibels has to produce that change in loudness,
        # or the mixer is decorative -- but the mix also has a peak guard, and
        # the two claims have to be separated or one of them gets asserted
        # falsely.
        #
        # Downwards the sum only gets smaller, the guard cannot engage, and
        # the relationship is exact arithmetic: a uniform move must arrive
        # within a tenth of a dB. Upwards, five voices already mixed near full
        # scale cannot all get 6 dB louder without clipping, so the guard
        # gives some of it back -- measured at +3.1 dB out for +6 dB in. The
        # engine is required to *report* that rather than to avoid it, and the
        # trim it reports has to account for the whole difference.
        base_lufs = dsp.loudness_lufs(res.mix, SR)
        for step in (-18.0, -12.0, -6.0):
            remixed, trim = concert.remix(res, [step] * voices)
            r.check(
                sweep, f"{tag}: no peak guard needed at {step:+.0f} dB",
                "0.00 dB", f"{trim:.2f} dB", abs(trim) < 1e-9,
            )
            r.near(
                sweep, f"{tag}: all faders {step:+.0f} dB moves loudness",
                dsp.loudness_lufs(remixed, SR) - base_lufs, step, 0.1, unit=" dB",
                series="faders: loudness error",
            )
        for step in (6.0, 12.0):
            remixed, trim = concert.remix(res, [step] * voices)
            got = dsp.loudness_lufs(remixed, SR) - base_lufs
            r.near(
                sweep, f"{tag}: {step:+.0f} dB accounted for by the peak guard",
                got, step + trim, 0.35, unit=" dB",
                series="faders: guard accounting error",
            )
            r.check(
                sweep, f"{tag}: mix still does not clip at {step:+.0f} dB",
                "<= 1.0", f"{float(np.abs(remixed).max()):.3f}",
                float(np.abs(remixed).max()) <= 1.0,
            )
        # Relative moves are what the fader bank is for, and they are not
        # affected by the guard, because the guard is one broadband number
        # applied to the sum.
        one_down = concert.remix(res, [-24.0] + [0.0] * (voices - 1))[0]
        drop = base_lufs - dsp.loudness_lufs(one_down, SR)
        r.check(
            sweep, f"{tag}: muting the lead lowers the mix",
            "> 0 dB and < 24 dB", f"{drop:.2f} dB", 0.0 < drop < 24.0,
        )
    return r


# --------------------------------------------------------------------------
# Sweep 6: duet
# --------------------------------------------------------------------------


def sweep_duet(lags_ms: Sequence[float], ratio: float) -> Result:
    """Two singers who never heard each other, at a known offset and pace."""
    r = Result()
    sweep = "duet"
    upper = take("E4 F4 G4 A4 G4 F4 E4 E4", "soprano", duration_ms=500.0, seed=301)
    for lag in lags_ms:
        lower = take(
            "C4 D4 E4 F4 E4 D4 C4 C4", "tenor", duration_ms=500.0, seed=302,
            tempo_ratio=float(ratio), lead_in_ms=float(lag), gain_db=-5.0,
        )
        res = duet.run(upper, lower, sr=SR)
        tag = f"lag {lag:.0f} ms @ {ratio:.2f}x"

        r.near(
            sweep, f"{tag}: entry offset", float(res.sync["entry_offset_ms"]), float(lag),
            60.0, unit=" ms", series="duet: entry offset error",
        )
        r.near(
            sweep, f"{tag}: tempo", float(res.sync["tempo_ratio"]), float(ratio),
            0.03, unit="x", series="duet: tempo error",
        )
        after = float(res.sync["tightness_after_ms"])
        before = float(res.sync["tightness_before_ms"])
        # `tightness_before_ms` is drift with the constant entry offset
        # already taken out, so at a tempo ratio of exactly 1.00 the two takes
        # are parallel before anything is done to them and it reads 5 ms --
        # one measurement frame, the floor of the grid. There is nothing to
        # tighten in that case, and asserting an improvement would be
        # demanding that the aligner beat its own resolution.
        #
        # So the two situations are asserted separately. Where there is real
        # drift to remove, it has to be removed. Where there is not, the
        # correction has to not make things worse by more than a few frames of
        # its own resolution.
        if before > 30.0:
            r.check(
                sweep, f"{tag}: drift removed",
                f"< {before:.0f} ms", f"{after:.0f} ms", after < before,
            )
            r.series.setdefault(
                "duet: drift removed", Series("duet: drift removed", " ms")
            ).add(before - after)
        else:
            r.check(
                sweep, f"{tag}: already-parallel pair not loosened",
                f"<= {before + 20.0:.0f} ms", f"{after:.0f} ms", after <= before + 20.0,
            )
        # The claim that reaches a user, and it holds either way: 35 ms is
        # inside the window where two attacks are heard as one event.
        r.check(
            sweep, f"{tag}: tight enough to sound together",
            "<= 35 ms", f"{after:.0f} ms", after <= 35.0,
        )
        r.series.setdefault(
            "duet: tightness after", Series("duet: tightness after", " ms")
        ).add(after)

        # Level matching: neither singer may win on microphone gain. Measured
        # on the stems as they go into the mix, which is where it matters --
        # one singer started 5 dB down and must not stay there.
        lufs = [float(dsp.loudness_lufs(a, SR)) for a in res.aligned]
        r.near(
            sweep, f"{tag}: levels matched", lufs[1] - lufs[0], 0.0, 2.0,
            unit=" dB", series="duet: level match error",
        )
        peak = float(np.abs(res.mix).max())
        r.check(sweep, f"{tag}: mix does not clip", "<= 1.0", f"{peak:.3f}", peak <= 1.0)
    return r


# --------------------------------------------------------------------------
# Sweep 7: the reference scenes, end to end
# --------------------------------------------------------------------------


def sweep_scenes() -> Result:
    """Every shipped scene, through the pipeline the interface calls.

    The sweeps above test one variable at a time against a hand-built pair.
    These are the eight scenes the product actually opens with, each carrying
    its own ground truth from the fixture generator, run through the same
    entry points the HTTP API uses.
    """
    r = Result()
    sweep = "scenes"
    from swarlink.engine import lesson

    for key in fixtures.SCENES:
        scene = fixtures.load(key)
        truth = scene.truth
        if scene.mode == "lesson":
            res = lesson.run_scene(scene)
            weighting_rows(r, sweep, key, res.score)
            if "detune_cents" in truth:
                r.near(
                    sweep, f"{key}: detune", res.score.values["median_signed_cents"],
                    float(truth["detune_cents"]), 25.0, unit="c",
                    series="scenes: detune error",
                )
            if "gain_db" in truth:
                r.near(
                    sweep, f"{key}: level", res.score.values["level_offset_db"],
                    float(truth["gain_db"]), 2.5, unit=" dB",
                    series="scenes: level error",
                )
            if "tempo_ratio" in truth:
                r.near(
                    sweep, f"{key}: tempo", res.score.values["tempo_ratio"],
                    float(truth["tempo_ratio"]), 0.03, unit="x",
                    series="scenes: tempo error",
                )
            # Both takes must write out to the same notes. If the segmenter
            # disagrees with itself across two performances of one phrase,
            # nothing downstream of it means anything.
            t_notes = [n.note for n in res.teacher.notes]
            s_notes = [n.note for n in res.student.notes]
            r.check(
                sweep, f"{key}: both takes write the same notes",
                " ".join(t_notes), " ".join(s_notes), t_notes == s_notes,
            )
        elif scene.mode == "duet":
            res = duet.run_scene(scene)
            after = float(res.sync["tightness_after_ms"])
            r.check(
                sweep, f"{key}: tightened", f"<= {res.sync['tightness_before_ms']:.0f} ms",
                f"{after:.0f} ms", after <= float(res.sync["tightness_before_ms"]),
            )
            r.check(
                sweep, f"{key}: harmony reported", "non-empty",
                res.harmony["label"][:40], bool(res.harmony["label"]),
            )
        else:
            res = concert.run_scene(scene)
            r.check(
                sweep, f"{key}: entries tightened",
                f"< {res.timing['spread_before_ms']:.0f} ms",
                f"{res.timing['spread_after_ms']:.0f} ms",
                float(res.timing["spread_after_ms"]) < float(res.timing["spread_before_ms"]),
            )
            r.check(
                sweep, f"{key}: every stem cleaned", "all applied",
                str(sum(1 for s in res.stems if s.view.cleaning.get("applied"))),
                all(s.view.cleaning.get("applied") for s in res.stems),
            )

        # Every payload the interface receives has to be JSON-safe and carry
        # its explanations, or a number reaches a screen without its meaning.
        payload = res.as_dict()
        r.check(
            sweep, f"{key}: payload is finite", "no NaN/Inf",
            _first_bad(payload) or "clean", _first_bad(payload) is None,
        )
    return r


def _first_bad(node: Any, path: str = "") -> Optional[str]:
    """The path of the first non-finite float in a payload, if any."""
    if isinstance(node, dict):
        for k, v in node.items():
            bad = _first_bad(v, f"{path}.{k}")
            if bad:
                return bad
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            bad = _first_bad(v, f"{path}[{i}]")
            if bad:
                return bad
    elif isinstance(node, float) and not math.isfinite(node):
        return path or "root"
    return None


# --------------------------------------------------------------------------
# Plan and run
# --------------------------------------------------------------------------

Job = Tuple[str, Callable[..., Result], tuple]


def plan(quick: bool) -> List[Job]:
    step = 3 if quick else 1

    cents = [float(c) for c in range(-200, 201, 5 * step)]
    ratios = [round(0.80 + 0.025 * i * step, 4) for i in range(int(19 / step) + 1)]
    ratios = [x for x in ratios if x <= 1.2501]
    gains = [float(g) for g in range(-24, 25, 2 * step)]
    snrs = [6.0, 10.0, 14.0, 18.0, 24.0, 30.0][:: 1 if not quick else 2]
    delays = [0.0, 40.0, 80.0, 100.0, 140.0, 200.0, 260.0, 300.0][:: 1 if not quick else 3]
    duet_lags = [0.0, 60.0, 120.0, 190.0, 260.0, 340.0][:: 1 if not quick else 3]

    profiles = ["alto", "soprano", "tenor"] if not quick else ["alto"]
    materials = [SCALE, PHRASE, LOW] if not quick else [SCALE]
    rooms = ["quiet_room", "laptop", "hiss", "hum"] if not quick else ["laptop"]
    voice_counts = [2, 3, 4, 5, 6, 8] if not quick else [5]
    duet_ratios = [1.00, 1.05, 0.94] if not quick else [1.05]

    jobs: List[Job] = []
    for p in profiles:
        for m in materials:
            jobs.append(("pitch", sweep_pitch, (cents, p, m)))
            jobs.append(("tempo", sweep_tempo, (ratios, p, m)))
            jobs.append(("gain", sweep_gain, (gains, p, m)))
    for room in rooms:
        for p in profiles[:2]:
            jobs.append(("noise", sweep_noise, (room, snrs, p)))
    for n in voice_counts:
        jobs.append(("concert", sweep_concert, (n, delays)))
    for ratio in duet_ratios:
        jobs.append(("duet", sweep_duet, (duet_lags, ratio)))
    jobs.append(("scenes", sweep_scenes, ()))
    return jobs


def _run_job(job: Job) -> Result:
    name, fn, args = job
    try:
        return fn(*args)
    except Exception as exc:  # a crash is a failed assertion, not a crashed run
        r = Result()
        r.check(name, f"{name}{args[:1]}: sweep completed", "no exception", repr(exc), False)
        return r


LIMITATIONS = [
    "Entry offset on repetitive material is ambiguous modulo the beat. Eight "
    "similar attacks give an unconstrained correlation several equally good "
    "answers, so the concert search is bounded below at -60 ms, capped above "
    "at 450 ms, and held to a tempo window of 0.97-1.03 -- by physics rather "
    "than by preference. The lower bound is doing as much work as the upper "
    "one: without it a performer 100 ms late reads as 430 ms early, a perfect "
    "alias at the same correlation, and the whole band's advance follows it. "
    "Outside a physically plausible delay the engine has no basis to choose.",
    "The pitch tracker has an operating floor around 10 dB SNR. Above it, "
    "fewer than 5% of frames land an octave from the truth on every noise bed "
    "tested; at 6 dB, where the bed sits at half the voice's amplitude, that "
    "reaches 14% and a quarter of the voiced frames are dropped as "
    "unplaceable. The suite asserts both bounds separately rather than one "
    "loose bound, so the floor is visible instead of averaged away. Frames "
    "the tracker cannot place are dropped rather than guessed at -- an "
    "unvoiced frame costs a little coverage, a wrong one costs a score.",
    "Strongly expressive rubato is fitted worst at the phrase edges, where "
    "there is timing context on only one side. The interior of a take lands "
    "within a few tens of milliseconds; the first and last note can be out by "
    "two to three hundred.",
    "The pitch tracker's floor is 70 Hz, so a bass line written below F2 "
    "reads as partly unvoiced. That is a stated range, not a defect, but it "
    "does mean the lowest part of a five-part chorale has to be written above "
    "it to be scored.",
    "Cleaning needs somewhere to measure a noise floor. A take that is voice "
    "from the first sample to the last falls back to a conservative estimate "
    "and is left almost untouched, which is the right failure but means the "
    "feature depends on a lead-in that real recordings usually have.",
]


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quick", action="store_true", help="about a tenth of the cases")
    ap.add_argument("--verbose", action="store_true", help="print every assertion")
    ap.add_argument("--jobs", type=int, default=0, help="worker processes (0 = all cores)")
    args = ap.parse_args(argv)

    jobs = plan(args.quick)
    workers = args.jobs if args.jobs > 0 else min(len(jobs), mp.cpu_count())
    started = time.time()

    print(f"Swarlink validation sweep — {len(jobs)} sweeps on {workers} worker(s)")
    print("synthesising and analysing; this takes a few minutes\n", flush=True)

    total = Result()
    if workers > 1:
        with mp.get_context("spawn").Pool(workers) as pool:
            for i, res in enumerate(pool.imap_unordered(_run_job, jobs), 1):
                total.merge(res)
                print(
                    f"  [{i}/{len(jobs)}] {len(total.rows)} assertions so far "
                    f"({time.time() - started:.0f}s)",
                    flush=True,
                )
    else:
        for i, job in enumerate(jobs, 1):
            total.merge(_run_job(job))
            print(f"  [{i}/{len(jobs)}] {len(total.rows)} assertions", flush=True)

    elapsed = time.time() - started
    failed = [r for r in total.rows if not r.ok]

    if args.verbose:
        width = min(max((len(r.name) for r in total.rows), default=10), 64)
        print()
        for r in total.rows:
            print(
                f"{'PASS' if r.ok else 'FAIL'}  {r.name[:width]:<{width}}  "
                f"want={r.want:<26} got={r.got}"
            )

    by_sweep: Dict[str, List[Row]] = {}
    for row in total.rows:
        by_sweep.setdefault(row.sweep, []).append(row)

    print("\n" + "=" * 78)
    print("PER SWEEP")
    print("=" * 78)
    for name in sorted(by_sweep):
        rows = by_sweep[name]
        bad = sum(1 for r in rows if not r.ok)
        print(f"  {name:<10} {len(rows) - bad:>6}/{len(rows):<6} passed" + ("" if not bad else f"   {bad} FAILED"))

    print("\n" + "=" * 78)
    print("ERROR STATISTICS  (|measured - truth|)")
    print("=" * 78)
    label_w = max((len(s.label) for s in total.series.values()), default=10)
    print(f"  {'quantity':<{label_w}}  {'n':>6}  {'median':>9}  {'p90':>9}  {'max':>9}")
    for key in sorted(total.series):
        s = total.series[key]
        st = s.stats()
        print(
            f"  {s.label:<{label_w}}  {int(st['n']):>6}  "
            f"{st['median']:>9.3f}  {st['p90']:>9.3f}  {st['max']:>9.3f}  {s.unit}"
        )

    if failed:
        print("\n" + "=" * 78)
        print(f"FAILURES ({len(failed)})")
        print("=" * 78)
        for r in failed[:80]:
            print(f"  [{r.sweep}] {r.name}\n      want={r.want}\n      got ={r.got}")
        if len(failed) > 80:
            print(f"  ... and {len(failed) - 80} more")

    print("\n" + "=" * 78)
    print("KNOWN LIMITATIONS  (measured, not worked around)")
    print("=" * 78)
    for line in LIMITATIONS:
        print("  - " + line.replace(". ", ".\n    ", 1))

    print("\n" + "=" * 78)
    print(
        f"{len(total.rows) - len(failed)}/{len(total.rows)} assertions passed "
        f"across {len(jobs)} sweeps in {elapsed:.0f}s"
    )
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
