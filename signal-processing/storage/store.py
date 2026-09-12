"""Signal storage + replay: the persistence layer for Task 3.

Persists Signal metadata in SQLite and audio blobs on the filesystem
(lossless WAV/FLAC only, per the roadmap — downstream cleaning/note-
conversion tasks are sensitive to compression artifacts).

Usage:
    store = SignalStore(root=Path("./data/signals"))
    signal = store.save(
        participant_id="p1", room_id="r1",
        start_time=1000.0, end_time=2500.0,
        sample_rate=44100, audio_bytes=wav_bytes, audio_format="wav",
    )
    fetched = store.get(signal.id)
    audio_bytes = store.get_audio(signal.id)   # bit-faithful replay
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schema import Signal, validate_signal_dict

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    participant_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    start_time REAL NOT NULL,
    end_time REAL NOT NULL,
    data TEXT NOT NULL
);
"""


class SignalNotFoundError(KeyError):
    pass


class SignalStore:
    """Persists Signals (metadata in SQLite, audio blobs on disk)."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.blob_dir = self.root / "blobs"
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "signals.db"
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SignalStore":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def save(
        self,
        *,
        participant_id: str,
        room_id: str,
        start_time: float,
        end_time: float,
        sample_rate: int,
        audio_bytes: bytes,
        audio_format: str = "wav",
        channels: int = 1,
        sample_width_bytes: int = 2,
        role: Optional[str] = None,
        mode: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        signal_id: Optional[str] = None,
    ) -> Signal:
        """Extract-and-store: write the audio blob + metadata row for a
        singing segment (Task 2's on/off boundaries -> this Signal)."""
        signal_id = signal_id or f"sig_{uuid.uuid4()}"
        blob_path = self.blob_dir / f"{signal_id}.{audio_format}"
        blob_path.write_bytes(audio_bytes)

        signal_dict = {
            "id": signal_id,
            "participant_id": participant_id,
            "room_id": room_id,
            "role": role,
            "mode": mode,
            "start_time": start_time,
            "end_time": end_time,
            "sample_rate": sample_rate,
            "channels": channels,
            "audio_ref": str(blob_path.relative_to(self.root)),
            "audio_format": audio_format,
            "sample_width_bytes": sample_width_bytes,
            "metadata": metadata or {},
        }
        validate_signal_dict(signal_dict)

        self._conn.execute(
            "INSERT INTO signals (id, participant_id, room_id, start_time, end_time, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                signal_id,
                participant_id,
                room_id,
                start_time,
                end_time,
                json.dumps(signal_dict),
            ),
        )
        self._conn.commit()
        return Signal.from_dict(signal_dict)

    def get(self, signal_id: str) -> Signal:
        """Fetch a Signal's metadata by ID."""
        row = self._conn.execute(
            "SELECT data FROM signals WHERE id = ?", (signal_id,)
        ).fetchone()
        if row is None:
            raise SignalNotFoundError(signal_id)
        return Signal.from_dict(json.loads(row[0]))

    def get_audio(self, signal_id: str) -> bytes:
        """Replay API: fetch a Signal's raw audio bytes, bit-faithful to
        what was stored, independent of whether the originating call/room
        is still active."""
        signal = self.get(signal_id)
        blob_path = self.root / signal.audio_ref
        if not blob_path.exists():
            raise SignalNotFoundError(f"audio blob missing for {signal_id}")
        return blob_path.read_bytes()

    def update_metadata(self, signal_id: str, **metadata_updates: Any) -> Signal:
        """Merge new keys into a Signal's metadata (e.g. Task 7 setting
        `cleaned=True`) without touching the stored audio blob."""
        signal = self.get(signal_id)
        signal.metadata.update(metadata_updates)
        signal_dict = signal.to_dict()
        validate_signal_dict(signal_dict)
        self._conn.execute(
            "UPDATE signals SET data = ? WHERE id = ?",
            (json.dumps(signal_dict), signal_id),
        )
        self._conn.commit()
        return signal

    def list_for_room(self, room_id: str) -> List[Signal]:
        rows = self._conn.execute(
            "SELECT data FROM signals WHERE room_id = ? ORDER BY start_time ASC",
            (room_id,),
        ).fetchall()
        return [Signal.from_dict(json.loads(r[0])) for r in rows]
