import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "audio-intelligence"))
sys.path.insert(0, str(ROOT / "shared" / "mocks"))

from fixtures import singing_scale
from mock_pitch_contour import mock_pitch_contour
from notes import extract_pitch_contour, notes_from_contour, signal_to_notes
from schema import validate_pitch_contour


def test_mock_contour_schema():
    validate_pitch_contour(mock_pitch_contour())


def test_extract_pitch_contour_signature_on_sine():
    sr = 16000
    t = np.arange(int(sr * 0.5)) / sr
    pcm = (0.3 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    points = extract_pitch_contour(pcm, sr)
    assert points, "expected contour points"
    assert set(points[0].keys()) == {"time_ms", "pitch_hz", "confidence"}
    voiced = [p for p in points if p["pitch_hz"]]
    assert voiced
    med = float(np.median([p["pitch_hz"] for p in voiced]))
    cents = 1200 * np.log2(med / 440.0)
    assert abs(cents) < 30, med


def test_sung_scale_to_note_names():
    sr = 16000
    hz = (261.63, 293.66, 329.63, 349.23)
    pcm = singing_scale(sample_rate=sr, note_hz=hz, note_ms=400)
    notes = signal_to_notes(pcm, sr)
    names = [n["note_name"] for n in notes]
    # Allow octave spelling C4/C5 depending on rounding; check pitch classes.
    pcs = [n["pitch_midi"] % 12 for n in notes]
    expected = [0, 2, 4, 5]  # C D E F
    # Contour may split a note; unique consecutive pitch classes should include the scale.
    collapsed = []
    for pc in pcs:
        if not collapsed or collapsed[-1] != pc:
            collapsed.append(pc)
    for pc in expected:
        assert pc in collapsed, names
