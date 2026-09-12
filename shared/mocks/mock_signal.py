"""Stub fixtures for the Signal contract (shared/schemas/signal.schema.json).

Consumers (Person A, Person B) should build against these instead of waiting
on the real signal-processing/storage implementation. Swapping the mock for
the real SignalStore later requires no changes to consumer code, only a
wiring change.
"""
from __future__ import annotations

import io
import json
import math
import struct
import wave
from pathlib import Path
from typing import Any, Dict

_MOCK_JSON_PATH = Path(__file__).with_name("mock_signal.json")


def mock_signal_dict() -> Dict[str, Any]:
    """Return a fresh dict matching signal.schema.json."""
    return json.loads(_MOCK_JSON_PATH.read_text())


def mock_audio_bytes(
    duration_ms: float = 1500.0,
    sample_rate: int = 44100,
    frequency_hz: float = 440.0,
) -> bytes:
    """Generate a synthetic mono 16-bit PCM WAV sine tone as raw file bytes.

    Useful as stand-in audio for a mock Signal's audio_ref without needing a
    real recording.
    """
    n_frames = int(sample_rate * duration_ms / 1000.0)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_frames):
            sample = int(32767 * 0.5 * math.sin(2 * math.pi * frequency_hz * i / sample_rate))
            frames += struct.pack("<h", sample)
        wf.writeframes(bytes(frames))
    return buf.getvalue()


if __name__ == "__main__":
    print(json.dumps(mock_signal_dict(), indent=2))
