import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "audio-intelligence"))
sys.path.insert(0, str(ROOT / "signal-processing"))
sys.path.insert(0, str(ROOT / "shared" / "mocks"))
sys.path.insert(0, str(ROOT / "modes"))
