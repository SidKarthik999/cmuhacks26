import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from supervisor import rooms_needing_workers  # noqa: E402


def _done_task():
    async def _noop():
        pass

    async def make():
        t = asyncio.ensure_future(_noop())
        await t
        return t

    return asyncio.run(make())


def test_skips_unsupported_modes():
    rooms = [
        {"room_id": "r1", "mode": "teach"},
        {"room_id": "r2", "mode": "practice"},
        {"room_id": "r3", "mode": "performance"},
    ]
    needing = rooms_needing_workers(rooms, {})
    assert {r["room_id"] for r in needing} == {"r2", "r3"}


def test_skips_rooms_with_an_active_worker():
    async def never_finishes():
        await asyncio.Event().wait()

    async def scenario():
        task = asyncio.ensure_future(never_finishes())
        try:
            rooms = [{"room_id": "r1", "mode": "performance"}]
            needing = rooms_needing_workers(rooms, {"r1": task})
            assert needing == []
        finally:
            task.cancel()

    asyncio.run(scenario())


def test_respawns_a_room_whose_worker_finished():
    rooms = [{"room_id": "r1", "mode": "performance"}]
    done_task = _done_task()
    needing = rooms_needing_workers(rooms, {"r1": done_task})
    assert needing == rooms
