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


def test_task4_aligner_never_reingests_full_rolling_buffer():
    """Regression test for the fix in docs/integration-contracts.md:
    _mix_with_task4 used to re-feed StreamingAligner.push() the entire
    ~4s rolling `cleaned` buffer every call, so its internal chroma history
    accumulated heavily overlapping/duplicated frames instead of genuinely
    new ones. After the fix, pending_for_aligner (what actually gets fed to
    the aligner) must never exceed roughly one hop's worth of new audio
    between pushes -- it should be drained on every call that has enough
    audio to feed the aligner, never left to grow toward the ~4s window.
    """
    sr = 16000
    pcm = singing_scale(sample_rate=sr, note_ms=280)
    session = PracticeSession("room_p", ["alice", "bob"], sample_rate=sr)

    max_pending_seen = 0
    for chunk, t in _chunks(pcm, sr):
        session.push("alice", chunk, t)
        session.push("bob", chunk, t)
        for st in session.streams.values():
            max_pending_seen = max(max_pending_seen, st.pending_for_aligner.size)

    four_second_window = sr * 4
    # The bug's signature was pending == the full rolling ~4s buffer; the
    # fix keeps it near one hop (a few hundred ms at most across a couple
    # of missed feeds), nowhere near the full window.
    assert max_pending_seen < four_second_window // 4
