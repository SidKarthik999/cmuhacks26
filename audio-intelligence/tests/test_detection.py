import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "audio-intelligence"))
sys.path.insert(0, str(ROOT / "shared" / "mocks"))

from detection import detect_buffer
from fixtures import conversational_speech, singing_scale
from mock_singing_events import mock_singing_events
from schema import validate_singing_event


def _fraction_singing_samples(events):
    samples = [e for e in events if e["kind"] == "sample"]
    if not samples:
        return 0.0
    return sum(1 for e in samples if e["is_singing"]) / len(samples)


def test_mock_events_match_schema():
    for ev in mock_singing_events():
        validate_singing_event(ev)


def test_detects_sustained_singing_within_one_second():
    sr = 16000
    pcm = singing_scale(sample_rate=sr, note_ms=400)
    events = detect_buffer(pcm, sr, participant_id="p_singer")
    starts = [e for e in events if e["kind"] == "singing_started"]
    assert starts, f"expected singing_started, got kinds {[e['kind'] for e in events[:12]]}"
    assert starts[0]["timestamp_ms"] <= 1000.0
    assert starts[0]["participant_id"] == "p_singer"
    assert _fraction_singing_samples(events) > 0.4


def test_low_false_positive_on_speech():
    sr = 16000
    pcm = conversational_speech(sample_rate=sr, duration_s=2.5)
    events = detect_buffer(pcm, sr, participant_id="p_talker")
    starts = [e for e in events if e["kind"] == "singing_started"]
    assert len(starts) == 0
    assert _fraction_singing_samples(events) < 0.25


def test_silence_is_not_singing():
    sr = 16000
    pcm = np.zeros(sr, dtype=np.float32)
    events = detect_buffer(pcm, sr)
    assert not any(e["kind"] == "singing_started" for e in events)
    assert _fraction_singing_samples(events) == 0.0
