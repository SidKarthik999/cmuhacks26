"""Task 2 — per-participant singing detection.

Baseline: YIN harmonicity + F0 stability. Singing holds a pitch; speech's
contour jumps every syllable; silence has no energy.

Emits periodic `sample` events (~120ms) plus `singing_started` /
`singing_stopped` edges so Task 3 does not have to poll.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from audio_io import pcm_to_float32, rms
from schema import validate_singing_event
from yin import yin_pitch

SAMPLE_HOP_MS = 80.0
FRAME_MS = 40.0
START_HOLD_MS = 160.0  # 2 hops — onset well inside the ~1s budget
STOP_HOLD_MS = 400.0


def _hz_to_midi(hz: float) -> float:
    return 69.0 + 12.0 * np.log2(hz / 440.0)


@dataclass
class FrameScore:
    timestamp_ms: float
    is_singing: bool
    confidence: float
    harmonicity: float
    pitch_hz: Optional[float]
    energy: float


class SingingDetector:
    """Stateful detector for one participant's raw PCM stream."""

    def __init__(
        self,
        participant_id: str,
        room_id: Optional[str] = None,
        sample_rate: int = 16000,
    ):
        self.participant_id = participant_id
        self.room_id = room_id
        self.sample_rate = sample_rate
        self._buf = np.zeros(0, dtype=np.float32)
        self._t0_ms = 0.0
        self._consumed = 0
        self._state = False
        self._pending_start = 0.0
        self._pending_stop = 0.0
        self._recent_f0: List[float] = []

    def reset(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)
        self._consumed = 0
        self._state = False
        self._pending_start = 0.0
        self._pending_stop = 0.0
        self._recent_f0 = []

    def push(
        self,
        pcm: np.ndarray,
        timestamp_ms: Optional[float] = None,
        sample_rate: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Ingest a PCM chunk; return any events whose hop just closed."""
        if sample_rate is not None:
            self.sample_rate = sample_rate
        x = pcm_to_float32(pcm)
        if timestamp_ms is not None and self._buf.size == 0:
            self._t0_ms = timestamp_ms
        self._buf = np.concatenate([self._buf, x])
        hop = int(self.sample_rate * SAMPLE_HOP_MS / 1000.0)
        frame = int(self.sample_rate * FRAME_MS / 1000.0)
        events: List[Dict[str, Any]] = []
        while self._buf.size - self._consumed >= hop:
            start = self._consumed
            end = start + hop
            chunk = self._buf[start:end]
            # Use the last FRAME_MS of the hop for YIN (more stable).
            yin_frame = chunk[-frame:] if chunk.size >= frame else chunk
            t_ms = self._t0_ms + 1000.0 * end / self.sample_rate
            score = self._score_frame(yin_frame, t_ms)
            events.extend(self._update_state(score))
            self._consumed = end
        # Drop consumed audio so the buffer cannot grow without bound.
        if self._consumed > 0:
            self._buf = self._buf[self._consumed :]
            self._t0_ms += 1000.0 * self._consumed / self.sample_rate
            self._consumed = 0
        return events

    def _score_frame(self, frame: np.ndarray, timestamp_ms: float) -> FrameScore:
        energy = rms(frame)
        pitch, harm = yin_pitch(frame, self.sample_rate)
        if pitch is not None:
            self._recent_f0.append(pitch)
            self._recent_f0 = self._recent_f0[-4:]
        voiced = energy > 0.018 and harm >= 0.5 and pitch is not None
        stability = 0.0
        if voiced and len(self._recent_f0) >= 2:
            midis = np.array([_hz_to_midi(f) for f in self._recent_f0], dtype=np.float32)
            std = float(np.std(midis))
            # Singing: std typically < ~0.7 semitones over a few hops; speech jumps.
            stability = float(np.clip(1.0 - std / 1.6, 0.0, 1.0))
        singing_raw = bool(voiced and (stability >= 0.42 or (harm >= 0.8 and energy > 0.04)))
        confidence = 0.0
        if singing_raw:
            confidence = float(
                0.35 * harm + 0.45 * max(stability, 0.5) + 0.20 * min(1.0, energy / 0.08)
            )
        elif voiced:
            confidence = float(0.55 * (1.0 - stability) + 0.2)
        else:
            confidence = float(min(0.9, 0.15 + (0.02 - min(energy, 0.02)) * 10))
        return FrameScore(
            timestamp_ms=timestamp_ms,
            is_singing=bool(singing_raw),
            confidence=float(np.clip(confidence, 0.0, 1.0)),
            harmonicity=float(harm),
            pitch_hz=pitch if voiced else None,
            energy=energy,
        )

    def _update_state(self, score: FrameScore) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        sample = self._event("sample", score, is_singing=self._state or score.is_singing)
        out.append(sample)
        if score.is_singing:
            self._pending_stop = 0.0
            if not self._state:
                self._pending_start += SAMPLE_HOP_MS
                if self._pending_start >= START_HOLD_MS:
                    self._state = True
                    self._pending_start = 0.0
                    started = self._event("singing_started", score, is_singing=True)
                    out.append(started)
        elif score.harmonicity >= 0.65 and score.pitch_hz is not None:
            # Note transitions: keep the current phrase open.
            self._pending_stop = 0.0
        else:
            self._pending_start = 0.0
            if self._state:
                self._pending_stop += SAMPLE_HOP_MS
                if self._pending_stop >= STOP_HOLD_MS:
                    self._state = False
                    self._pending_stop = 0.0
                    stopped = self._event("singing_stopped", score, is_singing=False)
                    out.append(stopped)
        return out

    def _event(self, kind: str, score: FrameScore, is_singing: bool) -> Dict[str, Any]:
        ev = {
            "participant_id": self.participant_id,
            "room_id": self.room_id,
            "timestamp_ms": score.timestamp_ms,
            "is_singing": is_singing,
            "confidence": score.confidence,
            "kind": kind,
            "harmonicity": score.harmonicity,
            "pitch_hz": score.pitch_hz,
        }
        validate_singing_event(ev)
        return ev


def detect_buffer(
    pcm: np.ndarray,
    sample_rate: int,
    participant_id: str = "participant",
    room_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Run the detector over a complete buffer (tests / batch)."""
    det = SingingDetector(participant_id, room_id=room_id, sample_rate=sample_rate)
    hop = int(sample_rate * SAMPLE_HOP_MS / 1000.0)
    events: List[Dict[str, Any]] = []
    t = 0.0
    x = pcm_to_float32(pcm)
    for i in range(0, x.size, hop):
        chunk = x[i : i + hop]
        if chunk.size < hop // 2:
            break
        events.extend(det.push(chunk, timestamp_ms=t, sample_rate=sample_rate))
        t += 1000.0 * chunk.size / sample_rate
    return events
