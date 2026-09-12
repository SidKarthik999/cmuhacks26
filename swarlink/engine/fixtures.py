"""Deterministic scenes: singers, rooms, and known ground truth.

Every Swarlink feature is a measurement, and a measurement is only
trustworthy if you can point it at something whose answer you already know.
These fixtures are that something. Each scene synthesises two or more takes
from the same written notes, applies a *documented* set of perturbations
-- detune in cents, tempo ratio, gain in dB, entry lag in milliseconds,
room noise at a chosen SNR -- and hands back both the audio and the numbers
that were used to make it.

So the pipelines can be judged, not just demonstrated: if the lesson scorer
says a student is 18 cents sharp and the fixture detuned them by 20, that
gap is the error of the whole chain -- synthesis, cleaning, alignment,
tracking, scoring -- and it is visible without a microphone, a studio, or a
human in the loop.

Two properties are deliberate:

* **Seeded, not random.** Every scene is reproducible from its key alone, so
  a regression is a regression and not a bad draw. Different singers in a
  scene get different seeds, because two takes built from the same noise
  stream share their vibrato and jitter and would align far better than two
  real people ever do.
* **Cached.** Synthesising a five-part concert is a few hundred milliseconds
  of arithmetic; the validation sweep asks for the same scenes thousands of
  times. Scenes are memoised on their arguments.

The audience in the concert scenes is not synthesised. Ninety-five listeners
produce no audio worth mixing -- they produce telemetry, which belongs to the
session layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import dsp, voice

SAMPLE_RATE = voice.SAMPLE_RATE

# A performer hears the lead through a network hop, a decoder, and a speaker.
# Round numbers for a fixture, but the shape is real: roughly a tenth of a
# second, never identical between two people on two connections.
LEAD_DELAY_MS = 100.0


# --------------------------------------------------------------------------
# Rooms
# --------------------------------------------------------------------------

def _pink(n: int, rng: np.random.Generator) -> np.ndarray:
    """Pink noise via spectral shaping -- the honest shape of a quiet room.

    White noise is the wrong fixture. A real room is dominated by low
    frequencies (traffic, HVAC, structure), and a noise reducer tuned on
    white noise will look far better than it is, because white noise puts
    most of its energy where singing is not.
    """
    spec = np.fft.rfft(rng.normal(0.0, 1.0, n))
    freqs = np.fft.rfftfreq(n, 1.0 / SAMPLE_RATE)
    shape = 1.0 / np.sqrt(np.maximum(freqs, 1.0))
    out = np.fft.irfft(spec * shape, n=n)
    peak = float(np.max(np.abs(out)))
    return (out / peak if peak > 0 else out).astype(np.float32)


def _hiss(n: int, rng: np.random.Generator) -> np.ndarray:
    """Preamp hiss: white, broadband, the thing spectral subtraction is for."""
    return rng.normal(0.0, 0.3, n).astype(np.float32)


def _hum(n: int, rng: np.random.Generator, base: float = 60.0) -> np.ndarray:
    """Mains hum and its odd harmonics, slightly unstable in phase."""
    t = np.arange(n) / SAMPLE_RATE
    out = np.zeros(n, dtype=np.float64)
    for k, level in ((1, 1.0), (2, 0.35), (3, 0.5), (5, 0.2)):
        phase = float(rng.uniform(0.0, 2.0 * np.pi))
        drift = 0.02 * np.sin(2.0 * np.pi * 0.11 * t + phase)
        out += level * np.sin(2.0 * np.pi * base * k * t * (1.0 + drift) + phase)
    peak = float(np.max(np.abs(out)))
    return (out / peak if peak > 0 else out).astype(np.float32)


def _laptop(n: int, rng: np.random.Generator) -> np.ndarray:
    """The realistic worst case: a fan, a hum, and hiss, all at once."""
    return (0.7 * _pink(n, rng) + 0.5 * _hum(n, rng) + 0.4 * _hiss(n, rng)).astype(np.float32)


ROOMS: Dict[str, Callable[[int, np.random.Generator], np.ndarray]] = {
    "silent": lambda n, rng: np.zeros(n, dtype=np.float32),
    "quiet_room": _pink,
    "hiss": _hiss,
    "hum": _hum,
    "laptop": _laptop,
}

ROOM_NOTES = {
    "silent": "No noise at all -- the control case.",
    "quiet_room": "Pink noise: HVAC and traffic, weighted to the low end.",
    "hiss": "White preamp hiss, broadband.",
    "hum": "60 Hz mains hum with odd harmonics.",
    "laptop": "Fan, hum and hiss together -- a laptop mic in a bedroom.",
}


def add_room(
    x: np.ndarray,
    room: str = "quiet_room",
    snr_db: float = 24.0,
    seed: int = 0,
    sr: int = SAMPLE_RATE,
) -> np.ndarray:
    """Mix a noise bed under a take at a given signal-to-noise ratio.

    The SNR is measured against the take's *voiced* level rather than its
    overall RMS. A take with a silent lead-in has a low overall RMS, so
    asking for 24 dB SNR against that average would quietly put the noise
    20 dB louder than intended during the singing itself.
    """
    x = np.asarray(x, dtype=np.float32)
    if x.size == 0 or room == "silent":
        return x
    rng = np.random.default_rng(seed)
    bed = ROOMS[room](x.size, rng)

    env = dsp.frame_rms(x, sr=sr)
    voiced = env[env > 0.2 * float(np.max(env))] if env.size else env
    signal = float(np.mean(voiced)) if voiced.size else float(dsp.rms(x))
    bed_rms = float(dsp.rms(bed)) or 1.0
    target = signal / (10.0 ** (snr_db / 20.0))
    return (x + bed * (target / bed_rms)).astype(np.float32)


# --------------------------------------------------------------------------
# Scene model
# --------------------------------------------------------------------------

@dataclass
class Part:
    """One singer's contribution to a scene, plus how it was made."""

    name: str
    role: str
    profile: str
    audio: np.ndarray
    notes: List[dict] = field(default_factory=list)
    truth: Dict[str, Any] = field(default_factory=dict)
    sample_rate: int = SAMPLE_RATE

    @property
    def duration_ms(self) -> float:
        return 1000.0 * len(self.audio) / self.sample_rate

    def summary(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "profile": self.profile,
            "duration_ms": round(self.duration_ms, 1),
            "peak": round(float(np.max(np.abs(self.audio))) if self.audio.size else 0.0, 4),
            "lufs": round(dsp.loudness_lufs(self.audio, self.sample_rate), 2),
            "truth": self.truth,
        }


