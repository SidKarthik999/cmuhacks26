# Integration contracts

Human-readable restatement of cross-track schemas and the **platform ↔ audio
backend** boundary. See Part 4 of `ROADMAP.md` for the rules governing
changes to these contracts.

Owner of the platform↔backend boundary section: **Person A** (`platform/`).
Schema ownership matches `ROADMAP.md` Part 4.

## Schemas (single source of truth under `shared/schemas/`)

| Schema | Owner | Consumers | Status |
|--------|-------|-----------|--------|
| `audio_track.schema.json` | Person A | B (Task 2), C (capture) | Landed |
| `room_state.schema.json` | Person A | B, C | Landed |
| `singing_event.schema.json` | Person B | C | Landed |
| `pitch_contour.schema.json` | Person B | C (Task 5) | Landed |
| `note_event.schema.json` | Person B | A/UI | Landed |
| `signal.schema.json` | Person C | A, B | Landed |
| `alignment_result.schema.json` | Person C | A (routing/mix), B | Landed |
| `comparison_result.schema.json` | Person C | A/UI | Landed |

**Change rule:** owner defines; every current consumer reviews before merge.
Breaking changes bump the schema `version` field.

Typed bindings for A-owned schemas live in `shared/bindings/`. Mocks/fixtures
in `shared/mocks/`.

---

## The one real boundary: `platform/` ↔ audio backend

`audio-intelligence/` and `signal-processing/` share one backend process
(function-call boundary). The **only** network/language boundary is between
the TypeScript platform and that backend.

### Platform → backend: raw per-participant audio

- **Transport:** WebSocket `ws://<platform-host>/ws/audio?room_id=...&role=backend`
- **Frame shape** (also `shared/mocks/mock_pcm_chunk.json`):

```json
{
  "type": "audio_chunk",
  "room_id": "room_demo_performance",
  "participant_id": "part_lead",
  "track_id": "trk_local_alice",
  "seq": 0,
  "timestamp_ms": 0,
  "sample_rate": 48000,
  "channels": 1,
  "format": "pcm_f32",
  "pcm_base64": "<Float32 little-endian PCM as base64>"
}
```

Client-side taps use `platform/src/audio/AudioTrackTap.ts` to produce these
chunks from Task 1 `MediaStreamTrack`s / `AudioTrack` descriptors.

### Backend → platform: processed results & mixes

- Same WebSocket (or a room channel). Example Performance mix push:
  `shared/mocks/mock_processed_result.json`.
- Clients that need a named Task 9 feed connect with
  `role=client&participant_id=...` and receive `feed_chunk` messages only for
  feeds listed in `room_state.feeds[participant_id]`.

**Current status: wired end-to-end (`backend/worker.py`).** A real
out-of-process Python worker connects to `/ws/audio?room_id=...&role=processor`,
receives every `audio_chunk` for that room, and computes a real mix using
Person B's `StreamingCleaner` and Person C's `sync` module — replacing the
in-process TypeScript stubs in `modes/performance/stubs/` for any room the
worker is attached to. `platform/api/server.ts` forwards `audio_chunk` to
attached processors and falls back to the original in-process stub
orchestrator only when no processor is connected, so nothing about the
existing stub path or its tests changed. The worker replies with a
generalized `mix_chunk` message (`{type, feed, room_id, timestamp_ms,
sample_rate, format, pcm_base64, meta}`); the server publishes it on
whichever named feed (`enhanced` for Practice, `performance_mix` for
Performance) the message specifies. See
`backend/tests/test_backend_worker_integration.py` for the full end-to-end
proof (real server + real worker + real WebSocket clients, no mocks).

Performance mode's group sync in the worker (`PerformanceGroupSession`) is a
pairwise-composed stand-in for Task 6 (pick a reference performer, batch-
realign everyone else to them over a bounded trailing window each round) —
correct today, but Task 6's real streaming/dropout-reassignment
implementation should replace it once it lands.

**Fixed: streaming output chunking (audible "mismatched"/garbled mix bug).**
Both `PracticeSession._mix_with_task4` and `PerformanceGroupSession.push`
used to slice a **fixed trailing duration** off a freshly-recomputed
mix/alignment every call, instead of tracking exactly how much genuinely
new, not-yet-emitted audio was available:

- Practice mode fed `StreamingAligner.push` the **entire rolling ~4s
  buffer** every call (not just newly-arrived audio), and separately
  returned a fixed 0.25s output tail on a ~120ms input cadence — a ~52%
  overlap per chunk, heard as stutter/warble. It also fell through to a
  legacy fixed-tail mock aligner (`shared/mocks/mock_alignment.py`)
  whenever the real aligner simply didn't have enough new data *yet* for a
  given call (a normal, frequent occurrence, not an error) — that mock path
  had the identical bug, and dominated the observed distortion in practice
  (~3.9x more audio emitted than the session's actual wall-clock duration).
- Performance mode's `PerformanceGroupSession` had the mirror-image bug:
  a fixed 0.25s output tail against a variable per-`audio_chunk` cadence
  meant roughly half of every chunk larger than 0.25s was silently
  **dropped**, never emitted in any call — heard as gaps/skipping.

Both are fixed with an emit-cursor design: each session tracks the absolute
position (on the reference/aligned stream's cumulative-sample timeline)
already emitted, and only emits the genuinely new samples since then, minus
a small trailing margin held back because the DTW warp near the window's
newest edge can still be revised once more audio arrives (see
`StreamingAligner.frame_offset` / `.local_warp_path()`, added to support
this). Verified post-fix: Practice mode's emitted-audio ratio to wall-clock
duration is ~0.94; Performance mode's is ~0.79 (both correctly *under* 1.0
from the margin/warm-up latency, not over from repeats) — see
`modes/practice/tests/test_practice.py::test_emitted_audio_duration_does_not_grossly_exceed_wall_clock_duration`
and `backend/tests/test_performance_group_session.py`.

Not yet addressed: the resampling used to apply the time-correction is
naive linear interpolation on raw waveform samples (`np.interp` in
`signal-processing/sync/playback.py`), not a pitch-preserving method like a
phase vocoder or WSOLA — this can still introduce its own small
artifacts independent of the chunking fix above.

**Fixed: click/"percussion" artifact at chunk boundaries.** The duration
fix above ensured chunks don't overlap or gap in time, but each chunk still
came from an *independent* re-render (fresh DTW warp + resample) of the
current window — nothing enforced that the waveform value at the end of
chunk N matched the start of chunk N+1. Measured: the sample-to-sample jump
at a chunk boundary was ~10-11x larger than typical jumps *within* a chunk,
a real discontinuity, not a rounding artifact — heard as a periodic
click/tick (i.e. "percussion") at the chunk-emission cadence.

Fixed by holding back a short (`MIX_CROSSFADE_MS = 15`) tail of each
render's newly-stable audio *before* emitting it, then on the next call
blending that held-back, never-before-heard region against the new
render's own estimate of that same absolute time range, and only then
committing it. An earlier attempt at this fix blended against
*already-emitted* audio instead — that doesn't remove the discontinuity,
it just relocates it to right before the blended region (confirmed by the
boundary-jump ratio barely moving, 10.7x → 7.7x, with that approach).
Holding back before emitting is what actually closes the gap: 10.7x → 1.2x
(Practice), 11.1x → 1.3x (Performance). See
`modes/practice/tests/test_practice.py::test_dual_sync_chunk_boundaries_are_smooth_not_clicky`
and `backend/tests/test_performance_group_session.py::test_chunk_boundaries_are_smooth_not_clicky`.

### Task 9 feed names

| Feed | Who typically receives it |
|------|---------------------------|
| `raw_call` | Everyone in Teach; performers in Performance; everyone in Practice |
| `enhanced` | Practice peers (second feed alongside raw) |
| `performance_mix` | Performance listeners only |

---

## HTTP surface (`platform/api`)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/rooms` | Create room `{ mode, room_id? }` |
| `GET` | `/rooms/:id` | Fetch `RoomState` |
| `POST` | `/rooms/:id/join` | Join with `{ display_name, role }` → room + LiveKit token |
| `POST` | `/rooms/:id/participants/:pid/leave` | Leave |
| `GET` | `/health` | Liveness |

LiveKit: set `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`. Without
them the API returns a **mock token** and the UI uses `MockCallSession`
while still exposing per-participant `AudioTrack`s for downstream work.

---

## signal.schema.json (Task 3)

- **Owner:** Person C (`signal-processing/storage/`)
- **Consumers:** Person B (Task 7 cleaning, Task 8 notes — read/update via
  `metadata`), Person A (routing/mix, room/role lookups feed `role`/`mode`)
- **Python binding:** `signal-processing/storage/schema.py` (`Signal`
  dataclass + `validate_signal_dict`)
- **Mock fixture:** `shared/mocks/mock_signal.json`,
  `shared/mocks/mock_signal.py` (`mock_signal_dict()`, `mock_audio_bytes()`)
- **What it represents:** one extracted singing segment — who sang it, in
  which room/mode/role, its time bounds, and a reference to the stored
  lossless audio blob (`audio_ref` + `audio_format`, WAV/FLAC only — no lossy
  formats, since Task 5/8 are sensitive to compression artifacts).
- **Extension rule:** add new fields under `metadata` (e.g. Task 7 sets
  `metadata.cleaned = true`) rather than changing existing top-level fields.
  A breaking change to a top-level field bumps `version` in the schema and
  requires review from Person A and Person B before merging.

### Storage & replay API (`signal-processing/storage/store.py`)

`SignalStore` is the reference implementation of Task 3's storage + replay
outputs:

- `save(...) -> Signal` — extract-and-store: writes the audio blob to disk
  and a validated metadata row to SQLite. Called once Task 2 closes out a
  singing segment (`singing_stopped`).
- `get(signal_id) -> Signal` — fetch metadata by ID.
- `get_audio(signal_id) -> bytes` — replay: returns the raw audio bytes
  bit-faithful to what was stored, independent of whether the originating
  call/room is still active.
- `update_metadata(signal_id, **kwargs)` — merge new metadata keys (e.g.
  Task 7's `cleaned=True`) without touching the audio blob.
- `list_for_room(room_id) -> list[Signal]` — signals for a room, ordered by
  `start_time`.

Storage root layout: `<root>/signals.db` (SQLite metadata) +
`<root>/blobs/<signal_id>.<format>` (audio blobs). Both backend
processes should point at the same root if they need to share a store.

Task 7 (Person B) does **not** overwrite that blob. After
`clean_signal_in_store`, `metadata.cleaned = true` and
`metadata.cleaned_audio_ref` points at `blobs/<id>.cleaned.wav`.

## singing_event.schema.json (Task 2)

- **Owner:** Person B (`audio-intelligence/detection/`)
- **Consumers:** Person C (Task 3 uses `singing_started` / `singing_stopped`
  to open and close a Signal)
- **Python binding:** `audio-intelligence/schema.py` (`validate_singing_event`)
- **Mock fixture:** `shared/mocks/mock_singing_events.json`,
  `shared/mocks/mock_singing_events.py`
- **Live API:** `SingingDetector.push(pcm, timestamp_ms) -> list[event]`
  and `detect_buffer(pcm, sample_rate, participant_id=...)`.
- **Cadence:** `kind=sample` about every 80ms; edge events fire after ~160ms
  of sustained singing (onset within ~1s) and ~400ms of non-singing.

## pitch_contour.schema.json (Task 8)

- **Owner:** Person B (`audio-intelligence/notes/`)
- **Consumers:** Person C Task 5 (pitch deviation). **Stable signature:**
  `extract_pitch_contour(audio, sample_rate=None) -> [{time_ms, pitch_hz, confidence}]`
- **Mock fixture:** `shared/mocks/mock_pitch_contour.json`,
  `shared/mocks/mock_pitch_contour.py`
- Discrete notes (`note_event.schema.json`) are produced by `signal_to_notes`
  from the same contour.

## alignment_result.schema.json (Task 4)

- **Owner:** Person C (`signal-processing/sync/`)
- **Consumers:** Person A (routing/mix delivery), Person B (mode owners
  displaying/using synced audio)
- **Python binding:** `signal-processing/sync/dtw.py` (`AlignmentResult`
  dataclass)
- **Mock fixture:** `shared/mocks/mock_alignment_result.json`
- **What it represents:** a full DTW warp path (not a constant offset —
  resolved design decision, needed to track tempo drift throughout a
  performance) between a reference Signal and another Signal, plus a
  confidence score and the time resolution (`hop_length_ms`) of the path's
  frame indices.
- **Extension rule:** same as `signal.schema.json` above — top-level field
  changes are breaking and require review from A and B.

### Sync API (`signal-processing/sync/`)

- `align_signals(signal_ref, signal_other, store, confidence_threshold=0.5) -> AlignmentResult` —
  batch entry point: fetches both Signals' audio via a `SignalStore` and
  runs DTW over chroma features. Use this for Teach mode's grading flow.
  **Fallback:** DTW assumes both signals share melodic/harmonic content
  (the same melody, sung together or echoed back). If confidence comes back
  below `confidence_threshold`, that means content-matching found nothing
  reliable to lock onto (e.g. the two performers are singing/playing
  different material) — in that case this returns a `method="timestamp_offset"`
  result instead: a constant offset from each Signal's captured `start_time`
  on the shared room clock (Task 1), not a fabricated warp path. Callers
  that need to know which basis was used should check `result.method`.
- `align_audio(audio_ref_bytes, audio_other_bytes) -> (warp_path, confidence, hop_length_ms)`
  — lower-level function operating on raw audio, no Signal/store dependency.
- `StreamingAligner` (`signal-processing/sync/streaming.py`) — the
  incremental/windowed variant required for Practice/Performance's <1-2s
  latency budget. Call `.push(chunk_ref_bytes, chunk_other_bytes)` as audio
  arrives; it recomputes DTW over a bounded trailing window (not the full
  history) so cost stays roughly constant per update, and returns an
  `AlignmentResult` with `streaming=True` once both streams have enough
  audio for at least one chroma frame. Pass `clock_offset_ms` (the two
  participants' room-clock start-time difference) at construction to get
  the same low-confidence fallback as `align_signals` above.
- `render_aligned_playback(audio_ref, audio_other, warp_path, hop_length_ms) -> AlignedPlayback`
  (`signal-processing/sync/playback.py`) — the "aligned playback" output:
  time-warps the other signal onto the reference's timeline and returns
  WAV bytes for the reference, the synced other track, and a mixed-down
  combination of both.

Confidence is a heuristic derived from average per-step DTW cost (cosine
distance over chroma), not a calibrated probability — use it to flag poor
alignments, not as a statistical guarantee.

## comparison_result.schema.json (Task 5)

- **Owner:** Person C (`signal-processing/compare/`)
- **Consumers:** Person A/UI, Teach mode's grading flow (whenever it lands)
- **Python binding:** `signal-processing/compare/compare.py`
  (`ComparisonResult` dataclass)
- **Mock fixture:** `shared/mocks/mock_comparison_result.json`
- **What it represents:** pitch/timing deviation between two Signals that
  have already been aligned by Task 4, plus the resolved
  `0.7 * pitch_accuracy + 0.3 * timing_accuracy` score. Reuses Person B's
  `extract_pitch_contour`/`notes_from_contour` (Task 8) as the shared
  pitch-tracking building block, per ROADMAP.md's cross-cutting note.
- **Confidence-gated content check:** if the Task 4 `AlignmentResult` used
  the `timestamp_offset` fallback (the two signals don't share melodic
  content), per-note pitch/timing comparison isn't meaningful — there's no
  real correspondence to measure deviation against. `compare_signals` in
  that case returns `content_matched: false` with every deviation/accuracy/
  score field `null`, rather than fabricating a number.
- **Normalization curves** (70/30 weighting is fixed by ROADMAP.md; these
  are the implementation-detail constants): `pitch_accuracy = clamp(100 -
  mean_abs_pitch_deviation_cents, 0, 100)` (a full semitone, 100 cents,
  zeroes the score); `timing_accuracy = clamp(100 -
  mean_abs_timing_deviation_ms / 2, 0, 100)` (200ms of onset drift zeroes
  the score).
- **Timing deviation basis:** per-note, not per-frame — extracts note
  onsets from both signals' pitch contours (Task 8's `notes_from_contour`),
  maps each reference onset through the Task 4 warp path to predict where
  it should land in the other signal's timeline, and compares that
  prediction against the other signal's actual nearest onset (within a
  300ms match tolerance, else skipped rather than guessed).
- **Verified against ROADMAP.md's acceptance criteria:** a known
  intentional pitch error is surfaced accurately (a 50-cent shift is
  detected as such); a correct-pitch/rushed-timing case scores noticeably
  higher than a wrong-pitch/correct-timing case (see
  `signal-processing/tests/test_compare.py`).

Person B assumes float32 mono PCM chunks keyed by `participant_id` +
`room_id` (`shared/mocks/mock_audio_track.py`) as a Python-side stand-in for
`audio_track.schema.json` until the real per-track handoff (via the platform
boundary above) is wired end-to-end. Practice mode delivers the enhanced
feed via `shared/mocks/mock_routing.py` (`EnhancedFeedRouter`) as a stand-in
for Person A's real `MultiFeedRouter` (`platform/src/feedRouter.ts`) — see
"Known integration gaps" below.

## Known integration gaps (found comparing all three tracks post-merge)

These are places where each track's *tests* pass in isolation, but the
tracks weren't actually wired to each other. (1) and (2) are now **closed**
by `backend/worker.py` and `platform/api/server.ts`'s new `role=processor`
path — see "Current status" above and
`backend/tests/test_backend_worker_integration.py` for the real end-to-end
proof. (3) is still open.

1. ~~**Practice mode's routing is disconnected from Task 9.**~~ **Closed.**
   `backend/worker.py` connects as `role=processor`, runs `PracticeSession`
   for real, and publishes the resulting `enhanced` feed through
   `platform/src/feedRouter.ts` via a `mix_chunk` message — the real router,
   not `mock_routing.EnhancedFeedRouter`.
2. ~~**Performance mode's Task 6/7 stubs are TypeScript, not the real Python
   implementations.**~~ **Closed** for rooms with a worker attached: the
   worker runs real `StreamingCleaner` (Task 7) and a pairwise-composed
   real sync (`PerformanceGroupSession`, standing in for Task 6) and
   publishes `performance_mix` through the real router. Rooms with no
   worker attached still fall back to `modes/performance/stubs/` unchanged.
3. **`audio_track.schema.json` (Person A) isn't consumed as-is by Person B.**
   `SingingDetector`/`StreamingCleaner` take raw PCM arrays directly; the
   schema's `track_id`/`format`/`is_remote` metadata only exists on the
   platform side and isn't threaded through the WebSocket `audio_chunk`
   message into the Python functions' inputs. Not broken, just not
   round-tripped. Still open.
