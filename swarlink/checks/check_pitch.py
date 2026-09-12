"""Pitch tracker checks, including agreement with the team's YIN.

Two independent references are used, which is the point of the file:

* the synthesiser's own f0 curve, which is the true answer by construction;
* `audio-intelligence/yin.py`, the implementation Person C's comparison code
  imports. Swarlink's tracker is a batched rewrite of it, so if the two ever
  disagree by more than a few cents, one of them has a bug and every score
  built on top of it is suspect.

Run: python3 -m swarlink.checks.check_pitch
"""
from __future__ import annotations

import os
import sys
import time
from typing import List, Tuple

import numpy as np

from swarlink.engine import pitch, voice

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "audio-intelligence"))

SR = voice.SAMPLE_RATE
Row = Tuple[str, object, object, bool]
SCALE = ["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"]


TRANSITION_GUARD_MS = 60.0


def case_ground_truth() -> List[Row]:
    """Tracked pitch against the f0 the synthesiser actually used.

    Steady frames and note transitions are scored separately, and the tight
    bound is on the steady ones. Inside 60 ms of a note boundary a 40 ms
    analysis window straddles two pitches while the portamento is still
    gliding between them, so there is no single correct f0 for that window to
    report -- a large error there is a property of the question, not of the
    tracker. Lumping the two together hides a real 8-cent steady-state
    accuracy behind a 100-cent number that cannot be improved.
    """
    rows: List[Row] = []
    for prof in (voice.SOPRANO, voice.ALTO, voice.TENOR, voice.BARITONE, voice.BASS):
        low = prof.name in ("bass", "baritone")
        notes = voice.phrase(voice.transpose(SCALE, -12 if low else 0), 480.0)
        take = voice.sing(notes, prof, seed=21)
        c = pitch.track(take.audio, sr=SR)
        truth_idx = np.clip((c.time_ms * SR / 1000.0).astype(int), 0, take.f0.size - 1)
        truth = take.f0[truth_idx]
        err = np.abs(pitch.cents_between(c.hz, truth))

        bounds = np.array(
            [s["start_ms"] for s in take.notes] + [take.notes[-1]["end_ms"]]
        )
        near = np.min(np.abs(c.time_ms[:, None] - bounds[None, :]), axis=1) <= TRANSITION_GUARD_MS
        both = c.voiced & (truth > 0)
        steady = err[both & ~near]
        trans = err[both & near]

        med = float(np.median(steady))
        worst = float(np.max(steady))
        rows.append((f"{prof.name}: steady median", "<=5c", f"{med:.1f}c", med <= 5.0))
        rows.append((f"{prof.name}: steady worst", "<=25c", f"{worst:.1f}c", worst <= 25.0))
        rows.append((
            f"{prof.name}: transition median", "<=20c",
            f"{np.median(trans):.1f}c", float(np.median(trans)) <= 20.0,
        ))
        rows.append((
            f"{prof.name}: voiced fraction", ">=0.80",
            f"{c.voiced_fraction:.2f}", c.voiced_fraction >= 0.80,
        ))
    return rows


def case_agrees_with_team_yin() -> List[Row]:
    from yin import yin_pitch  # noqa: E402  (path set above)

    take = voice.sing(voice.phrase(SCALE, 480.0), voice.ALTO, seed=21)
    mine = pitch.track(take.audio, sr=SR)
    frame = int(SR * pitch.FRAME_MS / 1000.0)

    diffs = []
    for t, hz in zip(mine.time_ms, mine.hz):
        if not np.isfinite(hz):
            continue
        a = int(t * SR / 1000.0)
        seg = take.audio[a : a + frame]
        if seg.size < frame:
            break
        theirs, conf = yin_pitch(seg, SR)
        if theirs is None or conf < pitch.VOICED_CONF:
            continue
        diffs.append(abs(1200.0 * np.log2(hz / theirs)))

    med = float(np.median(diffs)) if diffs else 1e9
    p99 = float(np.percentile(diffs, 99)) if diffs else 1e9
    return [
        ("team yin: frames compared", ">=250", len(diffs), len(diffs) >= 250),
        ("team yin: median diff", "<=2c", f"{med:.2f}c", med <= 2.0),
        ("team yin: p99 diff", "<=25c", f"{p99:.2f}c", p99 <= 25.0),
    ]


