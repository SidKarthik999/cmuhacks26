"""Feature 3: a band of five, spread across five connections, made one.

The situation, stated exactly. A hundred people are in a call; five of them
are the band. The lead singer begins. Their voice takes some tens of
milliseconds to reach each of the other four -- encode, network, jitter
buffer, decode, speaker -- and each performer, being a musician, comes in
*on what they hear*. So each of them starts late by their own delay, and
their delays are not equal, because their connections are not equal.

Mix those five takes as they arrive and it is not a chord, it is a flam: five
attacks smeared over a tenth of a second, which is long enough to hear as
five separate events rather than one band.

The correction is the one the physics suggests. Nobody was wrong. The four
performers responded correctly to what reached them; the only thing out of
place is that the lead's own voice was never delayed. **So delay the lead.**
Push the lead forward by the time it took to reach the others, and all five
land together -- and, importantly, they land on the *performers'* shared
timeline rather than on a timeline none of them heard.

That single insight is what the whole feature turns on, and it is why this is
a mixing problem rather than a latency problem. There is no amount of network
engineering that removes the delay; there is a very small amount of
arithmetic that makes it irrelevant.

Three things this module does beyond the shift:

* **Measures the delay rather than assuming it.** A nominal tenth of a second
  is the right ballpark and the wrong number for any particular performer.
  Each stem is aligned against the lead to recover its own entry, and the
  advance applied to the lead is drawn from that distribution.
* **Cleans every voice separately, before mixing.** Noise reduction after the
  sum is hopeless: five noise floors have already been added together, and
  the quietest singer's hiss is indistinguishable from the loudest singer's
  breath. Cleaning per stem is also what makes the per-performer faders
  usable, because each fader then moves one voice rather than one voice plus
  a share of everybody's room.
* **Reports tightness before and after**, so the claim is checkable.

Audience members are counted, not mixed. Ninety-five listeners contribute no
audio; what they need is the mix and a seat.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import align, dsp, lesson, metrics, pitch

SAMPLE_RATE = 22050
DISPLAY_POINTS = 900
STEM_TARGET_LUFS = -23.0
# The lead is advanced by a robust centre of the measured delays rather than
# their mean. One performer on a bad connection should not drag the whole band
# forward, and the median is the shift that minimises the total displacement.
ADVANCE_STATISTIC = "median"
MAX_ADVANCE_MS = 400.0
# The physical bound on the unknown. A performer heard the lead through a
# network hop and a jitter buffer, which is tens of milliseconds and at worst
# a few hundred; anything larger is the estimator answering a different
# question. Slightly generous, so a bad connection is measured rather than
# clipped.
MAX_DELAY_MS = 450.0
# A band follows one beat. Allowing the estimator a 20% tempo search would let
# it explain a late entry as a fast performer, which on this material is a
# worse fit to the situation than to the data.
BAND_TEMPO_RANGE = (0.97, 1.03)


@dataclass
class Stem:
    """One performer's channel: their audio, their delay, their fader."""

    view: lesson.TakeView
    delay_ms: float
    confidence: float
    gain_db: float
    aligned: np.ndarray
    is_lead: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            **self.view.as_dict(),
            "is_lead": self.is_lead,
            "measured_delay_ms": round(self.delay_ms, 1),
            "delay_confidence": round(self.confidence, 3),
            "gain_db": round(self.gain_db, 2),
            "aligned_wave": dsp.downsample_envelope(self.aligned, DISPLAY_POINTS),
        }


