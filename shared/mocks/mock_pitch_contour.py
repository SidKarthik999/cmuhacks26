"""Fixture for pitch_contour.schema.json (Task 8, Person B).

Person C Task 5 should call `extract_pitch_contour(audio, sample_rate)` and
treat this mock as the return-value shape until the real tracker is wired.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

_MOCK_JSON_PATH = Path(__file__).with_name("mock_pitch_contour.json")


def mock_pitch_contour() -> Dict[str, Any]:
    return json.loads(_MOCK_JSON_PATH.read_text())


def mock_pitch_points() -> List[Dict[str, Any]]:
    return list(mock_pitch_contour()["points"])
