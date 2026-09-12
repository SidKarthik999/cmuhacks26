"""Feature 1: a teacher demonstrates, a student imitates, the gap is measured.

The chain, in order, because each step exists to make the next one possible:

1. **Clean both takes.** Not cosmetic. A 60 Hz mains hum under a tenor line
   drags the pitch tracker to 85 Hz where the truth is 147 -- the reading is
   not noisy, it is *wrong*, and every number downstream inherits that. So
   cleaning runs first and its effect is reported, not assumed.
2. **Track pitch and segment notes** on both takes, giving the wave-plus-
   notation view that goes on both screens.
3. **Align the student to the teacher.** Two people singing the same line
   90 seconds apart do not share a clock. Comparing frame *k* to frame *k*
   measures their start times, not their singing. The aligner recovers the
   entry lag and the tempo difference and builds a time map between them.
4. **Score 70/30 on pitch and volume**, through that map.

Two decisions worth stating plainly, because they are what makes the number
mean anything:

**Timing is measured, then forgiven.** The entry lag and tempo difference are
reported in full -- a student who rushes should see it -- but they are not
part of the 70/30 score. Someone who starts a beat late and then sings
beautifully has a timing note to work on, not a tuning problem, and rolling
the two together produces a score that cannot be acted on.

**Level is normalised, dynamics are not.** Absolute loudness is a property of
the microphone and the gain knob, so the volume half of the score ignores it
and measures *shape*: whether the student swelled and tapered where the
teacher did. Penalising a student for sitting further from their laptop would
be measuring the room.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from . import align, bridge, dsp, metrics, pitch, scoring

SAMPLE_RATE = 22050
DISPLAY_POINTS = 900
CONTOUR_POINTS = 320


@dataclass
class TakeView:
    """Everything the interface needs to draw one take, plus its provenance."""

    name: str
    role: str
    audio: np.ndarray
    contour: pitch.Contour
    notes: List[pitch.NoteSpan]
    cleaning: Dict[str, Any] = field(default_factory=dict)
    sample_rate: int = SAMPLE_RATE

    @property
    def duration_ms(self) -> float:
        return 1000.0 * len(self.audio) / self.sample_rate

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "duration_ms": round(self.duration_ms, 1),
            "lufs": round(dsp.loudness_lufs(self.audio, self.sample_rate), 2),
            "peak": round(float(np.max(np.abs(self.audio))) if self.audio.size else 0.0, 4),
            "wave": dsp.downsample_envelope(self.audio, DISPLAY_POINTS),
            "contour": _contour_series(self.contour, CONTOUR_POINTS),
            "voiced_percent": round(100.0 * self.contour.voiced_fraction, 1),
            "notes": [s.as_dict() for s in self.notes],
            "cleaning": self.cleaning,
        }


@dataclass
class LessonResult:
    teacher: TakeView
    student: TakeView
    score: scoring.Score
    alignment: align.Alignment
    student_aligned: np.ndarray
    timing: Dict[str, Any] = field(default_factory=dict)
    sample_rate: int = SAMPLE_RATE

    def as_dict(self, glossary: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "mode": "lesson",
            "teacher": self.teacher.as_dict(),
            "student": self.student.as_dict(),
            "student_aligned": {
                "wave": dsp.downsample_envelope(self.student_aligned, DISPLAY_POINTS),
                "duration_ms": round(1000.0 * len(self.student_aligned) / self.sample_rate, 1),
                "note": (
                    "The student's take resampled onto the teacher's timeline. "
                    "This is what the score was computed against, so the two "
                    "waveforms above it should line up note for note."
                ),
            },
            "score": self.score.as_dict(),
            "timing": self.timing,
        }
        if glossary:
            out["glossary"] = metrics.glossary()
        return out

    def report(self) -> str:
        """A plain-text readout, for the checks and for a terminal demo."""
        lines = [
            f"Lesson  overall {self.score.overall:5.1f}/100   "
            f"pitch {self.score.pitch_score:5.1f} x{metrics.PITCH_WEIGHT:.2f}   "
            f"volume {self.score.volume_score:5.1f} x{metrics.VOLUME_WEIGHT:.2f}",
            f"  {self.score.headline()}",
        ]
        for row in metrics.describe(self.score.values):
            if row["value"] is None:
                continue
            lines.append(
                f"  {row['label']:<26} {row['display']:>14}   {row['reading']}"
            )
        lines.append(
            f"  timing: entry {self.timing['entry_lag_ms']:+.0f} ms, "
            f"tempo {self.timing['tempo_ratio']:.3f}x, "
            f"worst window {self.timing['worst_window_ms']:.0f} ms "
            f"({self.timing['verdict']})"
        )
        for caveat in self.score.caveats:
            lines.append(f"  ! {caveat}")
        return "\n".join(lines)


def _contour_series(contour: pitch.Contour, points: int) -> List[Dict[str, float]]:
    """Thin a pitch contour for drawing, keeping unvoiced gaps as gaps.

    Unvoiced frames become `None` rather than zero or an interpolated line.
    A breath is not a note at 0 Hz, and a chart that joins across one is
    telling the student they sang something they did not.
    """
    n = contour.time_ms.size
    if n == 0:
        return []
    idx = np.unique(np.linspace(0, n - 1, min(points, n)).astype(int))
    voiced = contour.voiced
    out: List[Dict[str, Any]] = []
    for i in idx:
        live = bool(voiced[i])
        out.append(
            {
                "t": round(float(contour.time_ms[i]), 1),
                "hz": round(float(contour.hz[i]), 2) if live else None,
                "midi": round(float(69.0 + 12.0 * np.log2(contour.hz[i] / 440.0)), 3)
                if live
                else None,
                "db": round(float(contour.rms_db[i]), 1),
            }
        )
    return out


def prepare(
    audio: np.ndarray,
    name: str,
    role: str,
    sr: int = SAMPLE_RATE,
    clean: bool = True,
) -> TakeView:
    """Clean, track, and segment one take.

    Shared by all three features: whatever a singer sends, this is what the
    rest of the engine wants it to look like.
    """
    audio = np.asarray(audio, dtype=np.float32)
    report: Dict[str, Any] = {"applied": False, "reason": "cleaning disabled"}
    if clean:
        audio, report = bridge.clean(audio, sr)
    contour = pitch.track(audio, sr=sr)
    onsets = dsp.pick_attacks(audio, sr=sr)
    return TakeView(
        name=name,
        role=role,
        audio=audio,
        contour=contour,
        notes=pitch.segment_notes(contour, onsets_ms=onsets),
        cleaning=report,
        sample_rate=sr,
    )


def run(
    teacher_audio: np.ndarray,
    student_audio: np.ndarray,
    sr: int = SAMPLE_RATE,
    clean: bool = True,
    teacher_name: str = "Teacher",
    student_name: str = "Student",
) -> LessonResult:
    """Compare a student's imitation against the teacher's demonstration."""
    teacher = prepare(teacher_audio, teacher_name, "teacher", sr, clean)
    student = prepare(student_audio, student_name, "student", sr, clean)

    aligned, alignment = align.align_and_warp(teacher.audio, student.audio, sr=sr)
    aligned = dsp.pad_to(aligned, teacher.audio.size)
    score = scoring.compare(
        teacher.audio,
        student.audio,
        sr=sr,
        alignment=alignment,
        student_aligned=aligned,
    )

    residual_ms, _residual_conf = align.residual_offset_ms(
        teacher.audio, aligned, sr=sr
    )
    windows = align.window_residuals(teacher.audio, aligned, sr=sr)
    live = [abs(r) for _t, r, c in windows if c >= align.WINDOW_MIN_CONF]
    worst = max(live) if live else abs(residual_ms)
    timing = {
        "entry_lag_ms": round(alignment.offset_ms, 1),
        "tempo_ratio": round(alignment.tempo_ratio, 4),
        "tempo_percent": round(100.0 * (alignment.tempo_ratio - 1.0), 1),
        "confidence": round(alignment.confidence, 3),
        "method": alignment.method,
        "residual_offset_ms": round(residual_ms, 1),
        "worst_window_ms": round(worst, 1),
        "verdict": metrics.timing_in_words(worst),
        "windows": [
            {
                "t": round(float(t), 1),
                "residual_ms": round(float(r), 1),
                "confidence": round(float(c), 3),
            }
            for t, r, c in windows
        ],
        "drift": alignment.drift_series(points=48, span_ms=teacher.duration_ms),
        "warp": alignment.warp_series(points=48, span_ms=teacher.duration_ms),
        "note": (
            "Entry lag and tempo are reported, not scored. Starting late is a "
            "different lesson from singing out of tune, and averaging the two "
            "gives a number nobody can act on."
        ),
    }

    return LessonResult(
        teacher=teacher,
        student=student,
        score=score,
        alignment=alignment,
        student_aligned=aligned,
        timing=timing,
        sample_rate=sr,
    )


def run_scene(scene: Any, clean: bool = True) -> LessonResult:
    """Run a fixture scene, for the checks and the demo's canned lessons."""
    teacher = scene.part("Teacher")
    student = scene.part("Student")
    return run(
        teacher.audio,
        student.audio,
        sr=scene.sample_rate,
        clean=clean,
        teacher_name=f"{teacher.name} ({teacher.profile})",
        student_name=f"{student.name} ({student.profile})",
    )
