"""Stub for Person A Task 9 (multi-feed audio routing).

Practice mode delivers an *additional* enhanced feed alongside the raw call.
This in-process router stands in until platform/api exposes real routing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np


@dataclass
class EnhancedFrame:
    timestamp_ms: float
    sample_rate: int
    pcm: np.ndarray
    singers: List[str]


@dataclass
class EnhancedFeedRouter:
    """In-memory Task 9 stub: one enhanced bus per room."""

    frames: Dict[str, List[EnhancedFrame]] = field(default_factory=dict)

    def push_enhanced(
        self,
        room_id: str,
        pcm: np.ndarray,
        sample_rate: int,
        timestamp_ms: float,
        singers: Optional[List[str]] = None,
    ) -> EnhancedFrame:
        frame = EnhancedFrame(
            timestamp_ms=timestamp_ms,
            sample_rate=sample_rate,
            pcm=np.asarray(pcm, dtype=np.float32),
            singers=list(singers or []),
        )
        self.frames.setdefault(room_id, []).append(frame)
        return frame

    def latest(self, room_id: str) -> Optional[EnhancedFrame]:
        items = self.frames.get(room_id) or []
        return items[-1] if items else None
