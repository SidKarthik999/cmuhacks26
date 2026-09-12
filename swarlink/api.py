"""HTTP surface for Swarlink: rooms, the three analyses, and audio.

Thin on purpose. Every endpoint is a call into `swarlink.engine` plus JSON
serialisation, so the interface and the checks exercise the same code paths
and there is no second implementation of anything to drift.

Three design points worth stating:

**Analyses are cached by their result id.** Alignment and cleaning are the
expensive part and they are deterministic, so re-running them because someone
moved a fader would be both slow and pointless. A concert analysis returns an
id; moving a fader re-sums the already-corrected stems under that id, which is
a weighted addition and returns in milliseconds.

**Audio is served by id, not inlined.** A four-second take is about 180 kB of
PCM; five stems plus two mixes base64-encoded into a JSON body is several
megabytes of response for a page that wants to draw waveforms immediately and
play audio only if asked. Waveform envelopes travel in the JSON, samples
travel on demand.

**Uploads and fixtures go through the same path.** The demo runs on
synthesised singers with known ground truth, and a real microphone take enters
at exactly the same function. That is deliberate: a demo that only works on
its own fixtures is a slideshow.
"""
from __future__ import annotations

import io
import time
import uuid
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .engine import (
    bridge,
    concert,
    dsp,
    duet,
    fixtures,
    lesson,
    metrics,
    session,
)

VERSION = "0.1.0"
SR = 22050

