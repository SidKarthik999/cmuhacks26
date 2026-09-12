"""Adapters onto the team's existing audio modules.

Swarlink is a layer on top of the repository, not a fork of it. Noise
cleaning and the shared pitch-contour document format already exist in
`audio-intelligence/` and other people's code depends on their signatures, so
this module imports them rather than reimplementing them, and converts at the
boundary.

Two conversions are needed and both are load-bearing:

* the team's modules take PCM plus a sample rate and hand back plain dicts;
  Swarlink's engine works in float32 at a fixed rate with dataclasses;
* the cleaner is written against 16 kHz streaming input, while the engine
  analyses at 22.05 kHz, so anything that round-trips through it is resampled
  both ways.

Everything here degrades to a documented no-op if the modules are missing, so
Swarlink still runs in a checkout where `audio-intelligence/` has been moved
or renamed. `available()` reports which parts are live, and the interface
shows it -- a demo that silently stopped cleaning would be worse than one
that says it is not.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import dsp, pitch

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TEAM_PATHS = [
    os.path.join(_ROOT, "audio-intelligence"),
    os.path.join(_ROOT, "signal-processing"),
]
for _p in _TEAM_PATHS:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)

_CLEAN_ERR: Optional[str] = None
_NOTES_ERR: Optional[str] = None

try:  # noqa: SIM105
    from cleaning import clean_pcm as _team_clean_pcm
except Exception as exc:  # pragma: no cover - depends on checkout layout
    _team_clean_pcm = None
    _CLEAN_ERR = f"{type(exc).__name__}: {exc}"

try:
    from notes import extract_pitch_contour as _team_contour
    from notes import notes_from_contour as _team_notes
except Exception as exc:  # pragma: no cover
    _team_contour = None
    _team_notes = None
    _NOTES_ERR = f"{type(exc).__name__}: {exc}"

CLEAN_SR = 16000


def available() -> Dict[str, Any]:
    """What the shared modules can do in this checkout, and why not if not."""
    return {
        "cleaning": _team_clean_pcm is not None,
        "cleaning_error": _CLEAN_ERR,
        "notes": _team_notes is not None,
        "notes_error": _NOTES_ERR,
        "search_paths": _TEAM_PATHS,
    }


def clean(x: np.ndarray, sr: int = 22050) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Noise-clean a take with the team's spectral subtraction.

    Returns the cleaned audio and a report of what changed, because "we
    cleaned it" is not a claim the interface should make without evidence.
    The noise reduction figure is the drop in level of the quietest fifth of
    the take -- where the noise floor lives -- while the signal change is
    measured over the loudest fifth, and a large value there would mean the
    cleaner had eaten into the singing.
    """
    x = np.asarray(x, dtype=np.float32)
    if _team_clean_pcm is None or x.size == 0:
        return x, {"applied": False, "reason": _CLEAN_ERR or "empty input"}

    down = dsp.resample(x, sr, CLEAN_SR)
    cleaned_16k = np.asarray(_team_clean_pcm(down, CLEAN_SR), dtype=np.float32)
    cleaned = dsp.pad_to(dsp.resample(cleaned_16k, CLEAN_SR, sr), x.size)

    before = dsp.frame_rms(x, sr=sr)
    after = dsp.frame_rms(cleaned, sr=sr)
    n = min(before.size, after.size)
    before, after = before[:n], after[:n]
    quiet = before <= np.percentile(before, 20)
    loud = before >= np.percentile(before, 80)

    def drop(mask: np.ndarray) -> float:
        if not np.any(mask):
            return 0.0
        a = float(np.mean(before[mask])) + 1e-12
        b = float(np.mean(after[mask])) + 1e-12
        return float(20.0 * np.log10(b / a))

    return cleaned, {
        "applied": True,
        "module": "audio-intelligence/cleaning (spectral subtraction)",
        "analysis_sr": CLEAN_SR,
        "noise_floor_change_db": round(drop(quiet), 2),
        "signal_change_db": round(drop(loud), 2),
        "peak_before": round(float(np.max(np.abs(x))), 4),
        "peak_after": round(float(np.max(np.abs(cleaned))), 4),
    }


def team_notes(x: np.ndarray, sr: int = 22050) -> List[Dict[str, Any]]:
    """Discrete notes via the team's contour + quantiser, for cross-checking."""
    if _team_notes is None or _team_contour is None or x.size == 0:
        return []
    points = _team_contour(np.asarray(x, dtype=np.float32), sr)
    return list(_team_notes(points))


def contour_document(contour: pitch.Contour) -> Dict[str, Any]:
    """Swarlink's contour in the shared document shape.

    Person C's comparison code consumes this format, so emitting it keeps the
    two halves of the project interchangeable at the data level even where
    they use different implementations underneath.
    """
    return {
        "sample_rate": int(contour.sample_rate),
        "hop_ms": float(contour.hop_ms),
        "points": contour.as_points(),
    }


def notes_document(spans: List[pitch.NoteSpan]) -> List[Dict[str, Any]]:
    """Swarlink note spans in the team's NoteEvent shape."""
    return [
        {
            "pitch_midi": int(s.midi),
            "note_name": s.note,
            "start_ms": round(float(s.start_ms), 2),
            "duration_ms": round(float(s.duration_ms), 2),
            "confidence": round(float(s.confidence), 4),
            "velocity": round(float(min(1.0, s.confidence)), 4),
        }
        for s in spans
    ]
