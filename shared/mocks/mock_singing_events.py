"""Fixtures for singing_event.schema.json (Task 2, Person B).

Person C should subscribe to `kind=singing_started|singing_stopped` rather
than polling `sample` events. Swap these mocks for
`audio-intelligence.detection.SingingDetector` without changing consumers.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

_MOCK_JSON_PATH = Path(__file__).with_name("mock_singing_events.json")


def mock_singing_events() -> List[Dict[str, Any]]:
    return json.loads(_MOCK_JSON_PATH.read_text())


def mock_singing_started(
    participant_id: str = "participant_teacher",
    timestamp_ms: float = 1000.0,
) -> Dict[str, Any]:
    return {
        "participant_id": participant_id,
        "room_id": "room_demo",
        "timestamp_ms": timestamp_ms,
        "is_singing": True,
        "confidence": 0.91,
        "kind": "singing_started",
        "harmonicity": 0.88,
        "pitch_hz": 220.0,
    }
