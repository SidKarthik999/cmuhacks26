"""Stub for Person C Task 4 (pairwise sync) until DTW lands.

Practice mode (Person B) consumes `sync()` / `sync_streaming()`. The stub
returns a constant-offset alignment so the dual-stream mix can be wired now.
Swap this module for signal-processing/sync without changing Practice code
if the function names stay the same.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np


def sync(
    pcm_a: np.ndarray,
    pcm_b: np.ndarray,
    sample_rate: int,
) -> Dict[str, Any]:
    """Batch stub: lag via energy-normalized cross-correlation peak."""
    a = np.asarray(pcm_a, dtype=np.float32).reshape(-1)
    b = np.asarray(pcm_b, dtype=np.float32).reshape(-1)
    if a.size == 0 or b.size == 0:
        return {
            "warp_path": [],
            "offset_ms": 0.0,
            "confidence": 0.0,
            "stub": True,
        }
    n = min(a.size, b.size, sample_rate * 2)
    a = a[:n] - a[:n].mean()
    b = b[:n] - b[:n].mean()
    corr = np.correlate(a, b, mode="full")
    lag = int(np.argmax(corr) - (n - 1))
    peak = float(np.max(np.abs(corr)) + 1e-9)
    energy = float(np.linalg.norm(a) * np.linalg.norm(b) + 1e-9)
    offset_ms = 1000.0 * lag / sample_rate
    return {
        "warp_path": [[0, max(0, lag)], [n, n + max(0, lag)]],
        "offset_ms": offset_ms,
        "confidence": float(min(1.0, peak / energy)),
        "stub": True,
    }


def sync_streaming(
    pcm_a: np.ndarray,
    pcm_b: np.ndarray,
    sample_rate: int,
    prev: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Streaming stub: same as batch on the current rolling buffers."""
    result = sync(pcm_a, pcm_b, sample_rate)
    if prev is not None:
        result["offset_ms"] = 0.7 * prev.get("offset_ms", 0.0) + 0.3 * result["offset_ms"]
    return result


def apply_offset(pcm: np.ndarray, offset_ms: float, sample_rate: int) -> np.ndarray:
    """Shift `pcm` by offset_ms (positive = delay this stream)."""
    shift = int(round(offset_ms * sample_rate / 1000.0))
    x = np.asarray(pcm, dtype=np.float32).reshape(-1)
    if shift == 0:
        return x
    if shift > 0:
        return np.concatenate([np.zeros(shift, dtype=np.float32), x])
    return x[-shift:]
