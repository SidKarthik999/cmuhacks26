# Integration contracts

Human-readable restatement of cross-track schemas and the **platform ↔ audio backend** boundary.

Owner of this doc for the boundary: **Person A** (`platform/`). Schema ownership matches `ROADMAP.md` Part 4.

## Schemas (single source of truth under `shared/schemas/`)

| Schema | Owner | Consumers | Notes |
|--------|-------|-----------|-------|
| `audio_track.schema.json` | Person A | B (Task 2), C (capture) | Per-participant raw audio handle from Task 1 |
| `room_state.schema.json` | Person A | B, C | Mode + roles + Task 9 feed map |
| `singing_event.schema.json` | Person B | C | Not yet landed — B owns |
| `pitch_contour.schema.json` | Person B | C (Task 5) | Not yet landed — B owns |
| `signal.schema.json` | Person C | A, B | Not yet landed — C owns |
| `alignment_result.schema.json` | Person C | A (routing/mix), B | Not yet landed — C owns |
| `comparison_result.schema.json` | Person C | A/UI | Not yet landed — C owns |

**Change rule:** owner defines; every current consumer reviews before merge. Breaking changes bump the schema `version` field.

Typed bindings for A-owned schemas live in `shared/bindings/`. Mocks/fixtures in `shared/mocks/`.

---

## The one real boundary: `platform/` ↔ audio backend

`audio-intelligence/` and `signal-processing/` share one backend process (function-call boundary). The **only** network/language boundary is between the TypeScript platform and that backend.

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

Client-side taps use `platform/src/audio/AudioTrackTap.ts` to produce these chunks from Task 1 `MediaStreamTrack`s / `AudioTrack` descriptors.

### Backend → platform: processed results & mixes

- Same WebSocket (or a room channel). Example Performance mix push: `shared/mocks/mock_processed_result.json`.
- Clients that need a named Task 9 feed connect with `role=client&participant_id=...` and receive `feed_chunk` messages only for feeds listed in `room_state.feeds[participant_id]`.

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

LiveKit: set `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`. Without them the API returns a **mock token** and the UI uses `MockCallSession` while still exposing per-participant `AudioTrack`s for downstream work.