@dataclass
class ConcertResult:
    stems: List[Stem]
    mix: np.ndarray
    naive_mix: np.ndarray
    lead_advance_ms: float
    timing: Dict[str, Any] = field(default_factory=dict)
    audience: int = 0
    sample_rate: int = SAMPLE_RATE

    @property
    def lead(self) -> Stem:
        return next(s for s in self.stems if s.is_lead)

    def as_dict(self, glossary: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "mode": "concert",
            "lead": self.lead.view.name,
            "audience": self.audience,
            "lead_advance_ms": round(self.lead_advance_ms, 1),
            "stems": [s.as_dict() for s in self.stems],
            "mix": {
                "wave": dsp.downsample_envelope(self.mix, DISPLAY_POINTS),
                "duration_ms": round(1000.0 * len(self.mix) / self.sample_rate, 1),
                "lufs": round(dsp.loudness_lufs(self.mix, self.sample_rate), 2),
            },
            "naive_mix": {
                "wave": dsp.downsample_envelope(self.naive_mix, DISPLAY_POINTS),
                "note": (
                    "The five channels summed as they arrived, with the lead "
                    "where the lead actually was. Kept so the correction can be "
                    "heard against something."
                ),
            },
            "timing": self.timing,
        }
        if glossary:
            out["glossary"] = metrics.glossary()
        return out

    def report(self) -> str:
        t = self.timing
        lines = [
            f"Concert {len(self.stems)} performers, lead = {self.lead.view.name}, "
            f"{self.audience} in the audience",
            f"  measured delays  " + ", ".join(
                f"{s.view.name.split(' (')[0]} {s.delay_ms:+.0f}ms"
                for s in self.stems if not s.is_lead
            ),
            f"  lead advanced    {self.lead_advance_ms:.0f} ms "
            f"({ADVANCE_STATISTIC} of the measured delays)",
            f"  spread of entries {t['spread_before_ms']:.0f} ms "
            f"-> {t['spread_after_ms']:.0f} ms  ({t['verdict']})",
            f"  worst pair       {t['worst_pair_before_ms']:.0f} ms "
            f"-> {t['worst_pair_after_ms']:.0f} ms",
            f"  faders           " + ", ".join(
                f"{s.view.name.split(' (')[0]} {s.gain_db:+.1f}dB" for s in self.stems
            ),
        ]
        for c in t.get("caveats", []):
            lines.append(f"  ! {c}")
        return "\n".join(lines)


def _pairwise_spread(entries: Sequence[float]) -> Tuple[float, float]:
    """(peak-to-peak, worst pair) of a set of entry times, in ms.

    Both are reported because they answer different questions. Peak-to-peak is
    how smeared the band is overall; the worst pair is what a listener
    actually notices, since one performer a tenth of a second out is audible
    whether or not the other four are together.
    """
    if not entries:
        return 0.0, 0.0
    arr = np.asarray(entries, dtype=np.float64)
    spread = float(arr.max() - arr.min())
    worst = 0.0
    for i in range(arr.size):
        for j in range(i + 1, arr.size):
            worst = max(worst, abs(float(arr[i] - arr[j])))
    return spread, worst


def _verdict(spread_ms: float) -> str:
    if spread_ms <= 20.0:
        return "one attack -- heard as a band, not five soloists"
    if spread_ms <= 40.0:
        return "tight; the spread is at the edge of audibility"
    if spread_ms <= 80.0:
        return "audibly loose -- a listener would hear the entries separate"
    return "a flam rather than a chord"


def measure_delays(
    stems: Sequence[np.ndarray],
    lead_index: int = 0,
    sr: int = SAMPLE_RATE,
    max_delay_ms: float = MAX_DELAY_MS,
) -> List[Tuple[float, float]]:
    """Each performer's entry relative to the lead, as (delay_ms, confidence).

    Measured with the full aligner rather than a plain cross-correlation,
    because band parts do not share pitch content -- a bass two octaves below
    the lead has almost no spectral overlap with them -- and because a
    performer who is both late and slightly quicker is two unknowns, not one.

    The search is capped at `max_delay_ms` and the tempo held near 1.0, both
    of which are statements about the situation rather than conveniences. A
    band is following one beat, so no performer is 10% quicker than the lead;
    and a performer heard the lead over a network, so nobody is a second and a
    half late. Without the cap, a five-part chorale -- five different lines on
    one shared rhythm -- had the bass measured at 1345 ms, which is two notes
    plus the true 86 ms delay, and the estimate was not wrong so much as
    answering an ambiguous question. Capping removes the ambiguity.
    """
    lead = stems[lead_index]
    out: List[Tuple[float, float]] = []
    for i, stem in enumerate(stems):
        if i == lead_index:
            out.append((0.0, 1.0))
            continue
        al = align.align(
            lead, stem, sr=sr,
            max_offset_ms=max_delay_ms,
            tempo_range=BAND_TEMPO_RANGE,
        )
        out.append((al.offset_ms, al.confidence))
    return out


