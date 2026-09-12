import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "audio-intelligence"))
sys.path.insert(0, str(ROOT / "modes"))
sys.path.insert(0, str(ROOT / "signal-processing"))
sys.path.insert(0, str(ROOT / "shared" / "mocks"))

from fixtures import singing_scale
from practice.pipeline import PracticeSession
from storage import SignalStore


def _chunks(pcm, sr, hop_ms=120):
    hop = int(sr * hop_ms / 1000)
    for i in range(0, pcm.size, hop):
        yield pcm[i : i + hop], 1000.0 * i / sr


def test_solo_singer_cleans_without_sync():
    sr = 16000
    pcm = singing_scale(sample_rate=sr, note_ms=280)
    session = PracticeSession("room_p", ["alice", "bob"], sample_rate=sr)
    last = None
    for chunk, t in _chunks(pcm, sr):
        last = session.push("alice", chunk, t)
    assert last is not None
    assert last["used_sync"] is False
    assert last["enhanced_samples"] > 0
    assert "alice" in last["singers"] or last["enhanced_samples"] > 0


def test_two_singers_enable_sync(tmp_path):
    sr = 16000
    pcm = singing_scale(sample_rate=sr, note_ms=280)
    store = SignalStore(root=tmp_path / "signals")
    session = PracticeSession(
        "room_p", ["alice", "bob"], sample_rate=sr, store=store
    )
    last_dual = None
    saw_sync = False
    two_singers = False
    for chunk, t in _chunks(pcm, sr):
        session.push("alice", chunk, t)
        last_dual = session.push("bob", chunk, t)
        saw_sync = saw_sync or last_dual["used_sync"]
        two_singers = two_singers or set(last_dual["singers"]) == {"alice", "bob"}
    assert last_dual is not None
    assert saw_sync is True
    assert two_singers is True
    store.close()
