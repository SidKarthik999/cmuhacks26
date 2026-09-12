#!/usr/bin/env python3
"""Runnable Person B demo: detection + cleaning + notes.

    python audio-intelligence/demo.py
    python audio-intelligence/demo.py --wav shared/tests/test.wav
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "audio-intelligence"))
sys.path.insert(0, str(ROOT / "signal-processing"))
sys.path.insert(0, str(ROOT / "shared" / "mocks"))

from audio_io import read_wav, write_wav_bytes  # noqa: E402
from cleaning import clean_pcm, clean_signal_in_store  # noqa: E402
from detection import detect_buffer  # noqa: E402
from fixtures import conversational_speech, noisy, singing_scale  # noqa: E402
from notes import extract_pitch_contour, notes_to_simple_midi, signal_to_notes  # noqa: E402
from storage import SignalStore  # noqa: E402


def summarize_detection(events):
    starts = [e for e in events if e["kind"] == "singing_started"]
    stops = [e for e in events if e["kind"] == "singing_stopped"]
    samples = [e for e in events if e["kind"] == "sample"]
    frac = (
        sum(1 for e in samples if e["is_singing"]) / len(samples) if samples else 0.0
    )
    return {
        "n_events": len(events),
        "singing_started": starts[:3],
        "singing_stopped": stops[:3],
        "sample_singing_fraction": round(frac, 3),
    }


def run_on_pcm(name: str, pcm, sr: int) -> dict:
    events = detect_buffer(pcm, sr, participant_id=name, room_id="demo")
    cleaned = clean_pcm(pcm, sr)
    notes = signal_to_notes(cleaned, sr)
    contour = extract_pitch_contour(cleaned, sr)
    voiced = [p for p in contour if p["pitch_hz"]]
    return {
        "name": name,
        "duration_s": round(len(pcm) / sr, 3),
        "sample_rate": sr,
        "detection": summarize_detection(events),
        "n_pitch_points": len(contour),
        "n_voiced_points": len(voiced),
        "notes": notes[:24],
        "note_names": [n["note_name"] for n in notes],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Person B Task 2/7/8 demo")
    parser.add_argument(
        "--wav",
        type=Path,
        default=ROOT / "shared" / "tests" / "test.wav",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "audio-intelligence" / "demo-output",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    results = []

    if args.wav.exists():
        pcm, sr = read_wav(args.wav)
        results.append(run_on_pcm("test.wav", pcm, sr))
        cleaned = clean_pcm(pcm, sr)
        (args.out / "test.cleaned.wav").write_bytes(write_wav_bytes(cleaned, sr))
    else:
        results.append({"name": "test.wav", "error": f"missing {args.wav}"})

    sr = 16000
    sung = singing_scale(sample_rate=sr)
    talk = conversational_speech(sample_rate=sr)
    sung_noisy = noisy(sung, snr_db=8.0, seed=1)
    results.append(run_on_pcm("generated_singing", sung_noisy, sr))
    results.append(run_on_pcm("generated_speech", talk, sr))
    (args.out / "generated_singing.wav").write_bytes(write_wav_bytes(sung_noisy, sr))
    (args.out / "generated_speech.wav").write_bytes(write_wav_bytes(talk, sr))
    (args.out / "generated_singing.cleaned.wav").write_bytes(
        write_wav_bytes(clean_pcm(sung_noisy, sr), sr)
    )

    notes = signal_to_notes(sung, sr)
    (args.out / "generated_singing.mid").write_bytes(notes_to_simple_midi(notes))

    # Task 7 auto-clean via Task 3 store
    with SignalStore(root=args.out / "signals") as store:
        wav = write_wav_bytes(sung_noisy, sr)
        signal = store.save(
            participant_id="demo_singer",
            room_id="demo",
            start_time=0.0,
            end_time=1000.0 * len(sung_noisy) / sr,
            sample_rate=sr,
            audio_bytes=wav,
            mode="practice",
            role="peer",
            metadata={"detected_confidence": 0.9},
        )
        cleaned_meta = clean_signal_in_store(store, signal.id)
        results.append(
            {
                "name": "store_auto_clean",
                "signal_id": signal.id,
                "cleaned": cleaned_meta["metadata"].get("cleaned"),
                "cleaned_audio_ref": cleaned_meta["metadata"].get("cleaned_audio_ref"),
            }
        )

    report = {"track": "Person B — Audio Signal Intelligence", "results": results}
    (args.out / "report.json").write_text(json.dumps(report, indent=2))
    (args.out / "report.html").write_text(_html(report))
    print(json.dumps(report, indent=2))
    print(f"\nWrote {args.out / 'report.html'}")
    return 0


def _html(report: dict) -> str:
    rows = []
    for r in report["results"]:
        names = ", ".join(r.get("note_names") or []) or "—"
        det = r.get("detection") or {}
        rows.append(
            f"<tr><td>{r.get('name')}</td>"
            f"<td>{r.get('duration_s', '—')}</td>"
            f"<td>{det.get('sample_singing_fraction', '—')}</td>"
            f"<td>{det.get('singing_started') and 'yes' or (r.get('cleaned') and 'cleaned' or 'no')}</td>"
            f"<td class='notes'>{names}</td></tr>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<title>GAPFILL is not this — Person B Task 2/7/8</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; font-family: ui-sans-serif, system-ui; background:#0b0d12; color:#e8ecf1; }}
  header {{ padding:28px 32px 12px; border-bottom:1px solid #232833; }}
  h1 {{ font-weight:560; letter-spacing:.08em; font-size:14px; text-transform:uppercase; color:#8eb4ff; margin:0 0 8px; }}
  p {{ color:#9aa3b2; max-width:720px; }}
  table {{ width:calc(100% - 64px); margin:24px 32px; border-collapse:collapse; }}
  th,td {{ text-align:left; padding:10px 12px; border-bottom:1px solid #232833; font-size:14px; }}
  th {{ color:#8b93a7; font-weight:500; }}
  .notes {{ font-variant-numeric: tabular-nums; color:#d2c194; }}
</style></head>
<body>
<header>
  <h1>Person B · Audio Signal Intelligence</h1>
  <p>Task 2 singing detection · Task 7 auto-clean · Task 8 pitch contour + notes.
  Practice mode wires these into a second enhanced feed (sync/routing stubbed).</p>
</header>
<table>
  <tr><th>Fixture</th><th>Duration (s)</th><th>Singing fraction</th><th>Started / cleaned</th><th>Notes</th></tr>
  {''.join(rows)}
</table>
</body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