def run(
    stems: Sequence[np.ndarray],
    names: Optional[Sequence[str]] = None,
    roles: Optional[Sequence[str]] = None,
    lead_index: int = 0,
    sr: int = SAMPLE_RATE,
    clean: bool = True,
    gains_db: Optional[Sequence[float]] = None,
    lead_advance_ms: Optional[float] = None,
    audience: int = 0,
    match_levels: bool = True,
) -> ConcertResult:
    """Clean, time-correct and mix a band of independently captured voices.

    `lead_advance_ms` overrides the measured advance, which is what the
    interface's slider is for: the correct value is a measurement, but hearing
    it move is how someone comes to believe the measurement.
    """
    if not stems:
        raise ValueError("a concert needs at least one performer")
    n = len(stems)
    names = list(names) if names else [f"Performer {i + 1}" for i in range(n)]
    roles = list(roles) if roles else [
        "lead" if i == lead_index else "performer" for i in range(n)
    ]
    gains = list(gains_db) if gains_db is not None else [0.0] * n

    views = [
        lesson.prepare(s, names[i], roles[i], sr, clean) for i, s in enumerate(stems)
    ]

    # Level-match before measuring as well as before mixing: the delay
    # estimator's features are level-invariant by construction, but the
    # faders are only meaningful if 0 dB means "as loud as everyone else"
    # rather than "as loud as this person's microphone happened to be".
    if match_levels:
        matched = [dsp.normalize_to_lufs(v.audio, STEM_TARGET_LUFS, sr) for v in views]
        audio = [m[0] for m in matched]
        match_db = [m[1] for m in matched]
    else:
        audio = [v.audio for v in views]
        match_db = [0.0] * n

    measured = measure_delays(audio, lead_index, sr)
    delays = [d for d, _c in measured]
    performer_delays = [d for i, d in enumerate(delays) if i != lead_index]

    if lead_advance_ms is None:
        if performer_delays:
            centre = (
                float(np.median(performer_delays))
                if ADVANCE_STATISTIC == "median"
                else float(np.mean(performer_delays))
            )
        else:
            centre = 0.0
        lead_advance_ms = float(np.clip(centre, 0.0, MAX_ADVANCE_MS))

    # Shift every channel so that all five sit on one clock: the lead moves
    # forward by the advance, and each performer moves by however much their
    # own delay differs from it. A performer whose delay was exactly the
    # advance does not move at all, which is the point -- the correction is
    # relative to the band, not to the wire.
    shifts = [
        lead_advance_ms if i == lead_index else lead_advance_ms - delays[i]
        for i in range(n)
    ]
    shifted = [dsp.shift_ms(audio[i], shifts[i], sr) for i in range(n)]
    length = max(s.size for s in shifted)
    aligned = [dsp.pad_to(s, length) for s in shifted]

    mix = dsp.mix(aligned, gains)
    naive_len = max(a.size for a in audio)
    naive_mix = dsp.mix([dsp.pad_to(a, naive_len) for a in audio], gains)

    entries_before = [0.0] + [d for i, d in enumerate(delays) if i != lead_index]
    entries_after = [
        delays[i] + shifts[i] - lead_advance_ms if i != lead_index else 0.0
        for i in range(n)
    ]
    spread_before, worst_before = _pairwise_spread(entries_before)
    spread_after, worst_after = _pairwise_spread(entries_after)

    # Verify against the audio rather than the arithmetic. The shift is exact
    # by construction, so a spread computed from the shifts alone would be
    # zero no matter how badly the delays were estimated -- which would make
    # the "after" number a restatement of the plan instead of a measurement.
    residual = _residual_entries(aligned, lead_index, sr)
    res_spread, res_worst = _pairwise_spread(residual)

    caveats: List[str] = []
    weak = [
        names[i] for i, (_d, c) in enumerate(measured)
        if i != lead_index and c < 0.45
    ]
    if weak:
        caveats.append(
            f"Entry for {', '.join(weak)} was hard to measure -- few clear "
            "attacks -- so that part's correction is a best estimate."
        )
    # Only worth saying when the residual is both worse than planned and
    # actually audible. The shift is exact arithmetic, so the planned spread
    # is usually zero and any measurement noise beats it; flagging 25 ms
    # against a plan of 0 reports a rounding error as a failure.
    if res_spread > 40.0 and res_spread > spread_after + 20.0:
        caveats.append(
            f"The measured spread after correction ({res_spread:.0f} ms) is "
            f"wider than the planned {spread_after:.0f} ms, so at least one "
            "entry estimate is off."
        )

    timing = {
        "nominal_delay_ms": 100.0,
        "measured_delays": [
            {
                "name": names[i],
                "delay_ms": round(delays[i], 1),
                "confidence": round(measured[i][1], 3),
                "shift_applied_ms": round(shifts[i], 1),
                # Measured on the corrected audio, not derived from the shift.
                # This is the number the "after" picture has to be drawn from;
                # the planned figure is zero for everyone by construction.
                "residual_ms": round(residual[i], 1),
            }
            for i in range(n)
        ],
        "lead_advance_ms": round(lead_advance_ms, 1),
        "advance_statistic": ADVANCE_STATISTIC,
        "spread_before_ms": round(spread_before, 1),
        "spread_after_ms": round(res_spread, 1),
        "worst_pair_before_ms": round(worst_before, 1),
        "worst_pair_after_ms": round(res_worst, 1),
        "planned_spread_after_ms": round(spread_after, 1),
        "verdict": _verdict(res_spread),
        "verdict_before": _verdict(spread_before),
        "caveats": caveats,
        "note": (
            "Nobody played late. Each performer came in on the lead's voice as "
            "it reached them, which is correct musicianship; the only channel "
            "never delayed was the lead's own. So the lead is advanced to the "
            "performers' shared clock rather than the performers being dragged "
            "back to the lead's."
        ),
    }

    return ConcertResult(
        stems=[
            Stem(
                view=views[i],
                delay_ms=delays[i],
                confidence=measured[i][1],
                gain_db=gains[i],
                aligned=aligned[i],
                is_lead=(i == lead_index),
            )
            for i in range(n)
        ],
        mix=mix,
        naive_mix=naive_mix,
        lead_advance_ms=lead_advance_ms,
        timing={**timing, "match_gain_db": [round(g, 2) for g in match_db]},
        audience=audience,
        sample_rate=sr,
    )


