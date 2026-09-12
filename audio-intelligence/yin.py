"""YIN pitch tracker (de Cheveigné & Kawahara). Shared by Task 2 and Task 8."""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np


def yin_pitch(
    frame: np.ndarray,
    sample_rate: int,
    fmin: float = 70.0,
    fmax: float = 900.0,
    threshold: float = 0.15,
) -> Tuple[Optional[float], float]:
    """Return (pitch_hz or None, confidence in [0, 1]) for one analysis frame."""
    x = np.asarray(frame, dtype=np.float32).reshape(-1)
    w = x.size
    if w < 32:
        return None, 0.0

    tau_min = max(2, int(sample_rate / fmax))
    tau_max = min(int(sample_rate / fmin), w // 2)
    if tau_max <= tau_min + 2:
        return None, 0.0

    # Difference function via autocorrelation identity:
    # d(tau) = 2r(0) - 2r(tau) on the overlapping region, computed with
    # cumulative energy so we stay O(n log n) via np.correlate.
    corr = np.correlate(x, x, mode="full")
    r = corr[w - 1 :]
    energy = np.concatenate([[0.0], np.cumsum(x * x)])
    taus = np.arange(0, tau_max + 1)
    # remaining energy in x[0:w-tau] and x[tau:w]
    e0 = energy[w - taus] - energy[0]
    e1 = energy[w] - energy[taus]
    d = (e0 + e1 - 2.0 * r[: tau_max + 1]).astype(np.float64)
    d = np.maximum(d, 0.0)

    cmnd = np.ones_like(d)
    cum = 0.0
    for tau in range(1, d.size):
        cum += d[tau]
        cmnd[tau] = d[tau] * tau / cum if cum > 1e-12 else 1.0

    tau = _absolute_threshold(cmnd, tau_min, tau_max, threshold)
    if tau is None:
        # Fall back to global min in range; still report low confidence.
        tau = int(tau_min + np.argmin(cmnd[tau_min : tau_max + 1]))
        if cmnd[tau] > 0.45:
            return None, float(max(0.0, 1.0 - cmnd[tau]))

    tau_f = _parabolic_interpolation(cmnd, tau)
    pitch = sample_rate / tau_f if tau_f > 0 else None
    conf = float(np.clip(1.0 - cmnd[tau], 0.0, 1.0))
    if pitch is None or not (fmin <= pitch <= fmax):
        return None, conf * 0.3
    return float(pitch), conf


def _absolute_threshold(
    cmnd: np.ndarray, tau_min: int, tau_max: int, threshold: float
) -> Optional[int]:
    tau = tau_min
    while tau <= tau_max:
        if cmnd[tau] < threshold:
            while tau + 1 <= tau_max and cmnd[tau + 1] < cmnd[tau]:
                tau += 1
            return int(tau)
        tau += 1
    return None


def _parabolic_interpolation(cmnd: np.ndarray, tau: int) -> float:
    if tau <= 0 or tau >= cmnd.size - 1:
        return float(tau)
    s0, s1, s2 = float(cmnd[tau - 1]), float(cmnd[tau]), float(cmnd[tau + 1])
    denom = s0 + s2 - 2.0 * s1
    if abs(denom) < 1e-12:
        return float(tau)
    return tau + 0.5 * (s0 - s2) / denom
