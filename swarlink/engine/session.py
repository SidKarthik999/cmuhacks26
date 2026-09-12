"""Rooms, roles, and the call log -- the conferencing frame around the audio.

The three features are not standalone tools; they are things that happen
inside a call, and the call has its own state: who is here, what part they
are singing, who is muted, what has been said, and -- the part that matters
for a concert -- how the mix is reaching the people who are only listening.

Deliberately in-memory and deliberately not a conferencing stack. Swarlink's
contribution is the audio: the alignment, the scoring, the lead advance.
Carriage of the media is a solved problem with several good implementations,
and reimplementing it here would add risk without adding an idea. What this
module provides is the state a real transport would need to drive, and an
event log honest enough to demonstrate against.

The event log is not decoration. Every analysis this engine performs is
appended to it with a timestamp, a duration and its headline numbers, which
means the interface can show *when* the system decided something and how long
it took -- and it means a claim like "the lead advance was 105 ms" has a
record behind it rather than a label.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from . import dsp

SAMPLE_RATE = 22050

ROLES = ("host", "teacher", "student", "singer", "lead", "performer", "audience")

# What a listener's client is assumed to do with the mix. Audience members are
# counted rather than mixed -- ninety-five people produce no audio worth
# summing -- but they are not nothing: they are the load, and the bandwidth
# and buffer figures are what make the concert feature a plausible product
# rather than a demo.
AUDIENCE_BITRATE_KBPS = 96.0
AUDIENCE_BUFFER_MS = 240.0


def _now_ms() -> float:
    return time.time() * 1000.0


@dataclass
class Participant:
    """One person in the room."""

    name: str
    role: str = "audience"
    participant_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    muted: bool = False
    gain_db: float = 0.0
    part: Optional[str] = None         # "Alto", "Bass", ... for band members
    voice_profile: Optional[str] = None
    level_db: float = -120.0           # last reported input level
    joined_ms: float = field(default_factory=_now_ms)

    @property
    def performs(self) -> bool:
        return self.role in ("teacher", "student", "singer", "lead", "performer")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.participant_id,
            "name": self.name,
            "role": self.role,
            "part": self.part,
            "voice_profile": self.voice_profile,
            "muted": self.muted,
            "gain_db": round(self.gain_db, 2),
            "level_db": round(self.level_db, 1),
            "performs": self.performs,
            "joined_ms": round(self.joined_ms, 1),
        }


@dataclass
class Event:
    """One line of the call log."""

    kind: str            # "join" | "leave" | "mode" | "analysis" | "mix" | "chat" | "note"
    text: str
    at_ms: float = field(default_factory=_now_ms)
    actor: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self, origin_ms: float = 0.0) -> Dict[str, Any]:
        elapsed = max(self.at_ms - origin_ms, 0.0) / 1000.0
        return {
            "kind": self.kind,
            "text": self.text,
            "actor": self.actor,
            "at_ms": round(self.at_ms, 1),
            "clock": f"{int(elapsed // 60):02d}:{int(elapsed % 60):02d}",
            "data": self.data,
        }


@dataclass
class Room:
    """A call: its people, its mode, its log, and the audio it has stored."""

    name: str
    mode: str = "teach"                # "teach" | "practice" | "perform"
    room_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    participants: List[Participant] = field(default_factory=list)
    events: List[Event] = field(default_factory=list)
    opened_ms: float = field(default_factory=_now_ms)
    sample_rate: int = SAMPLE_RATE
    _audio: Dict[str, np.ndarray] = field(default_factory=dict, repr=False)

    # ----------------------------------------------------------- people

    def join(
        self,
        name: str,
        role: str = "audience",
        part: Optional[str] = None,
        voice_profile: Optional[str] = None,
    ) -> Participant:
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}; expected one of {ROLES}")
        person = Participant(
            name=name, role=role, part=part, voice_profile=voice_profile
        )
        self.participants.append(person)
        self.log(
            "join",
            f"{name} joined as {role}" + (f" on {part}" if part else ""),
            actor=name,
            data={"role": role, "part": part},
        )
        return person

    def join_audience(self, count: int, prefix: str = "Listener") -> int:
        """Seat a block of listeners.

        One log line for the block rather than ninety-five lines, because a
        log nobody can read is not a record. The individual participants are
        still created, so the roster count and the telemetry are real.
        """
        for i in range(count):
            self.participants.append(
                Participant(name=f"{prefix} {i + 1:03d}", role="audience")
            )
        self.log("join", f"{count} listeners joined the audience",
                 data={"count": count})
        return count

    def leave(self, name: str) -> bool:
        for i, p in enumerate(self.participants):
            if p.name == name:
                del self.participants[i]
                self.log("leave", f"{name} left", actor=name)
                return True
        return False

    def find(self, name: str) -> Optional[Participant]:
        return next((p for p in self.participants if p.name == name), None)

    def performers(self) -> List[Participant]:
        return [p for p in self.participants if p.performs]

    def audience(self) -> List[Participant]:
        return [p for p in self.participants if p.role == "audience"]

    def set_mute(self, name: str, muted: bool) -> bool:
        person = self.find(name)
        if person is None:
            return False
        person.muted = muted
        self.log(
            "note",
            f"{name} {'muted' if muted else 'unmuted'}",
            actor=name,
            data={"muted": muted},
        )
        return True

    def set_gain(self, name: str, gain_db: float) -> bool:
        """Move one performer's fader.

        Logged, because in a concert the mix is a shared artefact: if the bass
        is suddenly 6 dB louder, everyone should be able to see who did that
        and when.
        """
        person = self.find(name)
        if person is None:
            return False
        before = person.gain_db
        person.gain_db = float(gain_db)
        self.log(
            "mix",
            f"{name} fader {before:+.1f} -> {gain_db:+.1f} dB",
            actor=name,
            data={"gain_db": round(float(gain_db), 2), "from_db": round(before, 2)},
        )
        return True

    def set_mode(self, mode: str) -> None:
        if mode not in ("teach", "practice", "perform"):
            raise ValueError(f"unknown mode {mode!r}")
        if mode != self.mode:
            self.log("mode", f"switched to {mode} mode",
                     data={"from": self.mode, "to": mode})
            self.mode = mode

    # ------------------------------------------------------------ audio

    def store_audio(self, audio: np.ndarray, label: str) -> str:
        """Keep a take and hand back an id the interface can fetch it by."""
        audio_id = f"{label}-{uuid.uuid4().hex[:8]}"
        self._audio[audio_id] = np.asarray(audio, dtype=np.float32)
        return audio_id

    def get_audio(self, audio_id: str) -> Optional[np.ndarray]:
        return self._audio.get(audio_id)

    def wav(self, audio_id: str) -> Optional[bytes]:
        audio = self.get_audio(audio_id)
        if audio is None:
            return None
        return dsp.to_wav_bytes(audio, self.sample_rate)

    def report_level(self, name: str, audio: np.ndarray) -> float:
        """Update a participant's meter from a block of their input."""
        person = self.find(name)
        if person is None:
            return -120.0
        person.level_db = dsp.dbfs(audio)
        return person.level_db

    # ------------------------------------------------------------- log

    def log(
        self,
        kind: str,
        text: str,
        actor: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Event:
        event = Event(kind=kind, text=text, actor=actor, data=data or {})
        self.events.append(event)
        return event

    def chat(self, name: str, text: str) -> Event:
        return self.log("chat", text, actor=name)

    def log_analysis(
        self, what: str, elapsed_ms: float, headline: str, numbers: Dict[str, Any]
    ) -> Event:
        """Record that an analysis ran, how long it took, and what it found.

        The elapsed time is logged as a first-class field because it is the
        answer to the obvious objection. Aligning and scoring a four-second
        take is not free, and a teacher deciding whether to use this in a
        lesson needs to know whether feedback arrives in a moment or a minute.
        """
        return self.log(
            "analysis",
            f"{what}: {headline}",
            data={"elapsed_ms": round(elapsed_ms, 1), **numbers},
        )

    def log_lines(self, limit: int = 0) -> List[Dict[str, Any]]:
        events = self.events[-limit:] if limit else self.events
        return [e.as_dict(self.opened_ms) for e in events]

    # ------------------------------------------------------- telemetry

    def telemetry(self) -> Dict[str, Any]:
        """What it costs to serve this room, and what the listeners receive.

        Included because the concert feature's credibility rests on it
        scaling. Five performers being mixed and ninety-five people receiving
        one stereo mix is a different proposition from a hundred-way mesh,
        which is what a conferencing tool would attempt: a mesh at this size
        is 9900 streams, and the reason group performance does not work on
        one is arithmetic rather than engineering.
        """
        performers = self.performers()
        listeners = self.audience()
        n_perf, n_aud = len(performers), len(listeners)
        total = n_perf + n_aud
        mesh_streams = total * (total - 1)
        return {
            "participants": total,
            "performers": n_perf,
            "audience": n_aud,
            "uplink_streams": n_perf,
            "downlink_streams": total,
            "mix_streams": 1,
            "mesh_streams_avoided": mesh_streams,
            "audience_bitrate_kbps": AUDIENCE_BITRATE_KBPS,
            "audience_downlink_mbps": round(
                n_aud * AUDIENCE_BITRATE_KBPS / 1000.0, 2
            ),
            "audience_buffer_ms": AUDIENCE_BUFFER_MS,
            "note": (
                f"{n_perf} uplinks are mixed once and sent to {total} people. "
                f"A peer-to-peer mesh of the same room would carry "
                f"{mesh_streams} streams, which is why group performance is "
                "not a feature that conferencing tools simply forgot to add."
            ),
        }

    # ------------------------------------------------------------ view

    def as_dict(self, log_limit: int = 200) -> Dict[str, Any]:
        return {
            "id": self.room_id,
            "name": self.name,
            "mode": self.mode,
            "opened_ms": round(self.opened_ms, 1),
            "elapsed_ms": round(_now_ms() - self.opened_ms, 1),
            "sample_rate": self.sample_rate,
            "participants": [p.as_dict() for p in self.performers()],
            "audience_count": len(self.audience()),
            "roster": [p.as_dict() for p in self.participants[:120]],
            "log": self.log_lines(log_limit),
            "telemetry": self.telemetry(),
            "audio_ids": sorted(self._audio),
        }


def from_scene(scene: Any, room_name: Optional[str] = None) -> Room:
    """Build a room populated from a fixture scene, audio and all.

    This is what makes the demo a demo rather than a mock: the participants
    in the roster are the same objects whose audio the analysis runs on, and
    the ids in the log are the ids the interface fetches WAVs by.
    """
    mode = {"lesson": "teach", "duet": "practice", "concert": "perform"}.get(
        scene.mode, "teach"
    )
    room = Room(name=room_name or scene.title, mode=mode, sample_rate=scene.sample_rate)
    room.log("note", f"{scene.title}: {scene.summary}", data={"scene": scene.key})
    for part in scene.parts:
        room.join(part.name, role=part.role, part=part.name, voice_profile=part.profile)
        audio_id = room.store_audio(part.audio, part.name.lower().replace(" ", "-"))
        room.report_level(part.name, part.audio)
        room.log(
            "note",
            f"{part.name} take stored ({part.duration_ms / 1000.0:.1f} s)",
            actor=part.name,
            data={"audio_id": audio_id, "truth": part.truth},
        )
    audience = int(scene.truth.get("audience", 0))
    if audience:
        room.join_audience(audience)
    return room


class Rooms:
    """A tiny registry, so the API has somewhere to keep rooms between calls."""

    def __init__(self) -> None:
        self._rooms: Dict[str, Room] = {}

    def create(self, name: str, mode: str = "teach") -> Room:
        room = Room(name=name, mode=mode)
        self._rooms[room.room_id] = room
        return room

    def adopt(self, room: Room) -> Room:
        self._rooms[room.room_id] = room
        return room

    def get(self, room_id: str) -> Optional[Room]:
        return self._rooms.get(room_id)

    def all(self) -> List[Room]:
        return list(self._rooms.values())

    def summaries(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": r.room_id,
                "name": r.name,
                "mode": r.mode,
                "performers": len(r.performers()),
                "audience": len(r.audience()),
                "events": len(r.events),
            }
            for r in self._rooms.values()
        ]