@dataclass
class Scene:
    """A reproducible multi-singer situation with a known answer."""

    key: str
    title: str
    mode: str          # "lesson" | "duet" | "concert"
    summary: str
    parts: List[Part]
    truth: Dict[str, Any] = field(default_factory=dict)
    sample_rate: int = SAMPLE_RATE

    def part(self, name: str) -> Part:
        for p in self.parts:
            if p.name == name:
                return p
        raise KeyError(f"{self.key} has no part {name!r}; have {[p.name for p in self.parts]}")

    @property
    def names(self) -> List[str]:
        return [p.name for p in self.parts]

    def describe(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "mode": self.mode,
            "summary": self.summary,
            "sample_rate": self.sample_rate,
            "truth": self.truth,
            "parts": [p.summary() for p in self.parts],
        }


# --------------------------------------------------------------------------
# Written material
# --------------------------------------------------------------------------

# Deliberately plain: a scale exercises every scale degree once, which is the
# worst case for a note segmenter (no repeated pitch to lean on) and the best
# case for reading a report, because every error has an obvious address.
MAJOR_SCALE = "C4 D4 E4 F4 G4 A4 B4 C5"
DESCENDING = "C5 B4 A4 G4 F4 E4 D4 C4"

# A phrase with repeated notes, a leap, and unequal durations -- the things a
# scale does not test.
LESSON_PHRASE = "G4 G4 A4 B4 B4 A4 G4 E4"
LESSON_RHYTHM = (420.0, 420.0, 560.0, 700.0, 420.0, 420.0, 560.0, 840.0)

# Two independent lines that share a harmonic grid, for the duet scenes.
DUET_UPPER = "E4 F4 G4 A4 G4 F4 E4 E4"
DUET_LOWER = "C4 D4 E4 F4 E4 D4 C4 C4"

