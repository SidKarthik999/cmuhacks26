# Checks

Four programs. Three are fast property suites meant to be run on every
change; the fourth is a wide sweep meant to be run before you believe a
number. None of them need pytest, a fixture directory, or a network.

```bash
python3 -m swarlink.checks.check_pitch      # ~4 s
python3 -m swarlink.checks.check_align      # ~9 s
python3 -m swarlink.checks.check_scoring    # ~8 s
python3 -m swarlink.checks.validate --quick # ~50 s
python3 -m swarlink.checks.validate         # the full sweep
```

Each prints one `PASS`/`FAIL` line per assertion, a tally, and exits nonzero
if anything failed.

Everything is synthesised. That is the reason these checks can assert
anything at all: the answer is a number that was *chosen* and then injected
into the synthesiser, not a number somebody measured and decided to trust. A
student take detuned by 70 cents is 70 cents out because it was built that
way, so "the scorer says 69.9" is a measurement of the scorer rather than an
opinion about it.

## The three property suites

**`check_pitch`** — the tracker against two independent references: the
synthesiser's own f0 curve, and `audio-intelligence/yin.py`, the
implementation the rest of the team's comparison code imports. Swarlink's
tracker is a batched rewrite of that one, so a disagreement of more than a
few cents means one of them has a bug and every score above it is suspect.
Also covers note segmentation and throughput.

**`check_align`** — offset and tempo recovery against injected values, and
residual offset measured in 0.8 s windows across the take rather than only
globally, because a warp can be right on average and a beat out in bar
three. Onset *counts* are deliberately not asserted; the phase vocoder
leaves small artefacts that a counter sees and a listener does not.

**`check_scoring`** — properties, not expected values. The 70/30 weighting
holds exactly to floating point on every case; a take further out of tune
never scores above one that is closer, at every step of a sweep; a student
who differs only by microphone gain is not penalised, because that is a room
and not a musician; a student who came in late but sang correctly scores as
correct, which is the entire reason alignment runs before scoring.

## The sweep

`validate.py` runs seven sweeps over a few thousand synthesised cases:
detuning from -200 to +200 cents in 5-cent steps across three voice types and
three kinds of material, tempo from 0.80x to 1.25x, gain from -24 to +24 dB,
four noise beds at six SNRs, concert bands of two to eight voices at eight
delays, duets at six lags and three tempo ratios, and all eight shipped
scenes end to end through the same entry points the HTTP API calls.

It is structured differently from the three suites above, in three ways that
matter:

- **Error statistics, not just verdicts.** Every recovered quantity is
  accumulated into a series and reported as median, p90 and max. A suite that
  only prints `PASS` tells you a bound held; this tells you how much room was
  left, which is the number worth quoting.
- **Monotonicity asserted step by step**, not end to end. A scorer that
  mostly decreases but has a bump in the middle passes an
  endpoint comparison and fails this.
- **A `KNOWN LIMITATIONS` block.** Measured limits are printed on every run
  next to the passing tally, so they are part of the result rather than a
  footnote somebody has to go and find.

`--quick` runs about a tenth of the cases and is the one to use in a loop.
`--jobs N` sets worker processes; it defaults to one per core and the sweep is
CPU-bound in numpy, so this is close to linear.

## On the tolerances

Every tolerance in these files is a number the harness measured and then
rounded outwards. Some are loose on purpose and say so at the point of
assertion — the entry of one voice against four others on a shared rhythm is
genuinely ambiguous to within a fraction of a note, and a tight bound there
would be asserting a precision the signal does not contain.

Where a bound is graded, the grading is visible. The tracker's octave-slip
bound is 5% above 10 dB SNR and 15% below it, as two separate assertions,
because one bound covering both would either be meaningless at 24 dB or
failing at 6 dB. The floor is a real property of the tracker and the suite's
job is to show it, not to average it away.

## Calibration

`calibrate_warp.py` is not a check and is not expected to be run routinely.
It fits the one free parameter in `align.warp_knots` — how sharply knot
weight falls off as feature novelty drops — against two fixture families at
once: takes that differ by a uniform stretch, and takes with genuine rubato
whose true time map is piecewise linear with known knots. Calibrating against
either alone produces a broken aligner in opposite directions, so it scores
the *worse* of the two.
