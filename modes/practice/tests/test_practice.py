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


def test_emitted_audio_duration_does_not_grossly_exceed_wall_clock_duration():
    """Regression test for the fixed-duration-tail bug described in
    docs/integration-contracts.md: solo and dual-sync emission both used to
    return a fixed-size tail every call regardless of how much genuinely
    new audio had arrived. With a ~120ms input cadence and a 250ms output
    tail, that meant most of every emitted chunk was already-heard audio --
    total emitted duration over a session could run several times the
    session's actual wall-clock duration. After the fix (emit cursors that
    track exactly what's new), total emitted duration should track the
    session's wall-clock duration closely, not run away.
    """
    sr = 16000
    pcm = singing_scale(sample_rate=sr, note_ms=280)
    session = PracticeSession("room_p", ["alice", "bob"], sample_rate=sr)

    total_emitted = 0
    for chunk, t in _chunks(pcm, sr):
        r1 = session.push("alice", chunk, t)
        r2 = session.push("bob", chunk, t)
        total_emitted += r1["enhanced_samples"] + r2["enhanced_samples"]

    wall_clock_samples = pcm.size
    ratio = total_emitted / wall_clock_samples
    # Real behavior after the fix comes out close to 1.0 (observed ~0.94:
    # slightly under, from margin held back at the end of the stream plus
    # aligner warm-up latency at the start -- both expected). The old bug
    # produced several times the wall-clock duration (observed ~3.9x, from
    # both the fixed-tail overlap and mock-fallback fallthrough -- see
    # docs/integration-contracts.md), so this range is a meaningful guard,
    # not just a loose upper bound.
    assert 0.5 <= ratio <= 1.2


def test_dual_sync_chunk_boundaries_are_smooth_not_clicky():
    """Regression test for the click/"percussion" artifact described in
    docs/integration-contracts.md: each committed dual-sync chunk comes
    from an independent re-render of the aligner's window, so consecutive
    chunks' waveform values weren't guaranteed to connect -- audible as a
    click at every chunk boundary. Checks that the sample-to-sample jump
    AT each chunk boundary is the same order of magnitude as jumps WITHIN
    a chunk, not roughly 10x larger (observed before the crossfade fix)."""
    sr = 16000
    pcm_alice = singing_scale(sample_rate=sr, note_ms=280)
    pcm_bob = np.concatenate([np.zeros(int(sr * 0.4), dtype=np.float32), pcm_alice.copy()])
    session = PracticeSession("room_p", ["alice", "bob"], sample_rate=sr)

    mix_pieces = []
    for (ca, ta), (cb, tb) in zip(_chunks(pcm_alice, sr), _chunks(pcm_bob, sr)):
        session.push("alice", ca, ta)
        result = session.push("bob", cb, tb)
        frame = result.get("feed")
        if frame is not None and frame.pcm.size:
            mix_pieces.append(frame.pcm.copy())

    assert len(mix_pieces) >= 3, "need several dual-sync chunks to check boundaries"

    interior_deltas = np.concatenate(
        [np.abs(np.diff(p)) for p in mix_pieces if p.size > 1]
    )
    boundary_jumps = np.array(
        [abs(mix_pieces[i][-1] - mix_pieces[i + 1][0]) for i in range(len(mix_pieces) - 1)]
    )
    ratio = np.median(boundary_jumps) / np.median(interior_deltas)
    # Observed ~10.7x before the crossfade fix, ~1.2x after. A generous
    # ceiling well below the old value still catches a regression.
    assert ratio < 3.0
