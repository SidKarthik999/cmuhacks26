"""Auto-attaches backend/worker.py's BackendWorker to every practice/
performance room the platform API knows about, so real audio processing
(Task 2/4/7, and the Task 6 stand-in) actually runs for any room created
through the web UI -- no manual per-room `python worker.py --room-id ...`
invocation needed.

Polls GET /rooms, spawns one BackendWorker per practice/performance room it
hasn't seen yet, and respawns one whose connection dropped (room deleted,
server restart, network blip).

Usage:
    python backend/supervisor.py --http-base http://localhost:8787 --ws-base ws://localhost:8787
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

from worker import BackendWorker  # noqa: E402

POLL_INTERVAL_SECONDS = 2.0
# Teach mode isn't a BackendWorker.mode this handles yet (Task 5/Teach mode
# aren't implemented) -- skip rather than spawn a worker that would just
# connect and never produce output.
SUPPORTED_MODES = {"practice", "performance"}


def fetch_rooms(http_base: str) -> List[Dict[str, Any]]:
    with urllib.request.urlopen(f"{http_base}/rooms", timeout=5) as resp:
        return json.loads(resp.read())["rooms"]


def rooms_needing_workers(
    rooms: List[Dict[str, Any]], active: Dict[str, "asyncio.Task[None]"]
) -> List[Dict[str, Any]]:
    """Which rooms should get a (re)spawned worker this poll cycle."""
    out = []
    for room in rooms:
        if room.get("mode") not in SUPPORTED_MODES:
            continue
        existing = active.get(room["room_id"])
        if existing is not None and not existing.done():
            continue
        out.append(room)
    return out


async def _run_and_log(worker: BackendWorker, room_id: str) -> None:
    try:
        await worker.run()
    except Exception as e:  # noqa: BLE001 -- log and let the poll loop respawn
        print(f"[supervisor] worker for {room_id} exited: {e}")


async def supervise(http_base: str, ws_base: str) -> None:
    active: Dict[str, "asyncio.Task[None]"] = {}
    print(f"[supervisor] watching {http_base}/rooms every {POLL_INTERVAL_SECONDS}s")
    while True:
        try:
            rooms = fetch_rooms(http_base)
        except Exception as e:  # noqa: BLE001
            print(f"[supervisor] failed to fetch rooms: {e}")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            continue

        for room in rooms_needing_workers(rooms, active):
            room_id, mode = room["room_id"], room["mode"]
            print(f"[supervisor] attaching worker to {room_id} (mode={mode})")
            worker = BackendWorker(ws_base=ws_base, room_id=room_id, mode=mode)
            active[room_id] = asyncio.create_task(_run_and_log(worker, room_id))

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http-base", default="http://localhost:8787")
    parser.add_argument("--ws-base", default="ws://localhost:8787")
    args = parser.parse_args()
    asyncio.run(supervise(args.http_base, args.ws_base))


if __name__ == "__main__":
    main()
