"""Assumed Task 1 audio-track chunk until Person A lands audio_track.schema.json.

Shape is the rolling-buffer input the audio backend needs:
PCM float32 mono frames keyed by participant_id + room_id.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np


def mock_audio_track_chunk(
    participant_id: str = "participant_peer_a",
    room_id: str = "room_practice",
    sample_rate: int = 16000,
    duration_ms: float = 200.0,
    timestamp_ms: float = 0.0,
) -> Dict[str, Any]:
    n = int(sample_rate * duration_ms / 1000.0)
    pcm = (0.1 * np.sin(2 * np.pi * 220.0 * np.arange(n) / sample_rate)).astype(
        np.float32
    )
    return {
        "participant_id": participant_id,
        "room_id": room_id,
        "sample_rate": sample_rate,
        "timestamp_ms": timestamp_ms,
        "pcm": pcm,
    }
