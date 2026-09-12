"""Calibrate how much the aligner should trust its own DTW path.

The knot shrinkage in `align.warp_knots` has one free parameter: how sharply
the weight falls off as feature novelty drops. Tuning it against takes whose
true mapping is a shift and a stretch would drive it to maximum shrinkage and
produce an aligner that assumes music is metronomic. Tuning it against rubato
alone would drive it the other way and let the path wander through every
sustained note.

So both cases are measured together, against exactly computable ground truth:

* **linear** -- one singer uniformly quicker, which is the fixture family the
  rest of the checks use;
* **rubato** -- per-note durations that differ between the two takes, so the
  true time map is piecewise linear with a documented set of knots. This is
  the case a straight line *cannot* fit, and the DTW is the only thing that
  can find it.

The score is the worse of the two families, because an aligner that is
excellent on one and poor on the other is not usable.
"""
from __future__ import annotations

import sys
from typing import Dict, List, Sequence, Tuple

import numpy as np

sys.path.insert(0, __file__.rsplit("/swarlink/", 1)[0])

from swarlink.engine import align, bridge, dsp, fixtures, voice  # noqa: E402

SR = voice.SAMPLE_RATE
PITCHES = "C4 E4 G4 F4 E4 D4 G4 C4"
BASE_MS = 520.0
LEAD_A = 280.0
LEAD_B = 460.0

# Per-note duration multipliers for the rubato cases: the second singer
# stretches some notes and hurries others, averaging out to the same total, so
# no single tempo ratio can describe them.
RUBATO_SHAPES: Dict[str, Sequence[float]] = {
    "push_pull": (1.18, 1.12, 0.88, 0.84, 0.86, 1.10, 1.16, 0.92),
    "late_rush": (1.00, 1.02, 1.04, 1.08, 1.14, 0.92, 0.80, 0.78),
    "hold_first": (1.34, 1.06, 0.94, 0.92, 0.94, 0.96, 0.98, 0.90),
}


def _truth_knots(
    durations_a: Sequence[float], durations_b: Sequence[float]
) -> Tuple[np.ndarray, np.ndarray]:
    """Exact time map between two takes from their per-note durations.

    Note boundaries are the only times the two takes are known to correspond,
    and between them the mapping is linear because each note is sung at a
    constant rate. That makes the true map a piecewise-linear curve through
    the boundaries -- which is precisely the shape the aligner reports, so the
    comparison is apples to apples.
    """
    a = LEAD_A + np.concatenate([[0.0], np.cumsum(durations_a)])
    b = LEAD_B + np.concatenate([[0.0], np.cumsum(durations_b)])
    return a, b


def _truth_map(
    ref_ms: np.ndarray, ka: np.ndarray, kb: np.ndarray
) -> np.ndarray:
    return np.interp(ref_ms, ka, kb)


def _pair(
    durations_a: Sequence[float],
    durations_b: Sequence[float],
    room: str,
    snr_db: float,
    seed: int,
) -> Tuple[np.ndarray, np.ndarray]:
    names = PITCHES.split()
    notes_a = [voice.Note(p, d, "ah" if i % 2 else "oo", 1.0)
               for i, (p, d) in enumerate(zip(names, durations_a))]
    notes_b = [voice.Note(p, d, "ah" if i % 2 else "oo", 1.0)
               for i, (p, d) in enumerate(zip(names, durations_b))]
    a = voice.sing(notes_a, voice.SOPRANO, lead_in_ms=LEAD_A, seed=seed)
    b = voice.sing(notes_b, voice.TENOR, gain_db=-6.0, lead_in_ms=LEAD_B, seed=seed + 700)
    a_audio = fixtures.add_room(a.audio, room, snr_db, seed=seed * 3)
    b_audio = fixtures.add_room(b.audio, room, snr_db - 3.0, seed=seed * 5)
    ca, _ = bridge.clean(a_audio)
    cb, _ = bridge.clean(b_audio)
    return dsp.normalize_to_lufs(ca, -20.0)[0], dsp.normalize_to_lufs(cb, -20.0)[0]


def _cases() -> List[Dict[str, object]]:
    n = len(PITCHES.split())
    out: List[Dict[str, object]] = []
    for tempo in (1.00, 1.05, 0.93, 1.12, 0.88):
        out.append(
            {
                "family": "linear",
                "label": f"uniform {tempo:.2f}x",
                "a": [BASE_MS] * n,
                "b": [BASE_MS / tempo] * n,
            }
        )
    for name, shape in RUBATO_SHAPES.items():
        out.append(
            {
                "family": "rubato",
                "label": name,
                "a": [BASE_MS] * n,
                "b": [BASE_MS * s for s in shape],
            }
        )
    return out


ROOMS = (("silent", 99.0), ("quiet_room", 22.0), ("laptop", 20.0))


def measure(power: float) -> Dict[str, Dict[str, float]]:
    """Median and worst map error per family, at one shrinkage power."""
    align.NOVELTY_POWER = power
    per_family: Dict[str, List[float]] = {"linear": [], "rubato": []}
    worst_family: Dict[str, List[float]] = {"linear": [], "rubato": []}
    for i, case in enumerate(_cases()):
        ka, kb = _truth_knots(case["a"], case["b"])  # type: ignore[arg-type]
        for room, snr in ROOMS:
            a, b = _pair(case["a"], case["b"], room, snr, 23 + 7 * i)  # type: ignore[arg-type]
            _, al = align.align_and_warp(a, b, sr=SR)
            grid = np.linspace(ka[0], ka[-1], 80)
            err = np.abs(al.inverse_ms(grid) - _truth_map(grid, ka, kb))
            per_family[case["family"]].append(float(np.median(err)))  # type: ignore[index]
            worst_family[case["family"]].append(float(np.max(err)))  # type: ignore[index]
    return {
        fam: {
            "median": float(np.median(per_family[fam])),
            "p90_of_worst": float(np.percentile(worst_family[fam], 90)),
            "worst": float(np.max(worst_family[fam])),
        }
        for fam in per_family
    }


def main() -> int:
    print(f"{'power':>6}  {'linear med':>11} {'linear worst':>13}  "
          f"{'rubato med':>11} {'rubato worst':>13}  {'decision score':>14}")
    rows = []
    powers = [float(a) for a in sys.argv[1:]] or [0.0, 1.0, 1.5, 2.0, 3.0, 6.0]
    for power in powers:
        got = measure(power)
        lin, rub = got["linear"], got["rubato"]
        # The score to minimise is the worse family's worst case: an aligner
        # that is excellent on metronomic takes and loose on expressive ones
        # is not an aligner for music.
        score = max(lin["p90_of_worst"], rub["p90_of_worst"])
        rows.append((power, score))
        print(
            f"{power:>6.1f}  {lin['median']:>11.1f} {lin['worst']:>13.1f}  "
            f"{rub['median']:>11.1f} {rub['worst']:>13.1f}  {score:>14.1f}"
        )
    best = min(rows, key=lambda r: r[1])
    print(f"\nbest power = {best[0]:.1f} at {best[1]:.1f} ms")
    print("(power 0.0 means ignore the DTW entirely and use the straight line)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