# Five parts that make triads on every beat, listed lead-first: the soprano
# carries the tune and the others support it, which is the shape the concert
# feature assumes. The parts span bass to soprano so the mix has real
# spectral separation instead of five altos in a row.
#
# Nothing dips below F2 (87 Hz). A bass line written at C2 is perfectly
# singable and perfectly normal, but 65 Hz is under the pitch tracker's 70 Hz
# floor, so those notes would read as unvoiced -- a fixture limitation
# masquerading as a performance problem.
CHORALE = {
    "Sopran1": "C5 B4 C5 A4 C5 A4 B4 C5",
    "Sopran2": "G4 G4 A4 F4 G4 F4 G4 G4",
    "Alto":    "E4 B3 C4 A3 E4 A3 B3 E4",
    "Tenor":   "G3 D3 E3 C3 G3 C3 D3 G3",
    "Bass":    "C3 G2 A2 F2 C3 F2 G2 C3",
}
CHORALE_PROFILES = {
    "Sopran1": "soprano",
    "Sopran2": "alto",
    "Alto": "alto",
    "Tenor": "tenor",
    "Bass": "bass",
}

BAND_LINE = "C4 E4 G4 E4 C4 E4 D4 C4"
# One lead plus four performers, each on a different voice and a different
# connection. The delays are the point of the concert feature. The bass sits
# at -21 semitones (E-flat 2, 78 Hz) rather than a tidier -24, for the same
# tracker-floor reason as the chorale.
BAND = (
    #  name        profile      semitones  delay_ms  gain_db  detune
    ("Lead",      "alto",        0,          0.0,     0.0,     0.0),
    ("Harmony",   "soprano",    +4,        104.0,    -2.5,    +7.0),
    ("Tenor",     "tenor",     -12,         92.0,    -1.0,    -9.0),
    ("Baritone",  "baritone",  -17,        118.0,    -3.5,    +4.0),
    ("Bass",      "bass",      -21,         97.0,     1.5,    -5.0),
)


def _rhythm(pitches: str, durations: Sequence[float], vowels: Sequence[str]) -> List[voice.Note]:
    names = pitches.split()
    return [
        voice.Note(p, float(durations[i % len(durations)]), vowels[i % len(vowels)], 1.0)
        for i, p in enumerate(names)
    ]


# --------------------------------------------------------------------------
# Scenes
# --------------------------------------------------------------------------

_CACHE: Dict[Tuple[Any, ...], Scene] = {}


def _cached(fn: Callable[..., Scene]) -> Callable[..., Scene]:
    def wrapper(**kwargs: Any) -> Scene:
        key = (fn.__name__,) + tuple(sorted(kwargs.items()))
        hit = _CACHE.get(key)
        if hit is None:
            hit = fn(**kwargs)
            _CACHE[key] = hit
        return hit
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


@_cached
def lesson(
    material: str = "scale",
    detune_cents: float = 18.0,
    tempo_ratio: float = 1.06,
    gain_db: float = -4.0,
    entry_ms: float = 160.0,
    room: str = "quiet_room",
    snr_db: float = 26.0,
    teacher_profile: str = "alto",
    student_profile: str = "soprano",
    seed: int = 11,
) -> Scene:
    """Teacher demonstrates, student imitates -- with every error known.

    The student is not a copy of the teacher with noise added. They are a
    different voice, at a different tempo, entering late, at a different
    level, in a different room. That combination is what makes the scene
    worth scoring: a chain that only survives when the two takes are near
    twins is a chain that will fail on the first real lesson.
    """
    if material == "phrase":
        notes = _rhythm(LESSON_PHRASE, LESSON_RHYTHM, ("ah", "ee", "oh"))
    elif material == "descending":
        notes = voice.phrase(DESCENDING, 460.0)
    else:
        notes = voice.phrase(MAJOR_SCALE, 500.0, vowels=("ah", "ee", "oo", "eh"))

    teacher = voice.sing(
        notes,
        voice.PROFILES[teacher_profile],
        tempo_ratio=1.0,
        lead_in_ms=320.0,
        seed=seed,
    )
    student = voice.sing(
        notes,
        voice.PROFILES[student_profile],
        tempo_ratio=tempo_ratio,
        detune_cents=detune_cents,
        gain_db=gain_db,
        lead_in_ms=320.0 + entry_ms,
        seed=seed + 500,
    )

    t_audio = add_room(teacher.audio, room, snr_db, seed=seed * 7)
    s_audio = add_room(student.audio, room, snr_db - 4.0, seed=seed * 13)

    return Scene(
        key=f"lesson:{material}",
        title="Lesson " + material,
        mode="lesson",
        summary=(
            f"{teacher_profile} teacher, {student_profile} student; student "
            f"{detune_cents:+.0f} cents, {tempo_ratio:.3f}x tempo, "
            f"{gain_db:+.1f} dB, {entry_ms:.0f} ms late, {room} at {snr_db - 4:.0f} dB SNR"
        ),
        parts=[
            Part("Teacher", "teacher", teacher_profile, t_audio, teacher.notes,
                 {"detune_cents": 0.0, "tempo_ratio": 1.0, "gain_db": 0.0,
                  "entry_ms": 0.0, "room": room, "snr_db": snr_db}),
            Part("Student", "student", student_profile, s_audio, student.notes,
                 {"detune_cents": detune_cents, "tempo_ratio": tempo_ratio,
                  "gain_db": gain_db, "entry_ms": entry_ms, "room": room,
                  "snr_db": snr_db - 4.0}),
        ],
        truth={
            "material": material,
            "written_notes": [n.pitch for n in notes],
            "detune_cents": detune_cents,
            "tempo_ratio": tempo_ratio,
            "gain_db": gain_db,
            "entry_ms": entry_ms,
        },
    )