app = FastAPI(title="Swarlink", version=VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOMS = session.Rooms()

# Result id -> (kind, result object). Held in memory: a hackathon demo and a
# research prototype both want the process to be the whole system, and the
# alternative is a storage layer that adds no idea.
_RESULTS: Dict[str, Any] = {}
_AUDIO: Dict[str, np.ndarray] = {}


def _keep_audio(audio: np.ndarray, label: str) -> str:
    audio_id = f"{label}-{uuid.uuid4().hex[:8]}"
    _AUDIO[audio_id] = np.asarray(audio, dtype=np.float32)
    return audio_id


def _read_upload(raw: bytes) -> np.ndarray:
    """Decode an uploaded WAV and bring it to the engine's analysis rate."""
    try:
        audio, sr = dsp.from_wav_bytes(raw)
    except Exception as exc:  # pragma: no cover - depends on client encoder
        raise HTTPException(400, f"could not read that audio: {exc}") from exc
    if audio.size == 0:
        raise HTTPException(400, "that audio file is empty")
    return dsp.resample(audio, sr, SR) if sr != SR else audio


# --------------------------------------------------------------- health


@app.get("/api/health")
def health() -> Dict[str, Any]:
    """What is running, and what the shared modules can do in this checkout."""
    team = bridge.available()
    return {
        "ok": True,
        "version": VERSION,
        "sample_rate": SR,
        "cleaning": team["cleaning"],
        "cleaning_error": team["cleaning_error"],
        "notes": team["notes"],
        "notes_error": team["notes_error"],
        "weighting": {
            "pitch": metrics.PITCH_WEIGHT,
            "volume": metrics.VOLUME_WEIGHT,
        },
        "rooms": len(ROOMS.all()),
    }


@app.get("/api/glossary")
def glossary() -> List[Dict[str, Any]]:
    """Every metric with its meaning, for the interface's help panel."""
    return metrics.glossary()


@app.get("/api/scenes")
def scenes() -> List[Dict[str, Any]]:
    """The built-in scenes, each with the ground truth used to build it."""
    return fixtures.catalogue()


# ---------------------------------------------------------------- rooms


class RoomCreate(BaseModel):
    name: Optional[str] = None
    scene: Optional[str] = Field(
        None, description="Fixture scene key, e.g. 'concert:band'"
    )
    mode: str = "teach"


@app.post("/api/rooms")
def create_room(body: RoomCreate = Body(...)) -> Dict[str, Any]:
    if body.scene:
        try:
            scene = fixtures.load(body.scene)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        room = ROOMS.adopt(session.from_scene(scene, body.name))
    else:
        room = ROOMS.create(body.name or "Untitled room", body.mode)
    return room.as_dict()


@app.get("/api/rooms")
def list_rooms() -> List[Dict[str, Any]]:
    return ROOMS.summaries()


def _room(room_id: str) -> session.Room:
    room = ROOMS.get(room_id)
    if room is None:
        raise HTTPException(404, f"no room {room_id!r}")
    return room


@app.get("/api/rooms/{room_id}")
def get_room(room_id: str, log_limit: int = Query(200, ge=0, le=2000)) -> Dict[str, Any]:
    return _room(room_id).as_dict(log_limit)


class JoinBody(BaseModel):
    name: str
    role: str = "audience"
    part: Optional[str] = None
    voice_profile: Optional[str] = None


@app.post("/api/rooms/{room_id}/join")
def join(room_id: str, body: JoinBody = Body(...)) -> Dict[str, Any]:
    room = _room(room_id)
    try:
        person = room.join(body.name, body.role, body.part, body.voice_profile)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return person.as_dict()


class ChatBody(BaseModel):
    name: str
    text: str


@app.post("/api/rooms/{room_id}/chat")
def chat(room_id: str, body: ChatBody = Body(...)) -> Dict[str, Any]:
    room = _room(room_id)
    return room.chat(body.name, body.text).as_dict(room.opened_ms)


class MuteBody(BaseModel):
    name: str
    muted: bool


@app.post("/api/rooms/{room_id}/mute")
def mute(room_id: str, body: MuteBody = Body(...)) -> Dict[str, Any]:
    room = _room(room_id)
    if not room.set_mute(body.name, body.muted):
        raise HTTPException(404, f"{body.name!r} is not in this room")
    return room.as_dict(40)


class GainBody(BaseModel):
    name: str
    gain_db: float


@app.post("/api/rooms/{room_id}/gain")
def gain(room_id: str, body: GainBody = Body(...)) -> Dict[str, Any]:
    room = _room(room_id)
    if not room.set_gain(body.name, body.gain_db):
        raise HTTPException(404, f"{body.name!r} is not in this room")
    return room.as_dict(40)


class ModeBody(BaseModel):
    mode: str


@app.post("/api/rooms/{room_id}/mode")
def mode(room_id: str, body: ModeBody = Body(...)) -> Dict[str, Any]:
    room = _room(room_id)
    try:
        room.set_mode(body.mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return room.as_dict(40)


# -------------------------------------------------------------- lesson


class SceneBody(BaseModel):
    scene: Optional[str] = None
    room_id: Optional[str] = None
    clean: bool = True


def _record(room_id: Optional[str], what: str, elapsed_ms: float,
            headline: str, numbers: Dict[str, Any]) -> None:
    if not room_id:
        return
    room = ROOMS.get(room_id)
    if room is not None:
        room.log_analysis(what, elapsed_ms, headline, numbers)


@app.post("/api/lesson")
def run_lesson(body: SceneBody = Body(...)) -> Dict[str, Any]:
    """Score a student's imitation against a teacher's demonstration."""
    scene = _scene_or_404(body.scene or "lesson:scale", "lesson")
    started = time.time()
    result = lesson.run_scene(scene, clean=body.clean)
    elapsed = (time.time() - started) * 1000.0
    payload = _lesson_payload(result, scene, elapsed)
    _record(
        body.room_id, "lesson score", elapsed, result.score.headline(),
        {
            "overall": round(result.score.overall, 1),
            "pitch_score": round(result.score.pitch_score, 1),
            "volume_score": round(result.score.volume_score, 1),
        },
    )
    return payload


@app.post("/api/lesson/upload")
async def run_lesson_upload(
    teacher: UploadFile = File(...),
    student: UploadFile = File(...),
    clean: bool = Query(True),
    room_id: Optional[str] = Query(None),
) -> Dict[str, Any]:
    """The same analysis on two real recordings."""
    t_audio = _read_upload(await teacher.read())
    s_audio = _read_upload(await student.read())
    started = time.time()
    result = lesson.run(t_audio, s_audio, sr=SR, clean=clean)
    elapsed = (time.time() - started) * 1000.0
    payload = _lesson_payload(result, None, elapsed)
    _record(
        room_id, "lesson score (uploaded)", elapsed, result.score.headline(),
        {"overall": round(result.score.overall, 1)},
    )
    return payload


def _lesson_payload(
    result: lesson.LessonResult, scene: Optional[Any], elapsed_ms: float
) -> Dict[str, Any]:
    payload = result.as_dict()
    payload["result_id"] = _stash("lesson", result)
    payload["elapsed_ms"] = round(elapsed_ms, 1)
    payload["audio"] = {
        "teacher": _keep_audio(result.teacher.audio, "teacher"),
        "student": _keep_audio(result.student.audio, "student"),
        "student_aligned": _keep_audio(result.student_aligned, "student-aligned"),
    }
    if scene is not None:
        payload["scene"] = scene.describe()
    return payload


# ---------------------------------------------------------------- duet


@app.post("/api/duet")
def run_duet(body: SceneBody = Body(...)) -> Dict[str, Any]:
    """Align, level-match and mix two takes recorded independently."""
    scene = _scene_or_404(body.scene or "duet:thirds", "duet")
    started = time.time()
    result = duet.run_scene(scene, clean=body.clean)
    elapsed = (time.time() - started) * 1000.0
    payload = _duet_payload(result, scene, elapsed)
    _record(
        body.room_id, "duet mix", elapsed, result.sync["verdict"],
        {
            "tightness_before_ms": result.sync["tightness_before_ms"],
            "tightness_after_ms": result.sync["tightness_after_ms"],
        },
    )
    return payload


@app.post("/api/duet/upload")
async def run_duet_upload(
    first: UploadFile = File(...),
    second: UploadFile = File(...),
    clean: bool = Query(True),
    room_id: Optional[str] = Query(None),
) -> Dict[str, Any]:
    a = _read_upload(await first.read())
    b = _read_upload(await second.read())
    started = time.time()
    result = duet.run(a, b, sr=SR, clean=clean)
    elapsed = (time.time() - started) * 1000.0
    payload = _duet_payload(result, None, elapsed)
    _record(room_id, "duet mix (uploaded)", elapsed, result.sync["verdict"], {})
    return payload


def _duet_payload(
    result: duet.DuetResult, scene: Optional[Any], elapsed_ms: float
) -> Dict[str, Any]:
    payload = result.as_dict()
    payload["result_id"] = _stash("duet", result)
    payload["elapsed_ms"] = round(elapsed_ms, 1)
    payload["audio"] = {
        "mix": _keep_audio(result.mix, "duet-mix"),
        "naive_mix": _keep_audio(result.naive_mix, "duet-naive"),
        "singers": [
            _keep_audio(a, f"duet-{i}") for i, a in enumerate(result.aligned)
        ],
    }
    if scene is not None:
        payload["scene"] = scene.describe()
    return payload


# ------------------------------------------------------------- concert


class ConcertBody(BaseModel):
    scene: Optional[str] = None
    room_id: Optional[str] = None
    clean: bool = True
    gains_db: Optional[List[float]] = None
    lead_advance_ms: Optional[float] = Field(
        None, description="Override the measured advance, for the demo slider"
    )


@app.post("/api/concert")
def run_concert(body: ConcertBody = Body(...)) -> Dict[str, Any]:
    """Clean, time-correct and mix a band captured over five connections."""
    scene = _scene_or_404(body.scene or "concert:band", "concert")
    started = time.time()
    result = concert.run_scene(
        scene,
        clean=body.clean,
        gains_db=body.gains_db,
        lead_advance_ms=body.lead_advance_ms,
    )
    elapsed = (time.time() - started) * 1000.0
    payload = result.as_dict()
    payload["result_id"] = _stash("concert", result)
    payload["elapsed_ms"] = round(elapsed, 1)
    payload["scene"] = scene.describe()
    payload["audio"] = {
        "mix": _keep_audio(result.mix, "concert-mix"),
        "naive_mix": _keep_audio(result.naive_mix, "concert-naive"),
        "stems": [_keep_audio(s.aligned, f"stem-{i}") for i, s in enumerate(result.stems)],
    }
    _record(
        body.room_id, "concert mix", elapsed,
        f"lead advanced {result.lead_advance_ms:.0f} ms, entries "
        f"{result.timing['spread_before_ms']:.0f} -> "
        f"{result.timing['spread_after_ms']:.0f} ms apart",
        {
            "lead_advance_ms": result.timing["lead_advance_ms"],
            "spread_before_ms": result.timing["spread_before_ms"],
            "spread_after_ms": result.timing["spread_after_ms"],
        },
    )
    return payload


class RemixBody(BaseModel):
    result_id: str
    gains_db: List[float]
    room_id: Optional[str] = None


@app.post("/api/concert/remix")
def remix(body: RemixBody = Body(...)) -> Dict[str, Any]:
    """Re-sum an existing concert at new fader settings.

    Separate from `/api/concert` because a fader must not re-run cleaning and
    alignment. Those are the seconds; this is the milliseconds.
    """
    entry = _RESULTS.get(body.result_id)
    if entry is None or entry[0] != "concert":
        raise HTTPException(404, f"no concert result {body.result_id!r}")
    result: concert.ConcertResult = entry[1]
    started = time.time()
    try:
        mixed = concert.remix(result, body.gains_db)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    elapsed = (time.time() - started) * 1000.0
    audio_id = _keep_audio(mixed, "concert-remix")
    for stem, gain in zip(result.stems, body.gains_db):
        stem.gain_db = float(gain)
    return {
        "result_id": body.result_id,
        "elapsed_ms": round(elapsed, 1),
        "gains_db": [round(float(g), 2) for g in body.gains_db],
        "audio": {"mix": audio_id},
        "mix": {
            "wave": dsp.downsample_envelope(mixed, 900),
            "lufs": round(dsp.loudness_lufs(mixed, SR), 2),
            "peak": round(float(np.max(np.abs(mixed))) if mixed.size else 0.0, 4),
        },
    }


# ---------------------------------------------------------------- audio


@app.get("/api/audio/{audio_id}.wav")
def audio(audio_id: str) -> Response:
    """Serve a stored take as a 16-bit WAV the browser can play directly."""
    got = _AUDIO.get(audio_id)
    if got is None:
        for room in ROOMS.all():
            got = room.get_audio(audio_id)
            if got is not None:
                break
    if got is None:
        raise HTTPException(404, f"no audio {audio_id!r}")
    return Response(
        content=dsp.to_wav_bytes(got, SR),
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=3600"},
    )


# --------------------------------------------------------------- utils


def _stash(kind: str, result: Any) -> str:
    result_id = f"{kind}-{uuid.uuid4().hex[:10]}"
    _RESULTS[result_id] = (kind, result)
    return result_id


def _scene_or_404(key: str, expected_mode: str) -> Any:
    try:
        scene = fixtures.load(key)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    if scene.mode != expected_mode:
        raise HTTPException(
            400,
            f"{key!r} is a {scene.mode} scene; this endpoint wants a "
            f"{expected_mode} scene",
        )
    return scene
