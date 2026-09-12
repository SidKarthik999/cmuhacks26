from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "audio-intelligence"))
sys.path.insert(0, str(ROOT / "signal-processing"))
sys.path.insert(0, str(ROOT / "shared" / "mocks"))
sys.path.insert(0, str(ROOT / "modes"))
