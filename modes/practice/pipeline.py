"""Practice mode orchestration (Person B).

Dual-stream: detect → Task 3 extract on edges → Task 7 stream-clean →
Person C Task 4 `StreamingAligner` → mix → Task 9 enhanced feed (stub until
Person A lands routing).

Solo singer: still clean, skip sync until the second person sings.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "audio-intelligence"))
sys.path.insert(0, str(_ROOT / "shared" / "mocks"))
sys.path.insert(0, str(_ROOT / "signal-processing"))

from audio_io import read_wav, write_wav_bytes  # noqa: E402
from cleaning import StreamingCleaner, clean_signal_in_store  # noqa: E402
from detection import SingingDetector  # noqa: E402
from mock_alignment import apply_offset, sync_streaming  # noqa: E402
from mock_routing import EnhancedFeedRouter  # noqa: E402

try:
    from storage import SignalStore  # noqa: E402
except ImportError:  # pragma: no cover
    SignalStore = None  # type: ignore

try:
    from sync import DEFAULT_HOP_LENGTH, StreamingAligner, render_aligned_playback  # noqa: E402
except ImportError:  # pragma: no cover
    DEFAULT_HOP_LENGTH = 2048
    StreamingAligner = None  # type: ignore
    render_aligned_playback = None  # type: ignore


@dataclass
class ParticipantStream:
    participant_id: str
    detector: SingingDetector
    cleaner: StreamingCleaner
    raw: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    cleaned: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    # Cleaned audio not yet fed to the Task 4 StreamingAligner. Separate from
    # `cleaned` (the rolling ~4s mixing buffer) because the aligner must only
    # ever see genuinely new audio -- re-feeding it `cleaned`'s full rolling
    # window every push() call would re-add heavily overlapping chroma
    # frames to its internal history each time instead of incremental new
    # frames (see docs/integration-contracts.md).
    pending_for_aligner: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    # Mirrors the aligner's own trailing chroma window in raw-sample terms,
    # so warp_path's frame indices (converted to local via
    # StreamingAligner.local_warp_path) stay meaningful against the audio
    # passed to render_aligned_playback.
    aligner_window_audio: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    singing: bool = False
    last_confidence: float = 0.0
    capture_start_ms: Optional[float] = None
    pending_pcm: List[np.ndarray] = field(default_factory=list)


class PracticeSession:
    def __init__(
        self,
        room_id: str,
        participant_ids: List[str],
        sample_rate: int = 16000,
        router: Optional[EnhancedFeedRouter] = None,
        store: Optional[Any] = None,
    ):
        if len(participant_ids) != 2:
            raise ValueError("Practice mode is exactly two peers")
        self.room_id = room_id
        self.sample_rate = sample_rate
        self.router = router or EnhancedFeedRouter()
        self.store = store
        self._pids = list(participant_ids)
        self._aligner = (
            StreamingAligner(signal_ids=(self._pids[0], self._pids[1]))
            if StreamingAligner is not None
            else None
        )
        self._last_align: Optional[Dict[str, Any]] = None
        self.streams = {
            pid: ParticipantStream(
                participant_id=pid,
                detector=SingingDetector(pid, room_id=room_id, sample_rate=sample_rate),
                cleaner=StreamingCleaner(sample_rate=sample_rate),
            )
            for pid in participant_ids
        }

    def push(
        self,
        participant_id: str,
        pcm: np.ndarray,
        timestamp_ms: float,
    ) -> Dict[str, Any]:
        st = self.streams[participant_id]
        pcm = np.asarray(pcm, dtype=np.float32).reshape(-1)
        events = st.detector.push(pcm, timestamp_ms=timestamp_ms, sample_rate=self.sample_rate)
        cleaned = st.cleaner.push(pcm)
        st.raw = np.concatenate([st.raw, pcm])[-self.sample_rate * 4 :]
        if cleaned.size:
            st.cleaned = np.concatenate([st.cleaned, cleaned])[-self.sample_rate * 4 :]
            st.pending_for_aligner = np.concatenate([st.pending_for_aligner, cleaned])

        for ev in events:
            st.last_confidence = ev["confidence"]
            if ev["kind"] == "singing_started":
                st.singing = True
                st.capture_start_ms = ev["timestamp_ms"]
                st.pending_pcm = [pcm.copy()]
            elif ev["kind"] == "singing_stopped":
                st.singing = False
                self._close_signal(st, ev)
                st.pending_pcm = []
                st.capture_start_ms = None
            elif st.singing:
                st.pending_pcm.append(pcm.copy())

        mix, used_sync = self._mix()
        singers = [s.participant_id for s in self.streams.values() if s.singing]
        frame = None
        if mix.size:
            frame = self.router.push_enhanced(
                self.room_id,
                mix,
                self.sample_rate,
                timestamp_ms,
                singers=singers,
            )
        return {
            "events": events,
            "singers": singers,
            "used_sync": used_sync,
            "enhanced_samples": int(mix.size),
            "feed": frame,
        }

    def _mix(self) -> tuple[np.ndarray, bool]:
        active = [s for s in self.streams.values() if s.singing and s.cleaned.size]
        if not active:
            solos = [s for s in self.streams.values() if s.cleaned.size]
            if len(solos) == 1:
                return solos[0].cleaned[-int(0.2 * self.sample_rate) :], False
            return np.zeros(0, dtype=np.float32), False
        if len(active) == 1:
            return active[0].cleaned[-int(0.25 * self.sample_rate) :], False

        a, b = active[0], active[1]
        mixed = self._mix_with_task4(a, b)
        if mixed is not None:
            return mixed, True

        align = sync_streaming(a.cleaned, b.cleaned, self.sample_rate, self._last_align)
        self._last_align = align
        offset = float(align.get("offset_ms") or 0.0)
        a_pcm = apply_offset(a.cleaned, max(0.0, -offset), self.sample_rate)
        b_pcm = apply_offset(b.cleaned, max(0.0, offset), self.sample_rate)
        n = min(a_pcm.size, b_pcm.size, int(0.25 * self.sample_rate))
        mix = 0.5 * a_pcm[-n:] + 0.5 * b_pcm[-n:]
        return mix.astype(np.float32), True

    def _mix_with_task4(
        self, a: ParticipantStream, b: ParticipantStream
    ) -> Optional[np.ndarray]:
        if self._aligner is None or render_aligned_playback is None:
            return None
        # Only feed the aligner genuinely new audio since its last push --
        # never a.cleaned/b.cleaned's full rolling window, which would
        # re-add already-seen chroma frames to the aligner's internal
        # history every call (see ParticipantStream.pending_for_aligner).
        min_len = DEFAULT_HOP_LENGTH
        if a.pending_for_aligner.size < min_len or b.pending_for_aligner.size < min_len:
            return None
        new_a, new_b = a.pending_for_aligner, b.pending_for_aligner
        wav_a = write_wav_bytes(new_a, self.sample_rate)
        wav_b = write_wav_bytes(new_b, self.sample_rate)
        a.pending_for_aligner = np.zeros(0, dtype=np.float32)
        b.pending_for_aligner = np.zeros(0, dtype=np.float32)

        result = self._aligner.push(wav_a, wav_b)
        if result is None:
            return None

        # Mirror the aligner's own trailing-window trim in raw-sample terms
        # so the audio we render against lines up with warp_path's frame
        # numbering (converted to local via local_warp_path below).
        window_samples = self._aligner.window_frames * self._aligner.hop_length
        a.aligner_window_audio = np.concatenate([a.aligner_window_audio, new_a])[-window_samples:]
        b.aligner_window_audio = np.concatenate([b.aligner_window_audio, new_b])[-window_samples:]

        local_warp_path = self._aligner.local_warp_path(result)
        playback = render_aligned_playback(
            write_wav_bytes(a.aligner_window_audio, self.sample_rate),
            write_wav_bytes(b.aligner_window_audio, self.sample_rate),
            local_warp_path,
            result.hop_length_ms,
        )
        pcm, _sr = read_wav(playback.mixed_wav)
        n = min(pcm.size, int(0.25 * self.sample_rate))
        return pcm[-n:] if n else pcm

    def _close_signal(self, st: ParticipantStream, stop_event: Dict[str, Any]) -> None:
        if self.store is None or SignalStore is None or not st.pending_pcm:
            return
        pcm = np.concatenate(st.pending_pcm)
        wav = write_wav_bytes(pcm, self.sample_rate)
        start = float(st.capture_start_ms or stop_event["timestamp_ms"])
        signal = self.store.save(
            participant_id=st.participant_id,
            room_id=self.room_id,
            start_time=start,
            end_time=float(stop_event["timestamp_ms"]),
            sample_rate=self.sample_rate,
            audio_bytes=wav,
            role="peer",
            mode="practice",
            metadata={"detected_confidence": st.last_confidence},
        )
        clean_signal_in_store(self.store, signal.id)
