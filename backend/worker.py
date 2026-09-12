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
MIX_CROSSFADE_MS = 15.0


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
    window, per ROADMAP.md's suggested approach.

    Emission is tracked with a cursor (`_emitted_abs`) on the reference
    stream's cumulative-cleaned-sample timeline, not a fixed trailing
    duration: `push()` is called once per incoming audio_chunk, and those
    chunks can be any size, so slicing a fixed-duration tail either
    re-emits audio (chunk smaller than the tail) or silently drops it
    (chunk larger than the tail) -- see docs/integration-contracts.md. A
    trailing margin is withheld from every emission since the DTW warp
    near the window's newest edge can still be revised once more audio
    arrives.
    """

    room_id: str
    sample_rate: int = 48000
    cleaners: Dict[str, StreamingCleaner] = field(default_factory=dict)
    buffers: Dict[str, np.ndarray] = field(default_factory=dict)
    reference_id: Optional[str] = None
    _ref_total_fed: int = field(default=0, repr=False)
    _emitted_abs: int = field(default=0, repr=False)
    # Up to MIX_CROSSFADE_MS of audio that's been rendered but deliberately
    # NOT yet emitted -- held back so the next call can blend its own more
    # current estimate of that exact same never-before-heard time range in
    # before finally committing it, rather than hard-cutting between two
    # independently-rendered chunks (see docs/integration-contracts.md).
    _prev_tail: Optional[np.ndarray] = field(default=None, repr=False)

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

        previous_reference = self.reference_id
        if self.reference_id is None or self.reference_id not in active:
            self.reference_id = active[0]

        if self.reference_id != previous_reference:
            # Reference changed (initial pick, or reassignment on dropout)
            # -- the timeline basis is different now. Resync _ref_total_fed
            # from the buffer's current size directly (it already reflects
            # this call's contribution if participant_id is the new
            # reference) rather than also adding cleaned.size below, which
            # would double-count it.
            self._ref_total_fed = self.buffers[self.reference_id].size
            self._emitted_abs = 0
        elif participant_id == self.reference_id:
            self._ref_total_fed += cleaned.size

        ref_buf = self.buffers[self.reference_id]
        others = [pid for pid in active if pid != self.reference_id]

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

        mix_len = min(t.size for t in aligned_tracks)
        if mix_len <= 0:
            return None
        mix = np.mean([t[-mix_len:] for t in aligned_tracks], axis=0).astype(np.float32)

        # `mix` covers the trailing `mix_len` samples of the current
        # window, i.e. absolute range [window_start_abs, window_start_abs
        # + mix_len) on the reference's cumulative timeline.
        window_start_abs = self._ref_total_fed - ref_buf.size
        margin_samples = int(self.sample_rate * 0.2)  # ~200ms still-revisable edge
        stable_end_local = mix_len - margin_samples

        if self._emitted_abs < window_start_abs:
            self._emitted_abs = window_start_abs  # fell behind the window; resync
            self._prev_tail = None  # no valid held-back audio to crossfade

        start_local = self._emitted_abs - window_start_abs
        if stable_end_local <= start_local:
            return None  # nothing new and stable enough to emit yet

        # Crossfade: `self._prev_tail`, if present, is audio held back last
        # call rather than emitted -- covering [start_local, start_local +
        # fade_n) in THIS call's window, a time range nobody has heard yet.
        # Blend it with this call's own (more current) estimate of that
        # same range, then emit the blend followed by genuinely new
        # content. Blending against already-emitted audio instead (an
        # earlier version of this fix) doesn't remove the discontinuity --
        # it only relocates it to just before the blended region; see
        # docs/integration-contracts.md.
        crossfade_samples = int(self.sample_rate * MIX_CROSSFADE_MS / 1000)
        if self._prev_tail is not None and self._prev_tail.size:
            fade_n = min(self._prev_tail.size, stable_end_local - start_local)
            current_estimate = mix[start_local : start_local + fade_n]
            fade_out = np.linspace(1.0, 0.0, fade_n, dtype=np.float32)
            fade_in = 1.0 - fade_out
            blended = self._prev_tail[:fade_n] * fade_out + current_estimate * fade_in
        else:
            fade_n = 0
            blended = np.zeros(0, dtype=np.float32)

        remainder_start = start_local + fade_n
        commit_end_local = max(remainder_start, stable_end_local - crossfade_samples)
        new_remainder = mix[remainder_start:commit_end_local]
        new_output = np.concatenate([blended, new_remainder])

        self._prev_tail = mix[commit_end_local:stable_end_local].copy()
        self._emitted_abs = window_start_abs + commit_end_local
        return new_output if new_output.size else None


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
