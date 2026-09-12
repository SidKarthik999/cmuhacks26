"""Real out-of-process audio backend (Task 9.5 framework glue).

Connects to platform/api/server.ts's `/ws/audio?room_id=...&role=processor`
WebSocket, receives every `audio_chunk` for that room, and computes a REAL
mix using Person B's detection/cleaning and Person C's sync -- replacing the
in-process TypeScript stubs (modes/performance/stubs/*, and Practice mode's
mock_routing/mock_alignment fallbacks) with real signal processing. This is
exactly the "non-stub path later" the server's comments anticipate.

Practice rooms reuse modes/practice/pipeline.PracticeSession as-is (it
already wires detect -> clean -> Task 4 StreamingAligner -> mix). Performance
rooms use a pairwise-composed group sync: pick a reference performer and
batch-realign every other active performer to them over a bounded trailing
window each round, per ROADMAP.md's suggested "avoid a separate N-way
algorithm unless pairwise composition proves inaccurate" approach for
Task 6. This intentionally uses the stateless `align_audio`/
`render_aligned_playback` batch functions on a bounded window rather than
`StreamingAligner`'s persistent state, since re-feeding an ever-growing
rolling buffer into `StreamingAligner.push` every call (as Practice mode
currently does) double-counts overlapping audio across calls -- see the
note in docs/integration-contracts.md.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import websockets

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "audio-intelligence"))
sys.path.insert(0, str(_ROOT / "signal-processing"))
sys.path.insert(0, str(_ROOT / "shared" / "mocks"))
sys.path.insert(0, str(_ROOT / "modes"))

from audio_io import read_wav, write_wav_bytes  # noqa: E402
from cleaning import StreamingCleaner  # noqa: E402
from sync import align_audio, render_aligned_playback  # noqa: E402
from practice.pipeline import PracticeSession  # noqa: E402

WINDOW_SECONDS = 3.0
MIX_CHUNK_SECONDS = 0.25


def decode_audio_chunk(msg: Dict[str, Any]) -> np.ndarray:
    raw = base64.b64decode(str(msg.get("pcm_base64", "")))
    return np.frombuffer(raw, dtype="<f4").copy()


def encode_mix_chunk(
    feed: str,
    room_id: str,
    pcm: np.ndarray,
    sample_rate: int,
    timestamp_ms: float,
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    return json.dumps(
        {
            "type": "mix_chunk",
            "feed": feed,
            "room_id": room_id,
            "timestamp_ms": timestamp_ms,
            "sample_rate": sample_rate,
            "format": "pcm_f32",
            "pcm_base64": base64.b64encode(np.asarray(pcm, dtype="<f4").tobytes()).decode(),
            "meta": meta or {},
        }
    )


@dataclass
class PerformanceGroupSession:
    """Real (non-stub) stand-in for Task 6 group sync: pairwise-compose
    every active performer onto a chosen reference over a bounded trailing
    window, per ROADMAP.md's suggested approach."""

    room_id: str
    sample_rate: int = 48000
    cleaners: Dict[str, StreamingCleaner] = field(default_factory=dict)
    buffers: Dict[str, np.ndarray] = field(default_factory=dict)
    reference_id: Optional[str] = None

    def push(self, participant_id: str, pcm: np.ndarray) -> Optional[np.ndarray]:
        cleaner = self.cleaners.setdefault(
            participant_id, StreamingCleaner(sample_rate=self.sample_rate)
        )
        cleaned = cleaner.push(np.asarray(pcm, dtype=np.float32))
        window = int(self.sample_rate * WINDOW_SECONDS)
        buf = np.concatenate(
            [self.buffers.get(participant_id, np.zeros(0, dtype=np.float32)), cleaned]
        )
        self.buffers[participant_id] = buf[-window:]

        active = [pid for pid, b in self.buffers.items() if b.size > 0]
        if not active:
            return None
        if self.reference_id is None or self.reference_id not in active:
            self.reference_id = active[0]

        ref_buf = self.buffers[self.reference_id]
        n_out = min(ref_buf.size, int(self.sample_rate * MIX_CHUNK_SECONDS))
        if n_out <= 0:
            return None

        others = [pid for pid in active if pid != self.reference_id]
        if not others:
            return ref_buf[-n_out:].astype(np.float32)

        aligned_tracks = [ref_buf]
        min_frames_for_dtw = 2048
        for pid in others:
            other_buf = self.buffers[pid]
            if other_buf.size < min_frames_for_dtw or ref_buf.size < min_frames_for_dtw:
                continue
            wav_ref = write_wav_bytes(ref_buf, self.sample_rate)
            wav_other = write_wav_bytes(other_buf, self.sample_rate)
            try:
                warp_path, confidence, hop_ms = align_audio(wav_ref, wav_other)
                playback = render_aligned_playback(wav_ref, wav_other, warp_path, hop_ms)
            except ValueError:
                continue
            synced_pcm, _ = read_wav(playback.synced_other_wav)
            aligned_tracks.append(synced_pcm)

        n = min(n_out, *[t.size for t in aligned_tracks])
        if n <= 0:
            return ref_buf[-n_out:].astype(np.float32)
        mix = np.mean([t[-n:] for t in aligned_tracks], axis=0)
        return mix.astype(np.float32)


