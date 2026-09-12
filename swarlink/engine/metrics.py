"""What every number on screen means.

A score with no explanation is not feedback, it is a verdict. This module is
the single place where each metric's unit, range, direction, and plain-English
reading live, so the interface never has to hard-code a caption and a number
can never drift away from its own description.

Every `Metric` carries:

``label``    what to call it in the interface
``unit``     the unit, so the number is never bare
``meaning``  one sentence on what the quantity is
``reading``  how to interpret the value you are looking at
``good``     the direction of "better", for colouring and sorting
``anchors``  reference values with names, which is what makes a number like
             "31 cents" mean something to someone who has never heard of a
             cent

The anchors are not decoration. They are why the lesson view can say "31
cents sharp, about a third of the way to the next semitone" instead of "31".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    meaning: str
    reading: str
    good: str = "low"  # "low", "high", or "zero"
    anchors: Tuple[Tuple[float, str], ...] = ()

    def display(self, value: Optional[float]) -> str:
        """The value as a person should read it, unit attached.

        Formatted here rather than at each call site so a metric's precision
        is a property of the metric. Cents and milliseconds are whole numbers
        -- nobody acts on a tenth of a cent -- while ratios and correlations
        need the decimals to say anything at all.
        """
        if value is None:
            return "--"
        if self.unit in ("cents", "ms"):
            return f"{value:+.0f} {self.unit}" if self.good == "zero" else f"{value:.0f} {self.unit}"
        if self.unit == "dB":
            return f"{value:+.2f} dB" if self.good == "zero" else f"{value:.2f} dB"
        if self.unit == "%":
            return f"{value:.1f}%"
        # A tempo ratio at one decimal place is 1.1 whether the singer was 6%
        # quick or 14% quick, which is the whole content of the number.
        if self.unit in ("", "x", "ratio"):
            return f"{value:.3f}{self.unit}"
        return f"{value:.1f} {self.unit}".strip()

    def describe(self, value: Optional[float]) -> Dict[str, object]:
        """Package a measured value with everything needed to render it."""
        return {
            "key": self.key,
            "label": self.label,
            "unit": self.unit,
            "value": None if value is None else round(float(value), 3),
            "display": self.display(value),
            "meaning": self.meaning,
            "reading": self.reading,
            "good": self.good,
            "anchor": None if value is None else self.nearest_anchor(value),
            "anchors": [{"value": v, "label": n} for v, n in self.anchors],
        }

    def nearest_anchor(self, value: float) -> Optional[str]:
        if not self.anchors:
            return None
        return min(self.anchors, key=lambda a: abs(a[0] - abs(value)))[1]


# --------------------------------------------------------------- pitch

CENTS_ANCHORS: Tuple[Tuple[float, str], ...] = (
    (0.0, "dead centre"),
    (5.0, "below what anyone can hear"),
    (15.0, "a good singer's natural wobble"),
    (25.0, "the edge of sounding in tune"),
    (50.0, "a quarter tone -- audibly out"),
    (100.0, "a whole semitone -- the wrong note"),
)

MEAN_ABS_CENTS = Metric(
    key="mean_abs_cents",
    label="Pitch accuracy",
    unit="cents",
    meaning=(
        "How far the student's pitch sits from the teacher's, averaged over "
        "every moment both of them were singing. A cent is a hundredth of a "
        "semitone, so 100 cents is the distance to the next note on a piano."
    ),
    reading=(
        "Under 25 cents reads as in tune. Around 50 is a quarter tone and "
        "audible to anyone. At 100 the student is singing a different note."
    ),
    anchors=CENTS_ANCHORS,
)

MEDIAN_SIGNED_CENTS = Metric(
    key="median_signed_cents",
    label="Pitch bias",
    unit="cents",
    meaning=(
        "Whether the student is consistently above or below the teacher, "
        "rather than merely scattered. Positive is sharp, negative is flat."
    ),
    reading=(
        "A large bias with small scatter is the easiest fault to fix: the "
        "student is singing the shape correctly but anchored to the wrong "
        "reference. Scatter without bias is an intonation problem instead."
    ),
    good="zero",
    anchors=CENTS_ANCHORS,
)

CENTS_SPREAD = Metric(
    key="cents_spread",
    label="Pitch stability",
    unit="cents",
    meaning=(
        "The spread of the student's deviation once the constant bias is "
        "removed -- how steady the intonation is from moment to moment."
    ),
    reading=(
        "Low spread means the pitch is under control even if it is in the "
        "wrong place. High spread means it wanders, which is a breath "
        "support and listening problem rather than a tuning one."
    ),
    anchors=CENTS_ANCHORS,
)

IN_TUNE_PERCENT = Metric(
    key="in_tune_percent",
    label="Time in tune",
    unit="%",
    meaning=(
        "The share of sung time within 25 cents of the teacher -- the "
        "tolerance inside which two voices are heard as the same note."
    ),
    reading=(
        "This is the most forgiving pitch number and the one that moves "
        "first as a student improves. Above 90% is a confident performance."
    ),
    good="high",
    anchors=((50.0, "half the phrase"), (75.0, "most of it"), (90.0, "confident"), (100.0, "throughout")),
)

# -------------------------------------------------------------- volume

DB_ANCHORS: Tuple[Tuple[float, str], ...] = (
    (0.0, "identical"),
    (1.0, "barely perceptible"),
    (3.0, "clearly audible"),
    (6.0, "half or double the loudness"),
    (12.0, "a different dynamic marking"),
)

LEVEL_OFFSET_DB = Metric(
    key="level_offset_db",
    label="Level offset",
    unit="dB",
    meaning=(
        "How much louder or quieter the student's whole take is than the "
        "teacher's, measured as broadcast loudness (LUFS)."
    ),
    reading=(
        "This one is reported but deliberately not scored. It is mostly "
        "microphone distance and gain, which is a room problem rather than a "
        "musical one, and penalising it would mark a student down for "
        "sitting further from their laptop."
    ),
    good="zero",
    anchors=DB_ANCHORS,
)

DYNAMICS_ERROR_DB = Metric(
    key="dynamics_error_db",
    label="Dynamics match",
    unit="dB",
    meaning=(
        "How closely the student's loudness *shape* follows the teacher's "
        "once the overall level difference is taken out: do they swell and "
        "taper in the same places, by the same amount."
    ),
    reading=(
        "This is the scored volume number, because it is the musical one. "
        "Under 2 dB is a faithful copy of the phrasing. Above 6 dB usually "
        "means the student sang the line flat while the teacher shaped it."
    ),
    anchors=DB_ANCHORS,
)

DYNAMIC_RANGE_DB = Metric(
    key="dynamic_range_db",
    label="Dynamic range",
    unit="dB",
    meaning="The distance from the quietest to the loudest moment of a take.",
    reading=(
        "Compare the two takes rather than reading either alone. A student "
        "with far less range than the teacher is singing everything at one "
        "volume; far more usually means uncontrolled rather than expressive."
    ),
    good="high",
    anchors=((3.0, "almost flat"), (8.0, "modest shaping"), (15.0, "expressive"), (25.0, "very wide")),
)

ENVELOPE_CORRELATION = Metric(
    key="envelope_correlation",
    label="Phrasing agreement",
    unit="",
    meaning=(
        "Correlation between the two loudness curves over time: 1.0 means "
        "every swell and taper happens at the same moment."
    ),
    reading=(
        "Reads phrasing rather than level. High correlation with a large "
        "dynamics error means the student shapes the phrase in the right "
        "places but exaggerates or flattens it."
    ),
    good="high",
    anchors=((0.0, "unrelated"), (0.5, "loosely following"), (0.8, "clearly together"), (1.0, "identical")),
)

# -------------------------------------------------------------- timing

TIMING_ANCHORS: Tuple[Tuple[float, str], ...] = (
    (0.0, "simultaneous"),
    (10.0, "tighter than a studio ensemble"),
    (20.0, "still feels locked together"),
    (30.0, "audibly loose"),
    (50.0, "heard as an echo"),
    (100.0, "a different beat"),
)

ONSET_OFFSET_MS = Metric(
    key="onset_offset_ms",
    label="Entry offset",
    unit="ms",
    meaning=(
        "How late the second singer came in relative to the first, before "
        "any correction. Positive is late."
    ),
    reading=(
        "A constant offset is the easy case: one number fixes the whole "
        "take. Under about 20 ms two voices are heard as one attack; past "
        "50 ms the later one is heard as a separate event."
    ),
    good="zero",
    anchors=TIMING_ANCHORS,
)

RESIDUAL_OFFSET_MS = Metric(
    key="residual_offset_ms",
    label="Residual offset",
    unit="ms",
    meaning="The timing error still left after alignment -- the honest 'after' number.",
    reading=(
        "This is what the alignment is judged on. It is measured on the "
        "aligned audio rather than predicted from the model that produced "
        "it, so it cannot flatter itself."
    ),
    anchors=TIMING_ANCHORS,
)

WORST_WINDOW_MS = Metric(
    key="worst_window_ms",
    label="Worst moment",
    unit="ms",
    meaning=(
        "The largest timing error found in any 0.8-second window of the "
        "take, rather than the average over all of it."
    ),
    reading=(
        "A take can average near zero and still fall apart in one bar. This "
        "is the number an ensemble actually notices, so it is the one the "
        "aligner is tuned against."
    ),
    anchors=TIMING_ANCHORS,
)

TEMPO_RATIO = Metric(
    key="tempo_ratio",
    label="Tempo ratio",
    unit="x",
    meaning=(
        "How much faster the second take is than the first. 1.00 is the same "
        "tempo; 1.08 is eight percent quicker."
    ),
    reading=(
        "Anything other than 1.00 means a fixed delay cannot make the two "
        "line up -- they diverge as the phrase goes on, which is why the "
        "aligner warps time instead of just shifting it."
    ),
    good="zero",
    anchors=((1.0, "same tempo"), (1.05, "slightly rushed"), (1.15, "noticeably rushed")),
)

DRIFT_SPREAD_MS = Metric(
    key="drift_spread_ms",
    label="Drift",
    unit="ms",
    meaning=(
        "How far apart the two takes would have ended up by the end of the "
        "phrase, with no correction, given their tempo difference."
    ),
    reading=(
        "This is the number that shows why a single delay is not enough. "
        "Two singers 8% apart in tempo are a quarter of a second apart after "
        "four bars, even if they started together."
    ),
    anchors=TIMING_ANCHORS + ((250.0, "a beat apart"),),
)

CONFIDENCE = Metric(
    key="confidence",
    label="Confidence",
    unit="",
    meaning=(
        "How strongly the audio supported the alignment that was chosen, "
        "from the peak of the onset cross-correlation."
    ),
    reading=(
        "Low confidence does not mean the singers were bad; it usually means "
        "the material has few clear attacks to lock onto, such as one long "
        "sustained vowel. Treat the timing numbers as soft below 0.5."
    ),
    good="high",
    anchors=((0.3, "weak evidence"), (0.6, "solid"), (0.85, "unambiguous")),
)

VOICED_PERCENT = Metric(
    key="voiced_percent",
    label="Voiced time",
    unit="%",
    meaning="The share of the take where a definite pitch was detectable.",
    reading=(
        "Low values mean breath, consonants, silence, or a signal too noisy "
        "to track -- and they cap how much of the take the pitch score is "
        "actually based on. Shown so a high score on a mostly-silent take "
        "cannot pass unnoticed."
    ),
    good="high",
    anchors=((40.0, "mostly not singing"), (70.0, "normal for a lyric"), (95.0, "a sustained exercise")),
)

# --------------------------------------------------------------- scores

PITCH_SCORE = Metric(
    key="pitch_score",
    label="Pitch score",
    unit="/100",
    meaning=(
        "Pitch accuracy mapped onto 0-100 through fixed perceptual landmarks "
        "rather than a curve chosen to look generous."
    ),
    reading=(
        "The landmarks are the anchors of pitch accuracy: dead centre is "
        "100, the edge of in-tune is 90, a quarter tone is 70, a whole "
        "semitone out is 30. Worth 70% of the overall score."
    ),
    good="high",
)

VOLUME_SCORE = Metric(
    key="volume_score",
    label="Volume score",
    unit="/100",
    meaning=(
        "How closely the student's dynamic shaping follows the teacher's, "
        "mapped onto 0-100. Overall level difference is excluded on purpose."
    ),
    reading=(
        "Matching the teacher's swell within 2 dB scores about 90. Singing "
        "the line at one flat volume while the teacher shapes it costs most "
        "of this score. Worth 30% of the overall score."
    ),
    good="high",
)

OVERALL_SCORE = Metric(
    key="overall_score",
    label="Overall",
    unit="/100",
    meaning=(
        "Seventy percent pitch, thirty percent volume. Exactly that, with no "
        "hidden extra terms -- timing is measured and reported separately "
        "rather than folded in."
    ),
    reading=(
        "The weighting says that singing the right note matters more than "
        "matching the teacher's loudness, but that phrasing is not free. The "
        "arithmetic is checked against the two sub-scores on every run."
    ),
    good="high",
)

PITCH_WEIGHT = 0.70
VOLUME_WEIGHT = 0.30

ALL: Tuple[Metric, ...] = (
    OVERALL_SCORE,
    PITCH_SCORE,
    VOLUME_SCORE,
    MEAN_ABS_CENTS,
    MEDIAN_SIGNED_CENTS,
    CENTS_SPREAD,
    IN_TUNE_PERCENT,
    LEVEL_OFFSET_DB,
    DYNAMICS_ERROR_DB,
    DYNAMIC_RANGE_DB,
    ENVELOPE_CORRELATION,
    ONSET_OFFSET_MS,
    RESIDUAL_OFFSET_MS,
    WORST_WINDOW_MS,
    TEMPO_RATIO,
    DRIFT_SPREAD_MS,
    CONFIDENCE,
    VOICED_PERCENT,
)

BY_KEY: Dict[str, Metric] = {m.key: m for m in ALL}


def describe(values: Dict[str, Optional[float]], keys: Optional[Sequence[str]] = None) -> List[Dict[str, object]]:
    """Turn a dict of measurements into renderable, self-explaining entries."""
    order = keys if keys is not None else [m.key for m in ALL if m.key in values]
    return [BY_KEY[k].describe(values.get(k)) for k in order if k in BY_KEY]


def glossary() -> List[Dict[str, object]]:
    """Every metric with its explanation and no value, for a help panel."""
    return [m.describe(None) for m in ALL]


def cents_in_words(cents: float) -> str:
    """Phrase a cents deviation the way a teacher would say it out loud."""
    mag = abs(cents)
    direction = "sharp" if cents > 0 else "flat"
    if mag < 6.0:
        return "dead on"
    if mag < 16.0:
        return f"a shade {direction}"
    if mag < 26.0:
        return f"slightly {direction}"
    if mag < 51.0:
        return f"{direction} by {mag:.0f} cents, around a quarter tone"
    if mag < 90.0:
        return f"{direction} by {mag:.0f} cents, most of a semitone"
    semitones = mag / 100.0
    return f"{direction} by {semitones:.1f} semitones -- a different note"


def timing_in_words(ms: float) -> str:
    mag = abs(ms)
    direction = "behind" if ms > 0 else "ahead"
    if mag < 10.0:
        return "locked together"
    if mag < 20.0:
        return f"{mag:.0f} ms {direction}, still tight"
    if mag < 50.0:
        return f"{mag:.0f} ms {direction}, audibly loose"
    if mag < 120.0:
        return f"{mag:.0f} ms {direction} -- heard as an echo"
    return f"{mag / 1000.0:.2f} s {direction} -- a different beat"


_UNSET = object()


def score_curve(value: float, anchors: Sequence[Tuple[float, float]]) -> float:
    """Piecewise-linear map from a measurement to 0-100.

    A smooth exponential would be tidier to write down and impossible to
    justify: nobody can say why a particular decay constant is the right one.
    Interpolating between named perceptual landmarks means every part of the
    curve can be defended by pointing at the landmark on either side, and the
    result is still monotone, which is the property the sweep checks.
    """
    xs = [a[0] for a in anchors]
    ys = [a[1] for a in anchors]
    if value <= xs[0]:
        return float(ys[0])
    if value >= xs[-1]:
        return float(ys[-1])
    for i in range(len(xs) - 1):
        if value <= xs[i + 1]:
            span = xs[i + 1] - xs[i]
            frac = 0.0 if span <= 0 else (value - xs[i]) / span
            return float(ys[i] + frac * (ys[i + 1] - ys[i]))
    return float(ys[-1])


# The landmarks behind PITCH_SCORE and VOLUME_SCORE, kept next to the metric
# descriptions they are explained by rather than buried in the scorer.
PITCH_CURVE: Tuple[Tuple[float, float], ...] = (
    (0.0, 100.0),
    (10.0, 96.0),
    (25.0, 90.0),
    (50.0, 70.0),
    (100.0, 30.0),
    (200.0, 0.0),
)

VOLUME_CURVE: Tuple[Tuple[float, float], ...] = (
    (0.0, 100.0),
    (1.0, 96.0),
    (2.0, 90.0),
    (4.0, 75.0),
    (8.0, 45.0),
    (15.0, 0.0),
)
