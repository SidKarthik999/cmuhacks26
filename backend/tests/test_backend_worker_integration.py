"""End-to-end test of the real framework wiring: the actual
platform/api/server.ts (Node), the real backend/worker.py (Python), and real
WebSocket clients standing in for a performer's mic and a listener's
playback -- no mocks on either side.

This is the concrete test for the integration gaps flagged in
docs/integration-contracts.md ("Known integration gaps" #1 and #2): before
this, Practice/Performance mode's real Person B/C implementations were never
actually invoked through the platform boundary, only through in-process
Python calls (Practice) or TypeScript stubs (Performance). This proves the
`role=processor` WebSocket path added to server.ts actually carries real
audio through Person B's cleaning and Person C's sync and back out to a
listener.

Requires Node + npx (skips otherwise, same as test_boundary_contract.py).
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import websockets

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from worker import BackendWorker, decode_audio_chunk, encode_mix_chunk  # noqa: E402

_NODE_AVAILABLE = shutil.which("npx") is not None
_START_SCRIPT = ROOT / "backend" / "tests" / "start_server_for_test.mts"

SAMPLE_RATE = 22050


def _melody(hz_list, note_ms=300, sample_rate=SAMPLE_RATE):
    segs = []
    for hz in hz_list:
        n = int(sample_rate * note_ms / 1000)
        t = np.arange(n) / sample_rate
        segs.append((0.4 * np.sin(2 * np.pi * hz * t)).astype(np.float32))
    return np.concatenate(segs)


async def _start_server() -> tuple[subprocess.Popen, int]:
    proc = subprocess.Popen(
        ["npx", "tsx", str(_START_SCRIPT)],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdout is not None
    for _ in range(200):
        line = proc.stdout.readline()
        if not line:
            await asyncio.sleep(0.05)
            continue
        m = re.search(r"PORT=(\d+)", line)
        if m:
            return proc, int(m.group(1))
    proc.kill()
    raise RuntimeError("server did not print PORT=<n> in time")


async def _http_json(method: str, url: str, body: dict | None = None) -> dict:
    import urllib.request

    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"content-type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="Node/npx not available to run the real platform server")
@pytest.mark.asyncio
async def test_performance_mix_flows_through_real_server_and_real_worker():
    proc, port = await _start_server()
    http_base = f"http://127.0.0.1:{port}"
    ws_base = f"ws://127.0.0.1:{port}"
    try:
        room = await _http_json(
            "POST", f"{http_base}/rooms", {"mode": "performance", "room_id": "room_integration_test"}
        )
        assert room["mode"] == "performance"

        lead = await _http_json(
            "POST", f"{http_base}/rooms/{room['room_id']}/join",
            {"display_name": "Lead", "role": "lead"},
        )
        other = await _http_json(
            "POST", f"{http_base}/rooms/{room['room_id']}/join",
            {"display_name": "Other", "role": "performer"},
        )
        listener = await _http_json(
            "POST", f"{http_base}/rooms/{room['room_id']}/join",
            {"display_name": "Listener", "role": "listener"},
        )
        lead_id = lead["participant"]["participant_id"]
        other_id = other["participant"]["participant_id"]
        listener_id = listener["participant"]["participant_id"]

        worker = BackendWorker(ws_base=ws_base, room_id=room["room_id"], mode="performance")
        worker_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.3)  # let the processor connection register

        received_mixes: list[dict] = []

        async def listen_as(participant_id: str):
            url = f"{ws_base}/ws/audio?room_id={room['room_id']}&role=client&participant_id={participant_id}"
            async with websockets.connect(url) as ws:
                while True:
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    if msg.get("type") == "feed_chunk" and msg.get("feed") == "performance_mix":
                        received_mixes.append(msg)
                        if len(received_mixes) >= 1:
                            return

        listener_task = asyncio.create_task(listen_as(listener_id))

        melody_lead = _melody([261.63, 293.66, 329.63, 349.23, 392.00])
        melody_other = np.concatenate(
            [np.zeros(int(SAMPLE_RATE * 0.3), dtype=np.float32), melody_lead]
        )

        url = f"{ws_base}/ws/audio?room_id={room['room_id']}&role=ingest"
        async with websockets.connect(url) as sender:
            chunk = SAMPLE_RATE // 2
            for start in range(0, max(len(melody_lead), len(melody_other)), chunk):
                for pid, buf in ((lead_id, melody_lead), (other_id, melody_other)):
                    piece = buf[start : start + chunk]
                    if piece.size == 0:
                        continue
                    await sender.send(
                        json.dumps(
                            {
                                "type": "audio_chunk",
                                "participant_id": pid,
                                "timestamp_ms": 1000.0 * start / SAMPLE_RATE,
                                "sample_rate": SAMPLE_RATE,
                                "channels": 1,
                                "format": "pcm_f32",
                                "pcm_base64": base64.b64encode(
                                    np.asarray(piece, dtype="<f4").tobytes()
                                ).decode(),
                            }
                        )
                    )
                await asyncio.sleep(0.02)

        mix_msg = await asyncio.wait_for(listener_task, timeout=15)

        assert len(received_mixes) >= 1
        mix = received_mixes[0]
        assert mix["feed"] == "performance_mix"
        pcm = decode_audio_chunk({"pcm_base64": mix["pcm_base64"]})
        assert pcm.size > 0
        assert np.max(np.abs(pcm)) > 0.0  # not silence/all-zero

        worker_task.cancel()
    finally:
        proc.kill()
        proc.wait(timeout=5)
