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
| `comparison_result.schema.json` | Person C | A/UI | Not yet landed — Task 5 |

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

**Current status:** `platform/api/server.ts` implements this boundary and a
TypeScript-side stand-in for the backend logic inside
`modes/performance/performanceMode.ts` (using the stubs below), so
Performance mode runs end-to-end today without the Python backend attached.
Wiring an actual Python process to connect as `role=backend`, run the real
Task 2/6/7 implementations, and push real `performance_mix_chunk`/
`feed_chunk` results back is tracked as the next piece of "framework" work —
see the note in `modes/performance/stubs/` below.

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

## Pending schemas (stubbed, not yet implemented)

`comparison_result.schema.json` (Task 5, Person C) is not defined yet. Add
it here once it lands.

Person B assumes float32 mono PCM chunks keyed by `participant_id` +
`room_id` (`shared/mocks/mock_audio_track.py`) as a Python-side stand-in for
`audio_track.schema.json` until the real per-track handoff (via the platform
boundary above) is wired end-to-end. Practice mode delivers the enhanced
feed via `shared/mocks/mock_routing.py` (`EnhancedFeedRouter`) as a stand-in
for Person A's real `MultiFeedRouter` (`platform/src/feedRouter.ts`) — see
"Known integration gaps" below.

## Known integration gaps (found comparing all three tracks post-merge)

These are places where each track's *tests* pass in isolation, but the
tracks aren't actually wired to each other yet — tracked here so they don't
get lost, and covered by the new tests in `modes/*/tests/test_integration.py`
where feasible:

1. **Practice mode's routing is disconnected from Task 9.** Person B's
   `modes/practice/pipeline.py` pushes the enhanced feed into
   `shared/mocks/mock_routing.EnhancedFeedRouter` (an in-process Python stub),
   not Person A's real `platform/src/feedRouter.ts`. Since one is Python and
   one is TypeScript, connecting them for real requires the Python side to
   run as a `role=backend` WebSocket client against `platform/api/server.ts`
   and publish frames there — that hasn't been built yet.
2. **Performance mode's Task 6/7 stubs are TypeScript, not the real Python
   implementations.** `modes/performance/stubs/groupSync.ts` and
   `streamingClean.ts` are identity/no-op stand-ins run in-process by
   `PerformanceOrchestrator`. The real `signal-processing/sync` (Task 4, and
   Task 6 once it lands) and `audio-intelligence/cleaning` (Task 7) only run
   on the Python side today, so Performance mode doesn't yet call them at
   all — it needs the same WebSocket backend connection as (1).
3. **`audio_track.schema.json` (Person A) isn't consumed as-is by Person B.**
   `SingingDetector`/`StreamingCleaner` take raw PCM arrays directly; the
   schema's `track_id`/`format`/`is_remote` metadata only exists on the
   platform side and isn't threaded through the WebSocket `audio_chunk`
   message into the Python functions' inputs. Not broken, just not
   round-tripped — worth checking when the backend WS client gets built.