def _residual_entries(
    aligned: Sequence[np.ndarray], lead_index: int, sr: int
) -> List[float]:
    """Remaining entry error per performer, measured on the corrected audio."""
    lead = aligned[lead_index]
    out: List[float] = []
    for i, stem in enumerate(aligned):
        if i == lead_index:
            out.append(0.0)
            continue
        lag, _conf = align.estimate_offset_ms(lead, stem, sr=sr, max_offset_ms=350.0)
        out.append(lag)
    return out


def remix(
    result: ConcertResult, gains_db: Sequence[float]
) -> np.ndarray:
    """Re-sum the already-corrected stems at new fader settings.

    Separate from `run` because moving a fader must not re-run the analysis.
    Cleaning and alignment are the expensive, deterministic part; the mix is
    a weighted sum, and the interface needs it at interactive speed.
    """
    gains = list(gains_db)
    if len(gains) != len(result.stems):
        raise ValueError(
            f"{len(gains)} fader values for {len(result.stems)} performers"
        )
    return dsp.mix([s.aligned for s in result.stems], gains)


def run_scene(
    scene: Any,
    clean: bool = True,
    gains_db: Optional[Sequence[float]] = None,
    lead_advance_ms: Optional[float] = None,
) -> ConcertResult:
    """Run a fixture scene, for the checks and the demo's canned concerts."""
    lead_name = scene.truth.get("lead", scene.parts[0].name)
    lead_index = next(
        (i for i, p in enumerate(scene.parts) if p.name == lead_name), 0
    )
    return run(
        [p.audio for p in scene.parts],
        names=[f"{p.name} ({p.profile})" for p in scene.parts],
        roles=[p.role for p in scene.parts],
        lead_index=lead_index,
        sr=scene.sample_rate,
        clean=clean,
        gains_db=gains_db,
        lead_advance_ms=lead_advance_ms,
        audience=int(scene.truth.get("audience", 0)),
    )
