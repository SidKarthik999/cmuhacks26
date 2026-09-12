"""Task 8 — shared pitch tracking + discrete notes.

Person C Task 5 must import `extract_pitch_contour` from here (or from
`audio-intelligence.notes`). Signature is stable:

    extract_pitch_contour(audio, sample_rate=None) -> list[{time_ms, pitch_hz, confidence}]

`audio` may be a float32 PCM array, 16-bit int PCM, or WAV bytes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Union

import numpy as np

from audio_io import pcm_to_float32, read_wav
from schema import validate_note_event, validate_pitch_contour
from yin import yin_pitch

AudioLike = Union[np.ndarray, bytes, bytearray]

HOP_MS = 10.0
FRAME_MS = 40.0
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def extract_pitch_contour(
    audio: AudioLike,
    sample_rate: Optional[int] = None,
    hop_ms: float = HOP_MS,
) -> List[Dict[str, Any]]:
    pcm, sr = _coerce_audio(audio, sample_rate)
    hop = max(1, int(sr * hop_ms / 1000.0))
    frame = max(hop * 2, int(sr * FRAME_MS / 1000.0))
    if pcm.size < frame:
        frame = pcm.size
    points: List[Dict[str, Any]] = []
    for start in range(0, max(1, pcm.size - frame + 1), hop):
        sl = pcm[start : start + frame]
        t_ms = 1000.0 * start / sr
        pitch, conf = yin_pitch(sl, sr)
        points.append(
            {
                "time_ms": float(t_ms),
                "pitch_hz": None if pitch is None or conf < 0.25 else float(pitch),
                "confidence": float(conf),
            }
        )
    payload = {"sample_rate": int(sr), "hop_ms": float(hop_ms), "points": points}
    validate_pitch_contour(payload)
    return points


def contour_document(
    audio: AudioLike, sample_rate: Optional[int] = None, hop_ms: float = HOP_MS
) -> Dict[str, Any]:
    pcm, sr = _coerce_audio(audio, sample_rate)
    points = extract_pitch_contour(pcm, sr, hop_ms=hop_ms)
    doc = {"sample_rate": int(sr), "hop_ms": float(hop_ms), "points": points}
    validate_pitch_contour(doc)
    return doc


def hz_to_midi(hz: float) -> int:
    return int(round(69 + 12 * np.log2(hz / 440.0)))


def midi_to_name(midi: int) -> str:
    name = NOTE_NAMES[midi % 12]
    octave = midi // 12 - 1
    return f"{name}{octave}"


def notes_from_contour(
    points: Sequence[Dict[str, Any]],
    min_duration_ms: float = 80.0,
) -> List[Dict[str, Any]]:
    """Quantize a pitch contour into NoteEvent dicts."""
    notes: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    def close(end_ms: float) -> None:
        nonlocal current
        if current is None:
            return
        dur = end_ms - current["start_ms"]
        if dur >= min_duration_ms:
            ev = {
                "pitch_midi": int(current["pitch_midi"]),
                "note_name": midi_to_name(int(current["pitch_midi"])),
                "start_ms": float(current["start_ms"]),
                "duration_ms": float(dur),
                "confidence": float(current["confidence"]),
                "velocity": float(min(1.0, current["confidence"])),
            }
            validate_note_event(ev)
            notes.append(ev)
        current = None

    for p in points:
        t = float(p["time_ms"])
        hz = p["pitch_hz"]
        conf = float(p["confidence"])
        if hz is None or conf < 0.3:
            close(t)
            continue
        midi = hz_to_midi(float(hz))
        if current is None:
            current = {
                "pitch_midi": midi,
                "start_ms": t,
                "confidence": conf,
            }
            continue
        if midi != current["pitch_midi"]:
            close(t)
            current = {"pitch_midi": midi, "start_ms": t, "confidence": conf}
        else:
            current["confidence"] = 0.7 * current["confidence"] + 0.3 * conf
    if points:
        last_t = float(points[-1]["time_ms"]) + hop_ms_from_points(points)
        close(last_t)
    return notes


def hop_ms_from_points(points: Sequence[Dict[str, Any]]) -> float:
    if len(points) < 2:
        return HOP_MS
    return max(1.0, float(points[1]["time_ms"]) - float(points[0]["time_ms"]))


def signal_to_notes(
    audio: AudioLike, sample_rate: Optional[int] = None
) -> List[Dict[str, Any]]:
    pcm, sr = _coerce_audio(audio, sample_rate)
    return notes_from_contour(extract_pitch_contour(pcm, sr))


def notes_to_simple_midi(notes: Sequence[Dict[str, Any]], ticks_per_beat: int = 480) -> bytes:
    """Minimal Type-0 MIDI (note on/off). Tempo assumed 120 BPM for timing."""
    events = []
    us_per_beat = 500_000
    for n in notes:
        start_tick = int(n["start_ms"] / 1000.0 * (1_000_000 / us_per_beat) * ticks_per_beat)
        end_tick = int(
            (n["start_ms"] + n["duration_ms"])
            / 1000.0
            * (1_000_000 / us_per_beat)
            * ticks_per_beat
        )
        vel = max(1, min(127, int(n.get("velocity", 0.8) * 96)))
        events.append((start_tick, 0x90, n["pitch_midi"], vel))
        events.append((end_tick, 0x80, n["pitch_midi"], 0))
    events.sort()
    track = bytearray()
    # tempo meta
    track += _vlq(0) + bytes([0xFF, 0x51, 0x03, 0x07, 0xA1, 0x20])
    last = 0
    for tick, status, pitch, vel in events:
        track += _vlq(tick - last)
        track += bytes([status, int(pitch), int(vel)])
        last = tick
    track += _vlq(0) + bytes([0xFF, 0x2F, 0x00])
    header = bytes(
        [
            0x4D,
            0x54,
            0x68,
            0x64,
            0,
            0,
            0,
            6,
            0,
            0,
            0,
            1,
            (ticks_per_beat >> 8) & 0xFF,
            ticks_per_beat & 0xFF,
        ]
    )
    th = bytes(
        [
            0x4D,
            0x54,
            0x72,
            0x6B,
            (len(track) >> 24) & 0xFF,
            (len(track) >> 16) & 0xFF,
            (len(track) >> 8) & 0xFF,
            len(track) & 0xFF,
        ]
    )
    return header + th + bytes(track)


def _vlq(n: int) -> bytes:
    n = max(0, int(n))
    chunks = [n & 0x7F]
    n >>= 7
    while n:
        chunks.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(chunks))


def _coerce_audio(
    audio: AudioLike, sample_rate: Optional[int]
) -> tuple[np.ndarray, int]:
    if isinstance(audio, (bytes, bytearray)):
        if len(audio) >= 12 and audio[:4] == b"RIFF":
            return read_wav(bytes(audio))
        if sample_rate is None:
            raise ValueError("sample_rate required for raw PCM bytes")
        pcm = np.frombuffer(bytes(audio), dtype="<i2").astype(np.float32) / 32768.0
        return pcm, sample_rate
    pcm = pcm_to_float32(np.asarray(audio))
    if sample_rate is None:
        raise ValueError("sample_rate required for PCM arrays")
    return pcm, sample_rate
