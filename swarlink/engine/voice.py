"""Source-filter singing synthesis.

Swarlink has to demo on one laptop with no choir present, so every fixture
voice is synthesised here rather than shipped as audio. The model is the
classical source-filter one: a glottal pulse train excites three vowel
formants. Enough of the messy parts of a real voice (vibrato that fades in
after the onset, cycle-to-cycle jitter, breath noise, legato transitions) are
included that pitch trackers, onset detectors and denoisers behave the way
they do on human takes -- a pure sine makes every algorithm downstream look
better than it is.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

SAMPLE_RATE = 22050

# Formant centres/bandwidths for a few sung vowels (Hz). Values are the usual
# soprano/alto ballpark; exact anatomy is not the point, contrast between
# vowels is.
VOWELS = {
    "ah": ((700.0, 1220.0, 2600.0), (110.0, 130.0, 180.0)),
    "eh": ((530.0, 1840.0, 2480.0), (90.0, 120.0, 170.0)),
    "ee": ((300.0, 2300.0, 3000.0), (70.0, 110.0, 170.0)),
    "oh": ((450.0, 800.0, 2600.0), (90.0, 110.0, 180.0)),
    "oo": ((320.0, 800.0, 2560.0), (70.0, 100.0, 170.0)),
}

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_to_hz(name: str) -> float:
    """'A4' -> 440.0. Accepts sharps only, which is all the fixtures need."""
    pitch = name[:-1]
    octave = int(name[-1])
    semitone = NOTE_NAMES.index(pitch)
    midi = (octave + 1) * 12 + semitone
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def cents_to_ratio(cents: float) -> float:
    return 2.0 ** (cents / 1200.0)


@dataclass
class Note:
    """One sung note in a phrase."""

    pitch: str
    duration_ms: float
    vowel: str = "ah"
    velocity: float = 1.0

    @property
    def hz(self) -> float:
        return note_to_hz(self.pitch)


@dataclass
class VoiceProfile:
    """Per-singer timbre and technique.

    `breathiness` and `jitter_cents` are what make two fixture singers
    distinguishable to the denoiser and the pitch tracker, not just louder or
    quieter versions of each other.
    """

    name: str = "voice"
    vibrato_hz: float = 5.2
    vibrato_cents: float = 28.0
    vibrato_onset_ms: float = 180.0
    jitter_cents: float = 7.0
    shimmer_db: float = 0.6
    breathiness: float = 0.012
    brightness: float = 1.0
    harmonics: int = 26
    legato_ms: float = 35.0
    attack_ms: float = 28.0
    release_ms: float = 90.0
    seed: int = 0


ALTO = VoiceProfile(
    name="alto",
    vibrato_hz=4.8,
    vibrato_cents=24.0,
    jitter_cents=6.0,
    breathiness=0.010,
    brightness=0.9,
    seed=11,
)
SOPRANO = VoiceProfile(
    name="soprano",
    vibrato_hz=5.6,
    vibrato_cents=34.0,
    jitter_cents=8.0,
    breathiness=0.014,
    brightness=1.15,
    seed=23,
)
TENOR = VoiceProfile(
    name="tenor",
    vibrato_hz=5.0,
    vibrato_cents=26.0,
    jitter_cents=7.0,
    breathiness=0.011,
    brightness=1.0,
    seed=37,
)
BARITONE = VoiceProfile(
    name="baritone",
    vibrato_hz=4.6,
    vibrato_cents=20.0,
    jitter_cents=5.5,
    breathiness=0.009,
    brightness=0.82,
    seed=53,
)
BASS = VoiceProfile(
    name="bass",
    vibrato_hz=4.3,
    vibrato_cents=18.0,
    jitter_cents=5.0,
    breathiness=0.008,
    brightness=0.72,
    seed=71,
)

PROFILES = {
    "alto": ALTO,
    "soprano": SOPRANO,
    "tenor": TENOR,
    "baritone": BARITONE,
    "bass": BASS,
}


def _one_pole_lowpass(x: np.ndarray, cutoff_hz: float, sr: int) -> np.ndarray:
    """Cheap smoothing filter, used for control signals (not audio quality)."""
    if cutoff_hz <= 0:
        return x
    dt = 1.0 / sr
    rc = 1.0 / (2.0 * np.pi * cutoff_hz)
    alpha = dt / (rc + dt)
    out = np.empty_like(x)
    acc = x[0] if x.size else 0.0
    for i, v in enumerate(x):
        acc += alpha * (v - acc)
        out[i] = acc
    return out


def _formant_response(freqs: np.ndarray, vowel: str, brightness: float) -> np.ndarray:
    """Magnitude of a 3-resonator vocal tract at the given harmonic freqs."""
    centres, bandwidths = VOWELS[vowel]
    mag = np.zeros_like(freqs)
    for idx, (fc, bw) in enumerate(zip(centres, bandwidths)):
        # Lorentzian resonance; higher formants weighted down, then pulled
        # back up by `brightness` so profiles differ in spectral tilt.
        weight = (1.0 / (idx + 1.0)) * (brightness ** idx)
        mag += weight * (bw / 2.0) ** 2 / ((freqs - fc) ** 2 + (bw / 2.0) ** 2)
    # Glottal source rolls off ~ -12 dB/octave above the fundamental.
    tilt = 1.0 / np.maximum(freqs / 180.0, 1.0) ** 1.1
    return mag * tilt


def _pitch_track(
    notes: Sequence[Note],
    sr: int,
    profile: VoiceProfile,
    rng: np.random.Generator,
    tempo_ratio: float,
    detune_cents: float,
) -> Tuple[np.ndarray, np.ndarray, List[dict]]:
    """Build sample-rate f0 and amplitude envelopes for a phrase.

    Returns (f0, amp, note_spans) where note_spans carries the ground truth
    onset/offset of every note in ms -- the fixtures' answer key.
    """
    f0_parts: List[np.ndarray] = []
    amp_parts: List[np.ndarray] = []
    spans: List[dict] = []
    cursor_ms = 0.0

    for i, note in enumerate(notes):
        dur_ms = note.duration_ms / max(tempo_ratio, 1e-6)
        n = max(int(round(sr * dur_ms / 1000.0)), 8)
        target = note.hz * cents_to_ratio(detune_cents)

        f0 = np.full(n, target, dtype=np.float64)
        if i > 0 and profile.legato_ms > 0:
            # Portamento: slide from the previous note instead of stepping,
            # which is what smears onsets for the detector.
            prev = notes[i - 1].hz * cents_to_ratio(detune_cents)
            glide = min(int(sr * profile.legato_ms / 1000.0), n // 2)
            if glide > 1:
                ramp = np.linspace(0.0, 1.0, glide) ** 1.6
                f0[:glide] = prev + (target - prev) * ramp

        # Vibrato fades in; singers rarely vibrate on the attack.
        t = np.arange(n) / sr
        onset_s = profile.vibrato_onset_ms / 1000.0
        depth = np.clip((t - onset_s) / max(onset_s, 1e-6), 0.0, 1.0)
        phase = rng.uniform(0.0, 2.0 * np.pi)
        vib_cents = profile.vibrato_cents * depth * np.sin(
            2.0 * np.pi * profile.vibrato_hz * t + phase
        )

        # Jitter: slow random walk in cents, smoothed so it is not hiss.
        walk = rng.normal(0.0, 1.0, n)
        walk = _one_pole_lowpass(walk, 12.0, sr)
        if np.max(np.abs(walk)) > 0:
            walk = walk / np.max(np.abs(walk))
        jitter_cents = profile.jitter_cents * walk

        f0 = f0 * cents_to_ratio_array(vib_cents + jitter_cents)

        # Amplitude: attack, sustain with shimmer, release.
        amp = np.ones(n)
        a = min(int(sr * profile.attack_ms / 1000.0), n // 3)
        r = min(int(sr * profile.release_ms / 1000.0), n // 3)
        if a > 1:
            amp[:a] = np.linspace(0.0, 1.0, a) ** 0.7
        if r > 1:
            amp[-r:] = np.linspace(1.0, 0.05, r) ** 1.3
        shimmer = _one_pole_lowpass(rng.normal(0.0, 1.0, n), 9.0, sr)
        if np.max(np.abs(shimmer)) > 0:
            shimmer = shimmer / np.max(np.abs(shimmer))
        amp *= 10.0 ** (profile.shimmer_db * shimmer / 20.0)
        amp *= note.velocity

        spans.append(
            {
                "index": i,
                "pitch": note.pitch,
                "hz": float(target),
                "start_ms": round(cursor_ms, 2),
                "end_ms": round(cursor_ms + dur_ms, 2),
                "vowel": note.vowel,
            }
        )
        cursor_ms += dur_ms
        f0_parts.append(f0)
        amp_parts.append(amp)

    if not f0_parts:
        return np.zeros(0), np.zeros(0), []
    return np.concatenate(f0_parts), np.concatenate(amp_parts), spans


def cents_to_ratio_array(cents: np.ndarray) -> np.ndarray:
    return 2.0 ** (cents / 1200.0)


def _vowel_spans(notes: Sequence[Note], spans: Sequence[dict], sr: int, total: int) -> List[Tuple[int, int, str]]:
    out: List[Tuple[int, int, str]] = []
    for note, span in zip(notes, spans):
        a = int(span["start_ms"] * sr / 1000.0)
        b = min(int(span["end_ms"] * sr / 1000.0), total)
        out.append((a, b, note.vowel))
    return out


@dataclass
class Take:
    """A synthesised performance plus its ground truth."""

    audio: np.ndarray
    sample_rate: int
    profile_name: str
    notes: List[dict] = field(default_factory=list)
    f0: np.ndarray = field(default_factory=lambda: np.zeros(0))

    @property
    def duration_ms(self) -> float:
        return 1000.0 * len(self.audio) / self.sample_rate


def sing(
    notes: Sequence[Note],
    profile: VoiceProfile = ALTO,
    sr: int = SAMPLE_RATE,
    tempo_ratio: float = 1.0,
    detune_cents: float = 0.0,
    gain_db: float = 0.0,
    lead_in_ms: float = 0.0,
    seed: Optional[int] = None,
) -> Take:
    """Synthesise a phrase.

    `tempo_ratio` > 1 sings faster; `detune_cents` offsets the whole take,
    which is how the lesson fixture makes a student who is consistently sharp.
    """
    rng = np.random.default_rng(profile.seed if seed is None else seed)
    f0, amp, spans = _pitch_track(notes, sr, profile, rng, tempo_ratio, detune_cents)
    if f0.size == 0:
        return Take(np.zeros(0, dtype=np.float32), sr, profile.name, [], np.zeros(0))

    n = f0.size
    # Integrate f0 to phase so frequency changes are continuous (no clicks).
    phase = 2.0 * np.pi * np.cumsum(f0) / sr
    audio = np.zeros(n, dtype=np.float64)

    vowel_regions = _vowel_spans(notes, spans, sr, n)
    harmonic_idx = np.arange(1, profile.harmonics + 1)

    for a, b, vowel in vowel_regions:
        if b <= a:
            continue
        seg_phase = phase[a:b]
        seg_f0 = f0[a:b]
        # Only keep harmonics below Nyquist to avoid aliasing.
        mean_f0 = float(np.mean(seg_f0))
        keep = harmonic_idx[(harmonic_idx * mean_f0) < (0.45 * sr)]
        if keep.size == 0:
            continue
        mags = _formant_response(keep * mean_f0, vowel, profile.brightness)
        if np.max(mags) > 0:
            mags = mags / np.max(mags)
        seg = np.zeros(b - a, dtype=np.float64)
        for h, m in zip(keep, mags):
            if m < 1e-3:
                continue
            seg += m * np.sin(h * seg_phase)
        audio[a:b] += seg

    audio *= amp

    # Breath noise, shaped by the envelope so it is not a constant hiss.
    if profile.breathiness > 0:
        noise = rng.normal(0.0, 1.0, n)
        noise = _one_pole_lowpass(noise, 4200.0, sr)
        noise -= _one_pole_lowpass(noise, 320.0, sr)
        audio += profile.breathiness * noise * np.maximum(amp, 0.0)

    peak = float(np.max(np.abs(audio))) or 1.0
    audio = audio / peak * 0.72
    audio *= 10.0 ** (gain_db / 20.0)

    if lead_in_ms > 0:
        pad = int(sr * lead_in_ms / 1000.0)
        audio = np.concatenate([np.zeros(pad), audio])
        f0 = np.concatenate([np.zeros(pad), f0])
        for s in spans:
            s["start_ms"] = round(s["start_ms"] + lead_in_ms, 2)
            s["end_ms"] = round(s["end_ms"] + lead_in_ms, 2)

    return Take(
        audio.astype(np.float32),
        sr,
        profile.name,
        spans,
        f0.astype(np.float32),
    )


def phrase(
    pitches: Iterable[str],
    duration_ms: float = 480.0,
    vowels: Optional[Sequence[str]] = None,
    velocities: Optional[Sequence[float]] = None,
) -> List[Note]:
    """Convenience builder: a list of note names at a fixed duration."""
    out: List[Note] = []
    pitches = list(pitches)
    for i, p in enumerate(pitches):
        vowel = (vowels[i % len(vowels)] if vowels else "ah")
        vel = (velocities[i % len(velocities)] if velocities else 1.0)
        out.append(Note(p, duration_ms, vowel, vel))
    return out


def transpose(pitches: Sequence[str], semitones: int) -> List[str]:
    """Move a phrase by N semitones, staying in sharp spelling."""
    out: List[str] = []
    for p in pitches:
        name, octave = p[:-1], int(p[-1])
        midi = (octave + 1) * 12 + NOTE_NAMES.index(name) + semitones
        out.append(f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}")
    return out
