"""Signal dataclass + validation against shared/schemas/signal.schema.json.

This is the Python binding for the Signal contract. Person A and Person B
should import Signal from here rather than hand-rolling a matching struct.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import jsonschema

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "shared" / "schemas" / "signal.schema.json"
)
_SCHEMA = json.loads(_SCHEMA_PATH.read_text())


class SignalValidationError(ValueError):
    """Raised when a Signal payload doesn't match signal.schema.json."""


@dataclass
class Signal:
    id: str
    participant_id: str
    room_id: str
    start_time: float
    end_time: float
    sample_rate: int
    audio_ref: str
    audio_format: str
    channels: int = 1
    sample_width_bytes: int = 2
    role: Optional[str] = None
    mode: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Signal":
        validate_signal_dict(data)
        return cls(**data)

    def validate(self) -> None:
        validate_signal_dict(self.to_dict())


def validate_signal_dict(data: Dict[str, Any]) -> None:
    """Raise SignalValidationError if data doesn't match signal.schema.json."""
    try:
        jsonschema.validate(instance=data, schema=_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise SignalValidationError(str(exc)) from exc

    if data["end_time"] < data["start_time"]:
        raise SignalValidationError("end_time must be >= start_time")
