"""Tests for the Practice-mode "record a take" feature: start_recording /
stop_recording accumulate each participant's full cleaned audio (not the
trimmed live-streaming buffer) and, on stop, batch-align + mix them at full
quality and save the result as a real Task 3 Signal.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "signal-processing"))

from worker import BackendWorker  # noqa: E402
from storage import SignalStore  # noqa: E402

SAMPLE_RATE = 16000


def _melody(hz_list, note_ms=280, sample_rate=SAMPLE_RATE) -> np.ndarray:
    segs = []
    for hz in hz_list:
        n = int(sample_rate * note_ms / 1000)
        t = np.arange(n) / sample_rate
        segs.append((0.4 * np.sin(2 * np.pi * hz * t)).astype(np.float32))
    return np.concatenate(segs)


def _audio_chunk_msg(participant_id: str, pcm: np.ndarray, t_ms: float) -> str:
    return json.dumps({
        "type": "audio_chunk",
        "participant_id": participant_id,
        "timestamp_ms": t_ms,
        "sample_rate": SAMPLE_RATE,
        "channels": 1,
        "format": "pcm_f32",
        "pcm_base64": base64.b64encode(np.asarray(pcm, dtype="<f4").tobytes()).decode(),
    })


def _push_chunks(worker: BackendWorker, pid: str, pcm: np.ndarray, hop_ms=120):
    hop = int(SAMPLE_RATE * hop_ms / 1000)
    for i in range(0, pcm.size, hop):
        piece = pcm[i : i + hop]
        if piece.size == 0:
            continue
        worker._handle_audio_chunk(json.loads(_audio_chunk_msg(pid, piece, 1000.0 * i / SAMPLE_RATE)))


def test_stop_without_start_is_a_noop(tmp_path):
    worker = BackendWorker(ws_base="ws://x", room_id="r1", mode="practice", store=SignalStore(root=tmp_path / "s"))
    assert worker._handle_stop_recording({}) is None


def test_recording_only_supported_in_practice_mode():
    worker = BackendWorker(ws_base="ws://x", room_id="r1", mode="performance")
    reply = json.loads(worker._handle_start_recording({}))
    assert reply["type"] == "recording_failed"
    assert "performance" in reply["reason"]


def test_stop_with_only_one_singer_fails_gracefully(tmp_path):
    store = SignalStore(root=tmp_path / "signals")
    worker = BackendWorker(ws_base="ws://x", room_id="r1", mode="practice", store=store)

    melody = _melody([261.63, 293.66, 329.63])
    _push_chunks(worker, "alice", melody)  # only alice -- no PracticeSession yet (needs 2 participants)

    start_reply = json.loads(worker._handle_start_recording({}))
    assert start_reply["type"] == "recording_started"

    _push_chunks(worker, "alice", melody)

    stop_reply = json.loads(worker._handle_stop_recording({}))
    assert stop_reply["type"] == "recording_failed"


def test_full_take_produces_a_real_synced_signal(tmp_path):
    store = SignalStore(root=tmp_path / "signals")
    worker = BackendWorker(ws_base="ws://x", room_id="room_rec", mode="practice", store=store)

    melody = _melody([261.63, 293.66, 329.63, 349.23, 392.00])
    delayed = np.concatenate([np.zeros(int(SAMPLE_RATE * 0.3), dtype=np.float32), melody])

    # Warm up the PracticeSession (needs both participants seen once).
    _push_chunks(worker, "alice", melody[: SAMPLE_RATE // 2])
    _push_chunks(worker, "bob", delayed[: SAMPLE_RATE // 2])
    assert worker._practice is not None

    start_reply = json.loads(worker._handle_start_recording({}))
    recording_id = start_reply["recording_id"]

    _push_chunks(worker, "alice", melody)
    _push_chunks(worker, "bob", delayed)

    stop_reply = json.loads(worker._handle_stop_recording({}))
    assert stop_reply["type"] == "recording_ready"
    assert stop_reply["recording_id"] == recording_id
    assert set(stop_reply["participant_ids"]) == {"alice", "bob"}
    assert stop_reply["duration_ms"] > 0

    pcm = np.frombuffer(base64.b64decode(stop_reply["pcm_base64"]), dtype="<f4")
    assert pcm.size > 0
    assert np.max(np.abs(pcm)) > 0.0  # not silence

    # A real Task 3 Signal was saved and is replayable.
    signal_id = stop_reply["signal_id"]
    signal = store.get(signal_id)
    assert signal.metadata["kind"] == "practice_recording"
    assert set(signal.metadata["participant_ids"]) == {"alice", "bob"}
    replayed = store.get_audio(signal_id)
    assert len(replayed) > 0
