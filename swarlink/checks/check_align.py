"""Alignment checks with known-correct answers.

Every case is synthesised, so the right answer is a number we chose rather
than a number we measured. Run: python3 -m swarlink.checks.check_align

What is asserted, and why those things and not others:

* Offset and tempo recovery, against the values injected into the synth.
* Residual offset measured in 0.8 s windows across the whole take, not just
  globally -- a warp can be right on average and a beat out in bar three.
* Envelope and onset-envelope correlation with the reference, which is the
  closest cheap proxy for "these two now sound like one performance".
* Chroma similarity before and after warping, because a time warp that
  transposes the singer has broken the thing it was meant to preserve.

Onset *counts* are deliberately not asserted. The phase vocoder leaves small
amplitude ripples that a peak picker reads as extra notes even when the
timing is exact, so counting them measures the detector's threshold rather
than the aligner.
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np

from swarlink.engine import align, dsp, voice

SR = voice.SAMPLE_RATE
SCALE = ["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"]
Row = Tuple[str, object, object, bool]


def windowed_residual_ms(ref: np.ndarray, other: np.ndarray, win_ms: float = 800.0) -> List[float]:
    win = int(win_ms * SR / 1000.0)
    out = []
    for a in range(0, max(ref.size - win, 1), max(win // 2, 1)):
        d, _ = align.estimate_offset_ms(
            ref[a : a + win], other[a : a + win], sr=SR, max_offset_ms=300.0
        )
        out.append(float(d))
    return out


def case_offset_recovery() -> List[Row]:
    rows: List[Row] = []
    base = voice.sing(voice.phrase(SCALE, 480.0), voice.ALTO, seed=11)
    for truth in (0.0, 60.0, 150.0, 300.0, -120.0):
        if truth >= 0:
            other = dsp.shift_ms(base.audio, truth, sr=SR)
        else:
            other = base.audio[int(-truth * SR / 1000.0) :]
        got, _ = align.estimate_offset_ms(base.audio, other, sr=SR)
        rows.append((f"offset {truth:+.0f}ms", truth, got, abs(got - truth) <= 10.0))
    return rows


def case_tempo_recovery() -> List[Row]:
    """`tempo_ratio` means the same thing in the synth and in the aligner."""
    rows: List[Row] = []
    base = voice.sing(voice.phrase(SCALE, 480.0), voice.ALTO, seed=7)
    for truth_tempo in (0.90, 1.00, 1.12):
        for truth_off in (0.0, 150.0):
            other = voice.sing(
                voice.phrase(SCALE, 480.0), voice.TENOR,
                tempo_ratio=truth_tempo, lead_in_ms=truth_off, seed=7,
            ).audio
            off, tempo, _ = align.scan_offset_tempo(base.audio, other, sr=SR)
            ok = abs(off - truth_off) <= 45.0 and abs(tempo - truth_tempo) <= 0.06
            rows.append((
                f"tempo {truth_tempo:.2f} @ {truth_off:.0f}ms",
                f"{truth_off:.0f}ms/{truth_tempo:.3f}",
                f"{off:.0f}ms/{tempo:.3f}",
                ok,
            ))
    return rows


CASES = [
    # tempo, lead-in ms, partner voice
    (1.00, 240.0, voice.ALTO),
    (1.07, 150.0, voice.TENOR),
    (0.94, 0.0, voice.SOPRANO),
    (1.18, 60.0, voice.BARITONE),
    (0.86, 320.0, voice.BASS),
]


def case_warp_quality() -> List[Row]:
    rows: List[Row] = []
    ref = voice.sing(voice.phrase(SCALE, 480.0), voice.ALTO, seed=3)
    for tempo, lead, prof in CASES:
        tag = f"{prof.name} {tempo:.2f}x +{lead:.0f}ms"
        other = voice.sing(
            voice.phrase(SCALE, 480.0), prof,
            tempo_ratio=tempo, lead_in_ms=lead, seed=3,
        )
        warped, al = align.align_and_warp(ref.audio, other.audio, sr=SR)

        resid = windowed_residual_ms(ref.audio, warped)
        worst = max(abs(r) for r in resid)
        # 20 ms is inside the window where an ensemble still feels locked;
        # the raw takes here are out by 100-300 ms.
        rows.append((f"{tag}: worst window", "<=20ms", f"{worst:.0f}ms", worst <= 20.0))

        median = float(np.median(np.abs(resid)))
        rows.append((f"{tag}: median window", "<=5ms", f"{median:.0f}ms", median <= 5.0))

        e_raw = dsp.correlation(
            dsp.frame_rms(dsp.pad_to(other.audio, ref.audio.size), sr=SR),
            dsp.frame_rms(ref.audio, sr=SR),
        )
        e_warp = dsp.correlation(dsp.frame_rms(warped, sr=SR), dsp.frame_rms(ref.audio, sr=SR))
        # Not 0.95+: two different voice types singing the same line have
        # genuinely different loudness envelopes, because the envelope depends
        # on which harmonics land inside which formant. The warp can fix the
        # timing, which is the windowed-residual assertion above; it cannot
        # make a bass sound like an alto, and should not.
        rows.append((
            f"{tag}: envelope corr", ">=0.90 (raw " + f"{e_raw:.2f})",
            f"{e_warp:.3f}", e_warp >= 0.90 and e_warp > e_raw,
        ))

        o_raw = dsp.correlation(
            dsp.onset_envelope(dsp.pad_to(other.audio, ref.audio.size), sr=SR),
            dsp.onset_envelope(ref.audio, sr=SR),
        )
        o_warp = dsp.correlation(
            dsp.onset_envelope(warped, sr=SR), dsp.onset_envelope(ref.audio, sr=SR)
        )
        rows.append((
            f"{tag}: onset corr", f">=0.35 (raw {o_raw:.2f})",
            f"{o_warp:.3f}", o_warp >= 0.35 and o_warp > o_raw,
        ))

        pre = dsp.chromagram(other.audio, SR).mean(axis=0)
        post = dsp.chromagram(warped, SR).mean(axis=0)
        sim = float(np.dot(pre, post) / (np.linalg.norm(pre) * np.linalg.norm(post) + 1e-9))
        rows.append((f"{tag}: chroma kept", ">=0.90", f"{sim:.3f}", sim >= 0.90))

        # Tempo drift the warp had to absorb should match the physics: a take
        # sung at ratio r over T ms ends up T*(1 - 1/r) ms away from the ref.
        expect = abs(ref.duration_ms * (1.0 - 1.0 / tempo))
        got = max(abs(d["drift_ms"]) for d in al.drift_series(13, ref.duration_ms))
        ok = abs(got - expect) <= max(0.25 * expect, 60.0)
        rows.append((f"{tag}: drift vs physics", f"{expect:.0f}ms", f"{got:.0f}ms", ok))
    return rows


def case_warp_identity() -> List[Row]:
    """Warping a take onto itself must be close to a no-op."""
    ref = voice.sing(voice.phrase(SCALE, 480.0), voice.SOPRANO, seed=5)
    warped, al = align.align_and_warp(ref.audio, ref.audio, sr=SR)
    resid = windowed_residual_ms(ref.audio, warped)
    worst = max(abs(r) for r in resid)
    corr = dsp.correlation(dsp.frame_rms(warped, sr=SR), dsp.frame_rms(ref.audio, sr=SR))
    return [
        ("identity: worst window", "<=10ms", f"{worst:.0f}ms", worst <= 10.0),
        ("identity: envelope corr", ">=0.97", f"{corr:.3f}", corr >= 0.97),
        ("identity: offset", "0.0", f"{al.offset_ms:.1f}", abs(al.offset_ms) <= 10.0),
    ]


def case_phase_locking_helps() -> List[Row]:
    """The locked vocoder must beat the textbook one on the same stretch."""
    ref = voice.sing(voice.phrase(SCALE, 480.0), voice.ALTO, seed=3)
    env_ref = dsp.onset_envelope(ref.audio, sr=SR)
    rows: List[Row] = []
    for rate in (1.07, 0.90):
        locked = dsp.time_stretch(ref.audio, rate)
        plain = dsp._time_stretch_unlocked(ref.audio, rate)
        target = dsp.onset_envelope(
            dsp.time_warp(ref.audio, np.arange(0.0, 1e9)[:0]) if False else ref.audio, sr=SR
        )
        # Compare each against a correctly time-scaled copy of the reference
        # onset envelope, so the only difference is vocoder artefacts.
        n = max(int(env_ref.size / rate), 4)
        scaled = np.interp(np.linspace(0, env_ref.size - 1, n), np.arange(env_ref.size), env_ref)
        c_lock = dsp.correlation(dsp.onset_envelope(locked, sr=SR), scaled)
        c_plain = dsp.correlation(dsp.onset_envelope(plain, sr=SR), scaled)
        rows.append((
            f"phase lock @ {rate:.2f}x", f">unlocked ({c_plain:.2f})",
            f"{c_lock:.3f}", c_lock > c_plain,
        ))
        _ = target
    return rows


def main() -> int:
    rows: List[Row] = []
    for fn in (
        case_offset_recovery,
        case_tempo_recovery,
        case_warp_identity,
        case_phase_locking_helps,
        case_warp_quality,
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
