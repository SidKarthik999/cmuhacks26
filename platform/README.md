# Platform (Person A)

SFU-backed multi-party call infrastructure, mode/role state, multi-feed routing (Task 9), and Performance mode integration.

## Quick start

```bash
# from repo root
npm install
npm run test
npm run api          # http://localhost:8787
npm run dev          # Vite UI → http://localhost:5173
```

Optional LiveKit (real A/V):

```bash
export LIVEKIT_URL=wss://your-project.livekit.cloud
export LIVEKIT_API_KEY=...
export LIVEKIT_API_SECRET=...
```

Without LiveKit credentials, the UI runs a **mock call session** that still satisfies Task 1’s contract: individually addressable per-participant audio tracks.

## Layout

- `src/` — roles, room store, feed router, call abstraction, UI
- `api/` — HTTP + WebSocket boundary to the audio backend
- `tests/` — unit + API tests
- `../modes/performance/` — Performance mode orchestration (Person A)
- `../shared/schemas/` — `room_state`, `audio_track` (Person A owned)
