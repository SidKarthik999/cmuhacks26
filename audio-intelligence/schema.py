"""JSON Schema validation helpers for Person B contracts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import jsonschema

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "shared" / "schemas"


def _load(name: str) -> Dict[str, Any]:
    return json.loads((_SCHEMA_DIR / name).read_text())


_SINGING = _load("singing_event.schema.json")
_CONTOUR = _load("pitch_contour.schema.json")
_NOTE = _load("note_event.schema.json")


def validate_singing_event(data: Dict[str, Any]) -> None:
    jsonschema.validate(instance=data, schema=_SINGING)


def validate_pitch_contour(data: Dict[str, Any]) -> None:
    jsonschema.validate(instance=data, schema=_CONTOUR)


def validate_note_event(data: Dict[str, Any]) -> None:
    jsonschema.validate(instance=data, schema=_NOTE)


def validate_note_events(items: List[Dict[str, Any]]) -> None:
    for item in items:
        validate_note_event(item)