@_cached
def duet(
    material: str = "unison",
    lag_ms: float = 180.0,
    tempo_ratio: float = 1.05,
    gain_db: float = -6.0,
    room: str = "laptop",
    snr_db: float = 22.0,
    upper_profile: str = "soprano",
    lower_profile: str = "tenor",
    seed: int = 23,
) -> Scene:
    """Two people singing alone, at their own pace, who never heard each other.

    That is the actual situation the duet feature addresses, so the fixture
    reproduces it literally: two takes with no shared clock and no shared
    reference, differing in start time, tempo and level. The `unison`
    variant is the hard one for a scorer -- the same notes twice, where any
    alignment error looks like a tuning error -- and `thirds` is the hard one
    for a mixer, because the parts must stay distinguishable in the sum.
    """
    if material == "thirds":
        upper_pitches, lower_pitches = DUET_UPPER, DUET_LOWER
    elif material == "octaves":
        upper_pitches = DUET_LOWER
        lower_pitches = " ".join(voice.transpose(DUET_LOWER.split(), -12))
    else:
        upper_pitches = lower_pitches = DUET_LOWER

    upper_notes = voice.phrase(upper_pitches, 520.0, vowels=("ah", "oo"))
    lower_notes = voice.phrase(lower_pitches, 520.0, vowels=("ah", "oo"))

    a = voice.sing(upper_notes, voice.PROFILES[upper_profile],
                   lead_in_ms=280.0, seed=seed)
    b = voice.sing(lower_notes, voice.PROFILES[lower_profile],
                   tempo_ratio=tempo_ratio, gain_db=gain_db,
                   lead_in_ms=280.0 + lag_ms, seed=seed + 700)

    a_audio = add_room(a.audio, room, snr_db, seed=seed * 3)
    b_audio = add_room(b.audio, room, snr_db - 3.0, seed=seed * 5)

    return Scene(
        key=f"duet:{material}",
        title="Duet " + material,
        mode="duet",
        summary=(
            f"{upper_profile} and {lower_profile} in {material}; second singer "
            f"{lag_ms:.0f} ms late at {tempo_ratio:.3f}x and {gain_db:+.1f} dB, "
            f"{room} at {snr_db:.0f} dB SNR"
        ),
        parts=[
            Part("Singer A", "singer", upper_profile, a_audio, a.notes,
                 {"lag_ms": 0.0, "tempo_ratio": 1.0, "gain_db": 0.0}),
            Part("Singer B", "singer", lower_profile, b_audio, b.notes,
                 {"lag_ms": lag_ms, "tempo_ratio": tempo_ratio, "gain_db": gain_db}),
        ],
        truth={
            "material": material,
            "lag_ms": lag_ms,
            "tempo_ratio": tempo_ratio,
            "gain_db": gain_db,
            "upper_notes": [n.pitch for n in upper_notes],
            "lower_notes": [n.pitch for n in lower_notes],
        },
    )


