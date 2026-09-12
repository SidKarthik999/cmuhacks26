# Integration contracts

Human-readable restatement of the schemas in `shared/schemas/`. See Part 4 of
`ROADMAP.md` for the rules governing changes to these contracts.

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

## Pending schemas (stubbed, not yet implemented)

The remaining schemas listed in Part 4's repo layout
(`singing_event.schema.json` — Person B, `pitch_contour.schema.json` —
Person B, `alignment_result.schema.json` — Person C, Task 4/6,
`comparison_result.schema.json` — Person C, Task 5, `room_state.schema.json`
and `audio_track.schema.json` — Person A) are not defined yet. Add them here
as their owning tasks land.
