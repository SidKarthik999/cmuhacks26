"""Task 9 stub — Person A owns the real multi-feed router.

Practice mode imports EnhancedFeedRouter from shared/mocks/mock_routing.py.
This module re-exports it so platform/api can replace the stub in place.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "mocks"))

from mock_routing import EnhancedFeedRouter, EnhancedFrame  # noqa: E402

__all__ = ["EnhancedFeedRouter", "EnhancedFrame"]