def case_faster_than_loop() -> List[Row]:
    from yin import yin_pitch  # noqa: E402

    take = voice.sing(voice.phrase(SCALE * 2, 480.0), voice.ALTO, seed=4)
    t0 = time.perf_counter()
    pitch.track(take.audio, sr=SR)
    batched = time.perf_counter() - t0

    frame = int(SR * pitch.FRAME_MS / 1000.0)
    hop = int(SR * pitch.HOP_MS / 1000.0)
    t0 = time.perf_counter()
    for a in range(0, take.audio.size - frame, hop):
        yin_pitch(take.audio[a : a + frame], SR)
    looped = time.perf_counter() - t0

    speedup = looped / max(batched, 1e-9)
    realtime = (take.audio.size / SR) / max(batched, 1e-9)
    # The speedup ratio is reported but not asserted: it is a wall-clock
    # measurement on a shared machine and drifts either side of any
    # threshold tight enough to be interesting. What is asserted is the
    # property the product needs -- that a take is analysed far faster than
    # it was sung, so a lesson scores while the singer is still looking at
    # the screen, and the validation sweep finishes in minutes.
    return [
        ("throughput", ">=20x realtime", f"{realtime:.0f}x", realtime >= 20.0),
        ("vs per-frame loop", "not slower", f"{speedup:.1f}x", speedup >= 1.0),
    ]


def case_detune_is_measured() -> List[Row]:
    """A take sung N cents sharp must read as N cents sharp."""
    rows: List[Row] = []
    ref = pitch.track(voice.sing(voice.phrase(SCALE, 480.0), voice.ALTO, seed=9).audio, sr=SR)
    for truth in (-200.0, -50.0, -12.0, 0.0, 12.0, 50.0, 200.0):
        take = voice.sing(voice.phrase(SCALE, 480.0), voice.ALTO, detune_cents=truth, seed=9)
        c = pitch.track(take.audio, sr=SR)
        n = min(c.hz.size, ref.hz.size)
        dev = pitch.cents_between(c.hz[:n], ref.hz[:n])
        got = float(np.nanmedian(dev))
        rows.append((f"detune {truth:+.0f}c", truth, f"{got:.1f}c", abs(got - truth) <= 6.0))
    return rows


def case_note_segmentation() -> List[Row]:
    """Detected notes must match the notes that were synthesised."""
    rows: List[Row] = []
    for dur in (320.0, 480.0, 700.0):
        take = voice.sing(voice.phrase(SCALE, dur), voice.ALTO, seed=13)
        spans = pitch.segment_notes(pitch.track(take.audio, sr=SR))
        names = [s.note for s in spans]
        rows.append((f"{dur:.0f}ms notes: count", len(SCALE), len(names), len(names) == len(SCALE)))
        rows.append((
            f"{dur:.0f}ms notes: names", ",".join(SCALE), ",".join(names), names == SCALE,
        ))
        if len(spans) == len(SCALE):
            starts = np.array([s.start_ms for s in spans])
            truth = np.array([s["start_ms"] for s in take.notes])
            worst = float(np.max(np.abs(starts - truth)))
            rows.append((f"{dur:.0f}ms notes: onsets", "<=40ms", f"{worst:.0f}ms", worst <= 40.0))
    return rows


def main() -> int:
    rows: List[Row] = []
    for fn in (
        case_ground_truth,
        case_agrees_with_team_yin,
        case_faster_than_loop,
        case_detune_is_measured,
        case_note_segmentation,
    ):
        rows.extend(fn())
    width = max(len(str(r[0])) for r in rows)
    failed = [r for r in rows if not r[3]]
    for name, want, got, ok in rows:
        print(f"{'PASS' if ok else 'FAIL'}  {str(name):<{width}}  want={want!s:<22} got={got!s}")
    print(f"\n{len(rows) - len(failed)}/{len(rows)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
