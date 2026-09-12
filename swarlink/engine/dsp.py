"""Signal-processing primitives shared by the three Swarlink features.

Deliberately dependency-light: numpy only, so the engine runs on a bare
laptop with no librosa/soundfile wheel available. Anything here that has a
librosa equivalent follows its conventions (hop-based frames, mel
filterbank, chroma folding) so results are comparable.
"""
from __future__ import annotations

import io
import wave
from typing import Optional, Sequence, Tuple

import numpy as np

EPS = 1e-12


# ---------------------------------------------------------------- amplitude


def rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))


def dbfs(x: np.ndarray) -> float:
    """Full-scale RMS in dB, floored at -120 so logs never blow up."""
    return 20.0 * np.log10(max(rms(x), 1e-6))


def frame_rms(
    x: np.ndarray, sr: int = 22050, hop_ms: float = 10.0, win_ms: float = 30.0
) -> np.ndarray:
    """Short-time RMS envelope."""
    hop = max(int(sr * hop_ms / 1000.0), 1)
    win = max(int(sr * win_ms / 1000.0), hop)
    if x.size < win:
        return np.array([rms(x)])
    n = 1 + (x.size - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    return np.sqrt(np.mean(np.square(x[idx], dtype=np.float64), axis=1))


def _biquad(sig: np.ndarray, b: Sequence[float], a: Sequence[float]) -> np.ndarray:
    out = np.zeros_like(sig)
    x1 = x2 = y1 = y2 = 0.0
    for i, v in enumerate(sig):
        y = b[0] * v + b[1] * x1 + b[2] * x2 - a[1] * y1 - a[2] * y2
        out[i] = y
        x2, x1 = x1, v
        y2, y1 = y1, y
    return out


def _k_weighting(x: np.ndarray) -> np.ndarray:
    """Approximate ITU-R BS.1770 K-weighting: HF shelf then high-pass.

    Standard 48 kHz coefficients are reused at our working rate. Absolute
    LUFS compliance is never claimed; what the UI reports is the *delta*
    between two takes, which is stable under this approximation.
    """
    shelf_b = (1.53512485958697, -2.69169618940638, 1.19839281085285)
    shelf_a = (1.0, -1.69065929318241, 0.73248077421585)
    hp_b = (1.0, -2.0, 1.0)
    hp_a = (1.0, -1.99004745483398, 0.99007225036621)
    return _biquad(_biquad(x.astype(np.float64), shelf_b, shelf_a), hp_b, hp_a)


def loudness_lufs(x: np.ndarray, sr: int = 22050) -> float:
    """Gated K-weighted loudness in LUFS-like units (BS.1770-4 shape)."""
    if x.size == 0:
        return -120.0
    y = _k_weighting(x)
    block = max(int(0.4 * sr), 1)
    hop = max(block // 4, 1)
    if y.size < block:
        return float(-0.691 + 10.0 * np.log10(np.mean(y ** 2) + EPS))
    n = 1 + (y.size - block) // hop
    idx = np.arange(block)[None, :] + hop * np.arange(n)[:, None]
    powers = np.mean(y[idx] ** 2, axis=1)
    loud = -0.691 + 10.0 * np.log10(powers + EPS)
    keep = loud > -70.0  # absolute gate
    if not np.any(keep):
        return -120.0
    ref = -0.691 + 10.0 * np.log10(np.mean(powers[keep]) + EPS)
    keep &= loud > (ref - 10.0)  # relative gate
    if not np.any(keep):
        return float(ref)
    return float(-0.691 + 10.0 * np.log10(np.mean(powers[keep]) + EPS))


def normalize_to_lufs(
    x: np.ndarray, target: float = -20.0, sr: int = 22050
) -> Tuple[np.ndarray, float]:
    """Scale a take to a target loudness. Returns (audio, gain_db_applied)."""
    current = loudness_lufs(x, sr)
    if current <= -119.0:
        return x.astype(np.float32), 0.0
    gain_db = target - current
    out = x * (10.0 ** (gain_db / 20.0))
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 0.99:
        trim = 0.99 / peak
        out = out * trim
        gain_db += 20.0 * np.log10(trim)
    return out.astype(np.float32), float(gain_db)


# ------------------------------------------------------------------- frames


def stft(x: np.ndarray, n_fft: int = 1024, hop: int = 256) -> np.ndarray:
    """Complex STFT with a Hann window."""
    x = np.asarray(x, dtype=np.float64)
    if x.size < n_fft:
        x = np.pad(x, (0, n_fft - x.size))
    window = np.hanning(n_fft)
    n = 1 + (x.size - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n)[:, None]
    return np.fft.rfft(x[idx] * window, axis=1)


def istft(
    spec: np.ndarray, n_fft: int = 1024, hop: int = 256, length: Optional[int] = None
) -> np.ndarray:
    """Inverse STFT with Hann overlap-add and window-sum normalisation."""
    window = np.hanning(n_fft)
    frames = np.fft.irfft(spec, n=n_fft, axis=1) * window
    total = hop * (frames.shape[0] - 1) + n_fft
    out = np.zeros(total)
    norm = np.zeros(total)
    for i in range(frames.shape[0]):
        a = i * hop
        out[a : a + n_fft] += frames[i]
        norm[a : a + n_fft] += window ** 2
    # Floor the window sum against its own steady-state value, not against an
    # absolute epsilon.
    #
    # Only a fraction of one window covers the first and last half-window, so
    # `norm` there falls towards `w[1]**2` ~ 1e-11. Dividing by an epsilon of
    # 1e-8 multiplies those samples by up to a thousand and turns the leading
    # edge of every time-warped take into a full-scale click: on a lesson take
    # whose loudest sample was 0.52, the warped student's first samples hit
    # 0.49 where the source was at 0.02. Flooring instead lets the
    # unreconstructable edge fade in, which is what it is.
    peak = float(norm.max()) if norm.size else 0.0
    out = out / np.maximum(norm, 1e-2 * peak if peak > 0.0 else 1e-8)
    if length is not None:
        out = out[:length] if out.size >= length else np.pad(out, (0, length - out.size))
    return out.astype(np.float32)


def _hz_to_mel(f: np.ndarray | float) -> np.ndarray | float:
    return 2595.0 * np.log10(1.0 + np.asarray(f, dtype=np.float64) / 700.0)


def _mel_to_hz(m: np.ndarray) -> np.ndarray:
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


def mel_filterbank(
    sr: int, n_fft: int, n_mels: int = 40, fmin: float = 80.0, fmax: Optional[float] = None
) -> np.ndarray:
    """Triangular mel filterbank, shape (n_mels, n_fft//2 + 1)."""
    fmax = fmax or sr / 2.0
    edges = _mel_to_hz(np.linspace(_hz_to_mel(fmin), _hz_to_mel(fmax), n_mels + 2))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    fb = np.zeros((n_mels, freqs.size))
    for i in range(n_mels):
        lo, mid, hi = edges[i], edges[i + 1], edges[i + 2]
        if hi <= lo:
            continue
        rising = (freqs - lo) / max(mid - lo, 1e-6)
        falling = (hi - freqs) / max(hi - mid, 1e-6)
        fb[i] = np.clip(np.minimum(rising, falling), 0.0, None)
        area = fb[i].sum()
        if area > 0:
            fb[i] /= area
    return fb


def _peak_normalise(x: np.ndarray) -> np.ndarray:
    """Scale a take to unit peak, for analysis that should ignore level.

    The `+ eps` inside a log spectrogram is what makes this necessary. It is
    there to keep silence finite, but it also fixes an absolute reference
    level, so the same performance recorded 18 dB quieter produces a
    different log-mel shape and a different onset envelope -- and then a
    different alignment, and then a different pitch score. Normalising first
    makes every shape descriptor in this module gain-invariant, which is what
    anyone would assume they already were.
    """
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    return x / peak if peak > 0 else x


def agc(x: np.ndarray, sr: int = 22050, window_ms: float = 400.0, floor: float = 0.05) -> np.ndarray:
    """Flatten slow level drift, leaving spectral shape alone.

    Alignment features need to describe *what* is being sung, not how loud.
    Peak-normalising the whole take is not enough: a singer who swells and
    fades within the phrase still moves every log-magnitude band up and down
    together, and a distance measure between normalised log-mel frames reads
    that common shift as a change of timbre. The warp then bends to correct a
    microphone level, and once the audio has been resampled along that bent
    path it is genuinely detuned.

    Dividing by a slowly-smoothed envelope removes drift on the scale of a
    phrase while leaving note-to-note attacks, and therefore onsets, intact.
    The floor keeps silence from being amplified into noise.
    """
    if x.size == 0:
        return np.asarray(x, dtype=np.float32)
    win = max(int(sr * window_ms / 1000.0) | 1, 3)
    power = np.convolve(np.asarray(x, dtype=np.float64) ** 2, np.ones(win) / win, mode="same")
    env = np.sqrt(np.maximum(power, 0.0))
    ref = float(np.max(env))
    if ref <= 0:
        return np.asarray(x, dtype=np.float32)
    return (x / np.maximum(env, floor * ref)).astype(np.float32)


def melspectrogram(
    x: np.ndarray,
    sr: int = 22050,
    n_fft: int = 1024,
    hop: int = 256,
    n_mels: int = 40,
    normalise: bool = True,
) -> np.ndarray:
    """Log-mel spectrogram, shape (frames, n_mels)."""
    if normalise:
        x = _peak_normalise(x)
    mag = np.abs(stft(x, n_fft, hop)) ** 2
    fb = mel_filterbank(sr, n_fft, n_mels)
    return np.log(mag @ fb.T + 1e-8)


def chromagram(
    x: np.ndarray, sr: int = 22050, n_fft: int = 2048, hop: int = 512
) -> np.ndarray:
    """12-bin chroma, shape (frames, 12), each frame L2-normalised."""
    mag = np.abs(stft(x, n_fft, hop))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    with np.errstate(divide="ignore", invalid="ignore"):
        midi = 69.0 + 12.0 * np.log2(np.maximum(freqs, 1e-6) / 440.0)
    bins = np.mod(np.round(midi).astype(int), 12)
    valid = (freqs > 55.0) & (freqs < 5000.0)
    out = np.zeros((mag.shape[0], 12))
    for b in range(12):
        sel = valid & (bins == b)
        if np.any(sel):
            out[:, b] = mag[:, sel].sum(axis=1)
    norm = np.linalg.norm(out, axis=1, keepdims=True)
    return out / np.maximum(norm, 1e-8)


def onset_envelope(
    x: np.ndarray, sr: int = 22050, hop_ms: float = 5.0
) -> np.ndarray:
    """Spectral-flux onset strength, normalised to [0, 1].

    Onsets (not chroma) are what the aligner scans first: two singers in
    thirds share rhythm but not pitch content, so harmonic features mislead
    while attack times do not.
    """
    hop = max(int(sr * hop_ms / 1000.0), 1)
    n_fft = max(int(2 ** np.ceil(np.log2(hop * 8))), 256)
    mag = np.abs(stft(_peak_normalise(x), n_fft, hop))
    fb = mel_filterbank(sr, n_fft, 32)
    mel = np.log(mag ** 2 @ fb.T + 1e-8)
    flux = np.diff(mel, axis=0, prepend=mel[:1])
    env = np.maximum(flux, 0.0).sum(axis=1)
    if env.size > 3:  # remove slow drift so quiet passages still show onsets
        kernel = np.ones(9) / 9.0
        env = env - np.convolve(env, kernel, mode="same")
        env = np.maximum(env, 0.0)
    if env.size == 0:
        return env
    # Robust scaling, not peak scaling: a take that begins after silence has
    # one enormous silence-to-voice transient, and dividing by it flattens
    # every musical onset that follows -- which then wrecks cross-correlation
    # against a take that starts immediately.
    scale = float(np.quantile(env, 0.98))
    if scale <= 0:
        scale = float(np.max(env)) or 1.0
    return np.clip(env / scale, 0.0, 1.0)


def pick_attacks(
    x: np.ndarray,
    sr: int = 22050,
    hop_ms: float = 5.0,
    dip_db: float = 10.0,
    min_gap_ms: float = 130.0,
    floor_db: float = -45.0,
) -> np.ndarray:
    """Re-articulation times in milliseconds, from dips in the level envelope.

    Needed because pitch alone cannot see a repeated note. Two crotchet G4s in
    a row are one continuous G4 to a contour-based segmenter, so "G4 G4 A4 B4"
    comes back as "G4 A4 B4" -- the segmenter is not wrong about the pitch, it
    simply has no evidence of the boundary.

    Deliberately *not* built on `onset_envelope`, despite that being the
    obvious candidate. Spectral flux is an excellent alignment feature, where
    only the overall shape matters, and a poor note detector on legato
    singing, where it has no percussive attack to find: measured on a sung
    phrase, the true note boundaries scored between 0.16 and 1.00 while
    spurious peaks from vibrato and formant motion reached 0.8, so no
    threshold separates them.

    What does separate them is loudness. A singer who re-articulates a note
    releases and re-attacks, leaving a dip; one who slurs does not, and there
    genuinely is only one note. On the same phrase, real boundaries dipped
    12-15 dB while everything else stayed under 8, so the decision is a
    threshold on dip depth rather than on absolute level -- which also makes
    it independent of how loud the singer was.
    """
    env = frame_rms(x, sr=sr, hop_ms=hop_ms, win_ms=25.0)
    if env.size < 5:
        return np.zeros(0)
    db = 20.0 * np.log10(np.maximum(env, 1e-6))
    width = max(int(round(30.0 / hop_ms)) | 1, 3)
    db = np.convolve(db, np.ones(width) / width, mode="same")

    span = max(int(round(min_gap_ms / hop_ms)), 2)
    minima = np.flatnonzero((db[1:-1] <= db[:-2]) & (db[1:-1] < db[2:])) + 1
    kept: List[int] = []
    for m in minima:
        lo, hi = max(0, m - span), min(db.size, m + span + 1)
        left, right = float(db[lo : m + 1].max()), float(db[m:hi].max())
        if min(left, right) < floor_db + dip_db:
            continue  # both sides are near silence: this is not a note boundary
        if min(left, right) - float(db[m]) < dip_db:
            continue
        if kept and (m - kept[-1]) < span:
            if db[m] < db[kept[-1]]:
                kept[-1] = int(m)
            continue
        kept.append(int(m))
    return np.asarray(kept, dtype=np.float64) * hop_ms


# ------------------------------------------------------- time manipulation


def _peak_owner(m: np.ndarray) -> np.ndarray:
    """Map every frequency bin to the spectral peak that dominates it.

    A sung note is a handful of harmonic peaks, each spread over three or four
    bins by the analysis window. Those neighbouring bins are not independent
    sinusoids; their phases describe the shape of the one partial. Advancing
    each bin on its own -- the textbook phase vocoder -- lets them drift apart,
    which is the smeared, slightly chorused sound people mean by "phasiness".
    Knowing which peak owns which bin lets the whole region rotate together.
    """
    if m.size < 3:
        return np.arange(m.size)
    peaks = np.flatnonzero((m[1:-1] > m[:-2]) & (m[1:-1] >= m[2:])) + 1
    if peaks.size == 0:
        return np.arange(m.size)
    owner = np.empty(m.size, dtype=int)
    edges = (peaks[:-1] + peaks[1:] + 1) // 2
    start = 0
    for i, p in enumerate(peaks):
        end = int(edges[i]) if i < edges.size else m.size
        owner[start:end] = p
        start = end
    owner[start:] = peaks[-1]
    return owner


TRANSIENT_RATIO = 1.9


def _vocode(
    x: np.ndarray, pos: np.ndarray, n_fft: int, hop: int
) -> np.ndarray:
    """Shared phase-vocoder engine. `pos[k]` is the source frame output k reads.

    Two departures from the textbook loop, both audible:

    * identity phase locking (see `_peak_owner`), which keeps each harmonic's
      bins phase-coherent instead of letting them diffuse;
    * a phase reset on transients, because a note attack is a moment where the
      source phase is the truth and an accumulator carried over from the
      previous note is not. Without it, attacks arrive soft and doubled, and
      an onset detector picks up the ghost as an extra note.
    """
    if x.size == 0 or pos.size == 0:
        return np.zeros(0, dtype=np.float32)
    spec = stft(x, n_fft, hop)
    frames, bins = spec.shape
    mag = np.abs(spec)
    phase = np.angle(spec)
    expected = 2.0 * np.pi * hop * np.arange(bins) / n_fft

    pos = np.clip(np.asarray(pos, dtype=np.float64), 0.0, frames - 1)
    out = np.zeros((pos.size, bins), dtype=np.complex128)
    acc = phase[int(round(pos[0]))].copy()
    prev_energy = 0.0

    for k, p in enumerate(pos):
        lo = int(np.floor(p))
        hi = min(lo + 1, frames - 1)
        frac = p - lo
        m = (1.0 - frac) * mag[lo] + frac * mag[hi]
        base = phase[hi] if frac >= 0.5 else phase[lo]

        energy = float(m.sum())
        if prev_energy > 1e-9 and energy > TRANSIENT_RATIO * prev_energy:
            acc = base.copy()
        prev_energy = energy

        owner = _peak_owner(m)
        out[k] = m * np.exp(1j * (acc[owner] + base - base[owner]))

        if hi > lo:
            dphi = phase[hi] - phase[lo] - expected
            dphi = np.mod(dphi + np.pi, 2.0 * np.pi) - np.pi
        else:
            dphi = np.zeros(bins)
        acc = acc + expected + dphi
    return istft(out, n_fft, hop)


def time_stretch(x: np.ndarray, rate: float, n_fft: int = 1024, hop: int = 256) -> np.ndarray:
    """Phase-vocoder time stretch that does not transpose.

    `rate` > 1 makes the take shorter. Pitch is preserved because phase
    advance is accumulated from the measured per-bin phase difference rather
    than resampling.
    """
    if abs(rate - 1.0) < 1e-4 or x.size == 0:
        return np.asarray(x, dtype=np.float32)
    frames = stft(x, n_fft, hop).shape[0]
    if frames < 2:
        return np.asarray(x, dtype=np.float32)
    return _vocode(x, np.arange(0.0, frames - 1, rate), n_fft, hop)


def _time_stretch_unlocked(x: np.ndarray, rate: float, n_fft: int = 1024, hop: int = 256) -> np.ndarray:
    """Retained only so the checks can measure what phase locking buys."""
    if abs(rate - 1.0) < 1e-4 or x.size == 0:
        return np.asarray(x, dtype=np.float32)
    spec = stft(x, n_fft, hop)
    frames, bins = spec.shape
    mag = np.abs(spec)
    phase = np.angle(spec)
    expected = 2.0 * np.pi * hop * np.arange(bins) / n_fft

    steps = np.arange(0.0, frames - 1, rate)
    out = np.zeros((steps.size, bins), dtype=np.complex128)
    acc = phase[0].copy()
    for i, pos in enumerate(steps):
        lo = int(np.floor(pos))
        frac = pos - lo
        hi = min(lo + 1, frames - 1)
        m = (1.0 - frac) * mag[lo] + frac * mag[hi]
        dphi = phase[hi] - phase[lo] - expected
        dphi = np.mod(dphi + np.pi, 2.0 * np.pi) - np.pi
        out[i] = m * np.exp(1j * acc)
        acc = acc + expected + dphi
    return istft(out, n_fft, hop)


def time_warp(
    x: np.ndarray, src_frames: np.ndarray, n_fft: int = 1024, hop: int = 256
) -> np.ndarray:
    """Phase vocoder along an arbitrary monotone source path.

    `src_frames[k]` is the (fractional) analysis frame that output frame k
    reads from. `time_stretch` is the special case of a straight line. Doing
    the whole warp in one overlap-add pass is what keeps segment boundaries
    from turning into fake note onsets -- stitching independently stretched
    chunks does not survive an onset detector.
    """
    return _vocode(x, src_frames, n_fft, hop)


def shift_ms(x: np.ndarray, ms: float, sr: int = 22050) -> np.ndarray:
    """Delay (positive) or advance (negative) a take, keeping its length."""
    n = int(round(sr * ms / 1000.0))
    if n == 0:
        return np.asarray(x, dtype=np.float32)
    if n > 0:
        return np.concatenate([np.zeros(n, dtype=np.float32), x]).astype(np.float32)
    n = -n
    return (x[n:] if x.size > n else np.zeros(0, dtype=np.float32)).astype(np.float32)


def pad_to(x: np.ndarray, length: int) -> np.ndarray:
    if x.size >= length:
        return x[:length].astype(np.float32)
    return np.pad(x, (0, length - x.size)).astype(np.float32)


def mix_detailed(
    stems: Sequence[np.ndarray], gains_db: Optional[Sequence[float]] = None
) -> Tuple[np.ndarray, float]:
    """Sum stems at per-stem gains, guard the peak, report the guard.

    Returns (audio, trim_db), where `trim_db` is 0.0 unless the sum would have
    clipped and a broadband trim was applied to bring it back under full
    scale.

    The trim is returned rather than swallowed because it is the one thing
    about a fader bank that surprises people. Gain is applied linearly in
    amplitude, so a fader at -6 dB contributes exactly half the amplitude and
    the validation sweep asserts that to a hundredth of a dB -- but a *uniform*
    move of every fader up 6 dB does not make the mix 6 dB louder once the sum
    is already near full scale, because the guard gives some of it back. On a
    five-voice band mixed to -23 LUFS a stem, +6 dB on everything measures
    +3.1 dB out, and the missing 2.9 dB is this trim. Without it reported, the
    interface looks broken; with it reported, the interface can say so.

    The trim is broadband and applied to the sum, so it changes the level of
    the mix and not the balance within it. Relative fader moves -- the ones
    that are actually musically interesting -- are unaffected.
    """
    if not stems:
        return np.zeros(0, dtype=np.float32), 0.0
    length = max(s.size for s in stems)
    acc = np.zeros(length, dtype=np.float64)
    for i, s in enumerate(stems):
        g = 10.0 ** ((gains_db[i] if gains_db is not None else 0.0) / 20.0)
        acc += pad_to(s, length) * g
    peak = float(np.max(np.abs(acc))) if acc.size else 0.0
    trim_db = 0.0
    if peak > 0.99:
        trim = 0.99 / peak
        acc *= trim
        trim_db = 20.0 * float(np.log10(trim))
    return acc.astype(np.float32), trim_db


def mix(stems: Sequence[np.ndarray], gains_db: Optional[Sequence[float]] = None) -> np.ndarray:
    """`mix_detailed` for the callers that do not need the trim figure."""
    return mix_detailed(stems, gains_db)[0]


def correlation(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation of two equal-length-ish signals."""
    n = min(a.size, b.size)
    if n < 2:
        return 0.0
    x = np.asarray(a[:n], dtype=np.float64)
    y = np.asarray(b[:n], dtype=np.float64)
    x = x - x.mean()
    y = y - y.mean()
    denom = np.sqrt((x ** 2).sum() * (y ** 2).sum())
    return float((x * y).sum() / denom) if denom > 0 else 0.0


# ----------------------------------------------------------------- wav i/o


def to_wav_bytes(x: np.ndarray, sr: int = 22050) -> bytes:
    """16-bit mono WAV, for <audio> playback in the browser."""
    clipped = np.clip(np.asarray(x, dtype=np.float64), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def from_wav_bytes(data: bytes) -> Tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data), "rb") as w:
        sr = w.getframerate()
        frames = w.readframes(w.getnframes())
        width = w.getsampwidth()
        channels = w.getnchannels()
    dtype = {1: np.int8, 2: np.int16, 4: np.int32}[width]
    audio = np.frombuffer(frames, dtype=dtype).astype(np.float32)
    audio /= float(np.iinfo(dtype).max)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, sr


def resample(x: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Linear resample. Only used for mic input, never inside analysis."""
    if src_sr == dst_sr or x.size == 0:
        return np.asarray(x, dtype=np.float32)
    n = int(round(x.size * dst_sr / src_sr))
    src_t = np.arange(x.size)
    dst_t = np.linspace(0, x.size - 1, n)
    return np.interp(dst_t, src_t, x).astype(np.float32)


def downsample_envelope(x: np.ndarray, points: int = 900) -> list:
    """Peak envelope for waveform drawing, as plain floats for JSON."""
    if x.size == 0:
        return []
    if x.size <= points:
        return [round(float(v), 4) for v in np.abs(x)]
    edges = np.linspace(0, x.size, points + 1).astype(int)
    out = []
    for i in range(points):
        seg = x[edges[i] : edges[i + 1]]
        out.append(round(float(np.max(np.abs(seg))) if seg.size else 0.0, 4))
    return out
