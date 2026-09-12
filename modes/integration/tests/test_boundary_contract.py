"""Cross-track integration test for the platform <-> audio backend boundary
documented in docs/integration-contracts.md.

Nobody currently tests this boundary: Person A's TypeScript encoder
(float32ToBase64) and Person B/C's Python decoding of `audio_chunk` messages
are each covered by unit tests in isolation, but no test proves the two
languages actually agree on the wire format. This test runs Person A's real
encoder (via `npx tsx`, not a Python re-implementation of it) on real audio,
decodes the result in Python, and feeds it through Person B's detector and
Person C's storage to prove the whole chain -- not just each side alone.
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "audio-intelligence"))
sys.path.insert(0, str(ROOT / "signal-processing"))

from detection import detect_buffer  # noqa: E402
from storage import SignalStore  # noqa: E402

PROBE_SCRIPT = ROOT / "modes" / "integration" / "encode_probe.mts"

_NODE_AVAILABLE = shutil.which("npx") is not None


def _encode_via_platform_ts(samples: np.ndarray) -> str:
    """Run Person A's real float32ToBase64 (TypeScript) on `samples` and
    return the base64 string it produced. Raises if Node/tsx aren't set up
    (callers should skip, not fail, in that case)."""
    payload = json.dumps({"samples": [float(x) for x in samples]})
    result = subprocess.run(
        ["npx", "tsx", str(PROBE_SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"encode_probe.mts failed: {result.stderr}")
    return json.loads(result.stdout)["base64"]


def _decode_audio_chunk_base64(b64: str) -> np.ndarray:
    """Person B/C-side decode of an `audio_chunk` message's pcm_base64
    field, per docs/integration-contracts.md's documented frame shape
    (Float32 little-endian PCM)."""
    raw = base64.b64decode(b64)
    return np.frombuffer(raw, dtype="<f4").copy()


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="Node/npx not available to run the real TS encoder")
def test_python_decode_matches_real_typescript_encoder():
    """Golden-vector check: encode known floats with A's actual encoder,
    decode in Python, and confirm exact agreement -- not just that each
    side's own unit tests pass independently."""
    samples = np.array([0.0, 0.25, -0.5, 0.999969482421875, -1.0, 0.0001234], dtype=np.float32)

    b64 = _encode_via_platform_ts(samples)
    decoded = _decode_audio_chunk_base64(b64)

    np.testing.assert_array_equal(decoded, samples)


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="Node/npx not available to run the real TS encoder")
def test_audio_encoded_by_platform_is_usable_by_detection_and_storage(tmp_path):
    """End-to-end: synthesize a melody, encode it exactly as the platform
    would over the WebSocket boundary, decode it back, and confirm Person
    B's detector and Person C's storage both operate correctly on audio
    that actually crossed the language boundary."""
    sample_rate = 16000
    t = np.arange(int(sample_rate * 1.2)) / sample_rate
    melody = (0.4 * np.sin(2 * np.pi * 293.66 * t)).astype(np.float32)  # sustained D4

    b64 = _encode_via_platform_ts(melody)
    decoded = _decode_audio_chunk_base64(b64)

    assert decoded.shape == melody.shape
    np.testing.assert_array_equal(decoded, melody)

    events = detect_buffer(decoded, sample_rate, participant_id="p_boundary")
    assert any(e["kind"] == "singing_started" for e in events)

    from audio_io import write_wav_bytes

    with SignalStore(root=tmp_path / "signals") as store:
        signal = store.save(
            participant_id="p_boundary",
            room_id="room_boundary",
            start_time=0.0,
            end_time=1200.0,
            sample_rate=sample_rate,
            audio_bytes=write_wav_bytes(decoded, sample_rate),
        )
        assert store.get_audio(signal.id) == write_wav_bytes(decoded, sample_rate)