@_cached
def concert(
    material: str = "band",
    lead_delay_ms: float = LEAD_DELAY_MS,
    jitter_ms: float = 0.0,
    room: str = "laptop",
    snr_db: float = 20.0,
    audience: int = 95,
    seed: int = 37,
) -> Scene:
    """A lead singer and four performers who each started when they heard them.

    This is the timing situation the concert feature exists to undo. The
    lead's take begins at zero. Every other performer begins at their own
    delay -- around a tenth of a second, but not the same tenth -- because
    that is when the lead's voice actually reached them. Mix the takes as
    captured and it is a flam, not a chord. The stored delays are the answer
    the pipeline has to find.

    `jitter_ms` adds a per-performer deviation on top of the documented
    delay, for sweeps that ask how much spread the estimator can take.
    """
    rng = np.random.default_rng(seed)
    parts: List[Part] = []
    delays: Dict[str, float] = {}

    if material == "chorale":
        roster = [
            (name, CHORALE_PROFILES[name], pitches)
            for name, pitches in CHORALE.items()
        ]
        spec = [
            (name, profile, pitches,
             0.0 if i == 0 else lead_delay_ms + float(rng.uniform(-18.0, 18.0)),
             0.0 if i == 0 else float(rng.uniform(-3.0, 1.0)),
             0.0 if i == 0 else float(rng.uniform(-8.0, 8.0)))
            for i, (name, profile, pitches) in enumerate(roster)
        ]
    else:
        spec = [
            (name, profile, " ".join(voice.transpose(BAND_LINE.split(), semis)),
             delay if delay == 0.0 else delay * (lead_delay_ms / LEAD_DELAY_MS),
             gain, detune)
            for name, profile, semis, delay, gain, detune in BAND
        ]

    for i, (name, profile, pitches, delay, gain, detune) in enumerate(spec):
        if jitter_ms and i > 0:
            delay += float(rng.uniform(-jitter_ms, jitter_ms))
        delay = max(delay, 0.0)
        notes = voice.phrase(pitches, 620.0, vowels=("ah", "oh", "ee"))
        take = voice.sing(
            notes,
            voice.PROFILES[profile],
            detune_cents=detune,
            gain_db=gain,
            lead_in_ms=240.0 + delay,
            seed=seed + 101 * (i + 1),
        )
        audio = add_room(take.audio, room, snr_db - i * 0.8, seed=seed * (i + 2))
        role = "lead" if i == 0 else "performer"
        parts.append(
            Part(name, role, profile, audio, take.notes,
                 {"delay_ms": round(delay, 2), "gain_db": round(gain, 2),
                  "detune_cents": round(detune, 2)})
        )
        delays[name] = round(delay, 2)

    lengths = [p.audio.size for p in parts]
    for p in parts:
        p.audio = dsp.pad_to(p.audio, max(lengths))

    return Scene(
        key=f"concert:{material}",
        title="Concert " + material,
        mode="concert",
        summary=(
            f"{len(parts)} performers, lead plus {len(parts) - 1}; heard-the-lead "
            f"delays {min(v for v in delays.values() if v):.0f}-"
            f"{max(delays.values()):.0f} ms, {audience} in the audience, "
            f"{room} at {snr_db:.0f} dB SNR"
        ),
        parts=parts,
        truth={
            "material": material,
            "lead": parts[0].name,
            "delay_ms": delays,
            "lead_delay_ms": lead_delay_ms,
            "audience": audience,
        },
    )


SCENES: Dict[str, Callable[..., Scene]] = {
    "lesson:scale": lambda **kw: lesson(material="scale", **kw),
    "lesson:phrase": lambda **kw: lesson(material="phrase", **kw),
    "lesson:descending": lambda **kw: lesson(material="descending", **kw),
    "duet:unison": lambda **kw: duet(material="unison", **kw),
    "duet:thirds": lambda **kw: duet(material="thirds", **kw),
    "duet:octaves": lambda **kw: duet(material="octaves", **kw),
    "concert:band": lambda **kw: concert(material="band", **kw),
    "concert:chorale": lambda **kw: concert(material="chorale", **kw),
}


def load(key: str, **kwargs: Any) -> Scene:
    """Build a scene by name, e.g. `load("concert:band", snr_db=14)`."""
    if key not in SCENES:
        raise KeyError(f"unknown scene {key!r}; have {sorted(SCENES)}")
    return SCENES[key](**kwargs)


def catalogue() -> List[Dict[str, Any]]:
    """Every scene, described, for the interface's scene picker."""
    return [load(key).describe() for key in SCENES]
