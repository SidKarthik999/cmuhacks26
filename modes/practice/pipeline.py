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

# Each committed mix chunk comes from an independent re-render of the
# aligner's current window (a fresh DTW warp path + resample), not a
# continuation of the previous chunk's synthesis -- so the waveform value
# at the end of one chunk and the start of the next aren't guaranteed to
# match, audible as a click/tick at every chunk boundary (see
# docs/integration-contracts.md). Crossfading a short overlap between
# consecutive chunks smooths that seam.
MIX_CROSSFADE_MS = 15.0


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
    # Cumulative count of samples ever appended into aligner_window_audio,
    # before trimming -- lets _mix_with_task4 compute the current window's
    # absolute start position on stream a's timeline even as old audio gets
    # dropped off the front of the (size-capped) window.
    aligner_total_fed: int = 0
    # Newly-cleaned audio not yet emitted via the solo (no dual-sync)
    # output path. Draining this instead of re-slicing the rolling
    # `cleaned` buffer's tail avoids re-emitting already-heard audio (see
    # docs/integration-contracts.md).
    pending_for_solo_emit: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
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
        # Position (in stream a's cumulative aligner-fed sample timeline)
        # up to which the dual-sync mix has already been emitted. See
        # _mix_with_task4.
        self._mix_emitted_abs = 0
        # Up to MIX_CROSSFADE_MS of audio that's been rendered but
        # deliberately NOT yet emitted -- held back so the next call can
        # blend its own (more current) estimate of that exact same
        # never-before-heard time range in before finally committing it,
        # rather than hard-cutting between two independently-rendered
        # chunks. None whenever there's no valid held-back audio to blend
        # (first chunk, or right after a resync jump).
        self._mix_prev_tail: Optional[np.ndarray] = None
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
            st.pending_for_solo_emit = np.concatenate([st.pending_for_solo_emit, cleaned])

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
            # This call's freshly-cleaned chunk for `participant_id` (not
            # the trimmed rolling `cleaned` buffer) -- callers that need a
            # full, untrimmed per-participant recording (e.g. a "record
            # this take" feature) should accumulate this themselves rather
            # than reading st.cleaned, which is capped to the last ~4s.
            "cleaned_chunk": cleaned,
        }

    def _mix(self) -> tuple[np.ndarray, bool]:
        active = [s for s in self.streams.values() if s.singing and s.cleaned.size]
        if not active:
            solos = [s for s in self.streams.values() if s.cleaned.size]
            if len(solos) == 1:
                return self._drain_solo_emit(solos[0]), False
            return np.zeros(0, dtype=np.float32), False
        if len(active) == 1:
            return self._drain_solo_emit(active[0]), False

        a, b = active[0], active[1]

        if self._aligner is None or render_aligned_playback is None:
            # Task 4's real streaming aligner isn't importable at all (not
            # "not ready yet this call" -- see below) -- fall back to the
            # legacy mock constant-offset aligner so Practice mode still
            # produces *a* dual mix. Not exercised in normal operation
            # since signal-processing/sync is a hard dependency here.
            align = sync_streaming(a.cleaned, b.cleaned, self.sample_rate, self._last_align)
            self._last_align = align
            offset = float(align.get("offset_ms") or 0.0)
            a_pcm = apply_offset(a.cleaned, max(0.0, -offset), self.sample_rate)
            b_pcm = apply_offset(b.cleaned, max(0.0, offset), self.sample_rate)
            n = min(a_pcm.size, b_pcm.size, int(0.25 * self.sample_rate))
            mix = 0.5 * a_pcm[-n:] + 0.5 * b_pcm[-n:]
            return mix.astype(np.float32), True

        mixed = self._mix_with_task4(a, b)
        if mixed is None:
            # Not enough newly-arrived audio for the aligner yet this call
            # -- NOT the same as Task 4 being unavailable. Previously this
            # fell through to the mock aligner above on every such call
            # (which happens routinely, not just on error), silently
            # replacing the real DTW-synced mix with the buggy fixed-tail
            # mock mix most of the time (see docs/integration-contracts.md).
            return np.zeros(0, dtype=np.float32), False
        return mixed, True

    def _drain_solo_emit(self, st: ParticipantStream) -> np.ndarray:
        """Return exactly the newly-cleaned audio not yet emitted (no
        dual-sync partner active), instead of re-slicing a fixed-duration
        tail of the rolling `cleaned` buffer -- the latter re-emits already
        -heard audio whenever the emission cadence differs from the input
        cadence (see docs/integration-contracts.md)."""
        out = st.pending_for_solo_emit
        st.pending_for_solo_emit = np.zeros(0, dtype=np.float32)
        return out

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
        a.aligner_total_fed += new_a.size
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

        # `pcm` covers the current window's absolute sample range
        # [window_start_abs, window_start_abs + pcm.size) on stream a's
        # cumulative aligner-fed timeline. Emit only the genuinely new part
        # since we last emitted -- never a fixed-duration tail, which
        # either repeats audio (tail > new-content-per-call) or silently
        # drops it (tail < new-content-per-call). Also hold back a trailing
        # margin: the DTW warp path near the window's newest edge can still
        # be revised once more audio arrives, so don't commit it yet.
        window_start_abs = a.aligner_total_fed - a.aligner_window_audio.size
        margin_samples = self._aligner.hop_length * 2
        stable_end_local = pcm.size - margin_samples

        if self._mix_emitted_abs < window_start_abs:
            # Fell behind further than the window covers (e.g. a long gap)
            # -- resync rather than try to backfill audio no longer in the
            # window. No valid continuity to crossfade against afterward.
            self._mix_emitted_abs = window_start_abs
            self._mix_prev_tail = None

        start_local = self._mix_emitted_abs - window_start_abs
        if stable_end_local <= start_local:
            return None  # nothing new and stable enough to emit yet

        # Crossfade: `self._mix_prev_tail`, if present, is audio that was
        # held back last call rather than emitted -- i.e. it covers
        # [start_local, start_local + fade_n) in THIS call's window, a time
        # range nobody has heard yet. Blend it with this call's (more
        # informed) estimate of that same range instead of hard-cutting to
        # either one, then emit the blend followed by genuinely new
        # content. (Blending against already-emitted audio, as an earlier
        # version of this fix did, doesn't remove the discontinuity -- it
        # just relocates it to before the blended region; see
        # docs/integration-contracts.md.)
        crossfade_samples = int(self.sample_rate * MIX_CROSSFADE_MS / 1000)
        if self._mix_prev_tail is not None and self._mix_prev_tail.size:
            fade_n = min(self._mix_prev_tail.size, stable_end_local - start_local)
            current_estimate = pcm[start_local : start_local + fade_n].astype(np.float32)
            fade_out = np.linspace(1.0, 0.0, fade_n, dtype=np.float32)
            fade_in = 1.0 - fade_out
            blended = self._mix_prev_tail[:fade_n] * fade_out + current_estimate * fade_in
        else:
            fade_n = 0
            blended = np.zeros(0, dtype=np.float32)

        remainder_start = start_local + fade_n
        # Hold back a fresh crossfade_samples of newly-stable audio for the
        # *next* call to blend against, rather than emitting it now.
        commit_end_local = max(remainder_start, stable_end_local - crossfade_samples)
        new_remainder = pcm[remainder_start:commit_end_local].astype(np.float32)
        new_output = np.concatenate([blended, new_remainder])

        self._mix_prev_tail = pcm[commit_end_local:stable_end_local].astype(np.float32).copy()
        self._mix_emitted_abs = window_start_abs + commit_end_local
        return new_output if new_output.size else None

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