class BackendWorker:
    def __init__(self, ws_base: str, room_id: str, mode: str, store: Optional[Any] = None):
        self.ws_base = ws_base
        self.room_id = room_id
        self.mode = mode
        self.store = store
        self._practice: Optional[PracticeSession] = None
        self._performance: Optional[PerformanceGroupSession] = None
        self._practice_participants: List[str] = []

    async def run(self) -> None:
        url = f"{self.ws_base}/ws/audio?room_id={self.room_id}&role=processor"
        async with websockets.connect(url) as ws:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") != "audio_chunk":
                    continue
                reply = self._handle_audio_chunk(msg)
                if reply is not None:
                    await ws.send(reply)

    def _handle_audio_chunk(self, msg: Dict[str, Any]) -> Optional[str]:
        participant_id = msg["participant_id"]
        sample_rate = int(msg.get("sample_rate", 48000))
        timestamp_ms = float(msg.get("timestamp_ms", 0.0))
        pcm = decode_audio_chunk(msg)

        if self.mode == "practice":
            if participant_id not in self._practice_participants:
                self._practice_participants.append(participant_id)
            if self._practice is None and len(self._practice_participants) == 2:
                self._practice = PracticeSession(
                    room_id=self.room_id,
                    participant_ids=self._practice_participants,
                    sample_rate=sample_rate,
                    store=self.store,
                )
            if self._practice is None:
                return None
            result = self._practice.push(participant_id, pcm, timestamp_ms)
            frame = result.get("feed")
            if frame is None or frame.pcm.size == 0:
                return None
            return encode_mix_chunk(
                "enhanced", self.room_id, frame.pcm, sample_rate, timestamp_ms,
                meta={"singers": result.get("singers", []), "used_sync": result.get("used_sync")},
            )

        if self.mode == "performance":
            if self._performance is None:
                self._performance = PerformanceGroupSession(
                    room_id=self.room_id, sample_rate=sample_rate
                )
            mix = self._performance.push(participant_id, pcm)
            if mix is None or mix.size == 0:
                return None
            return encode_mix_chunk(
                "performance_mix", self.room_id, mix, sample_rate, timestamp_ms,
                meta={
                    "anchor_participant_id": self._performance.reference_id,
                    "contributor_ids": list(self._performance.buffers.keys()),
                },
            )

        return None


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http-base", default="http://localhost:8787")
    parser.add_argument("--ws-base", default="ws://localhost:8787")
    parser.add_argument("--room-id", required=True)
    parser.add_argument("--mode", choices=["practice", "performance"], required=True)
    args = parser.parse_args()

    worker = BackendWorker(ws_base=args.ws_base, room_id=args.room_id, mode=args.mode)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
