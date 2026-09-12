# Roadmap: Virtual Musical Collaboration Platform

## Mission

Enable musicians to collaborate remotely as if they were in the same room — starting from live video conversation, and building up the signal-processing pipeline needed to capture, clean, compare, sync, and visualize sung/played performances.

This document is the shared source of truth for implementation. Each task below is written to be handed to an LLM (or a human engineer) independently, with enough context to implement without re-deriving the whole project. **Do not skip ahead** — later tasks assume the interfaces defined by earlier tasks exist, even if the internals change.

## How to use this doc

- Tasks are ordered by priority — build top to bottom.
- Each task defines: **Goal**, **Inputs**, **Outputs**, **Depends on**, **Suggested approach**, **Acceptance criteria**.
- "Outputs" are meant to be stable interfaces — if you're implementing a later task, treat the previous task's Outputs as your contract, not an implementation detail.
- When a task's design changes in a way that breaks its Outputs contract, update this doc in the same PR.
- Open questions / unresolved design decisions are called out inline as `> **Open question:**` — resolve and remove these as decisions are made.

---

## Task 1 — Video conversation platform

**Goal:** A working video/audio call between 2+ participants, in-browser, with per-participant audio tracks accessible for downstream processing.

**Inputs:** N/A (entry point).

**Outputs:**
- A room/session abstraction (join via room ID or link).
- Per-participant raw audio track (accessible as a stream/buffer, not just rendered to speakers) — this is what Task 2 consumes.
- Per-participant raw video track (rendered, not necessarily processed further right now).

**Depends on:** Nothing.

**Suggested approach:**
- WebRTC (via a managed SFU like LiveKit, Daily, or Twilio Video) for low-latency multi-party audio/video — rolling your own SFU is out of scope.
- Keep a clean abstraction boundary between "call infrastructure" and "audio processing" — Task 2 should be able to tap an audio stream without knowing about signaling/room logic.

**Acceptance criteria:**
- 2+ people can join a room and see/hear each other with acceptable latency (<300ms audio).
- Each participant's audio stream is individually addressable (not just a mixed room feed) — critical, since later tasks need per-person signals.
- Room supports up to ~25 concurrent participants (resolved) — sized for Performance mode's audience (a handful of performers + a real small-audience listener count), not just point-to-point calls. This rules out a pure mesh WebRTC topology; use an SFU (LiveKit, Daily, etc.) that scales to this size from the start.

---

## Task 2 — Singing detection

**Goal:** Given a live per-participant audio stream, detect whether the person is currently singing (vs. talking, silent, or background noise).

**Inputs:** Per-participant raw audio track (Task 1 output).

**Outputs:**
- A boolean/confidence stream: `is_singing(timestamp) -> {is_singing: bool, confidence: float}`, updated at a regular interval (e.g. every 100–250ms).
- Ideally exposed as an event stream (`singing_started`, `singing_stopped`) so downstream consumers don't have to poll.

**Depends on:** Task 1 (needs raw audio access).

**Suggested approach:**
- Start with pitch-salience + harmonicity heuristics (singing has stable, sustained pitch vs. speech's more erratic pitch contour) as a fast baseline.
- If heuristics aren't accurate enough, use a lightweight classifier (e.g. a small CNN on mel-spectrograms) trained/fine-tuned to distinguish singing vs. speech vs. silence.
- Run detection client-side or in a low-latency edge worker to avoid adding round-trip lag before Task 3 can start capturing.

**Acceptance criteria:**
- Correctly flags sustained singing within ~1s of onset.
- Low false-positive rate on normal conversational speech.

---

## Task 3 — Signal extraction, storage, and replay

**Goal:** When singing is detected, extract the audio as a discrete, storable, replayable "musical signal" — the core data object every later task operates on.

**Inputs:** Raw audio track + singing on/off events (Task 2 output).

**Outputs:**
- A **Signal** object/schema — this is the central data type for the rest of the roadmap. Suggested shape:
  ```
  Signal {
    id: string
    participant_id: string
    room_id: string
    start_time: timestamp
    end_time: timestamp
    sample_rate: int
    audio: raw or encoded waveform (e.g. WAV/FLAC blob, or reference to stored file)
    metadata: { detected_confidence, ... }
  }
  ```
- Storage layer: persist Signals (blob storage for audio + DB row for metadata).
- Replay API: fetch a Signal by ID and stream/play it back.

**Depends on:** Task 2 (needs singing boundaries to know what to extract).

**Suggested approach:**
- Buffer audio continuously in a rolling window; on `singing_started`, mark the start offset; on `singing_stopped`, close out the Signal and flush to storage.
- Store lossless or near-lossless (WAV/FLAC) — downstream cleaning/note-conversion tasks will be sensitive to compression artifacts.

**Acceptance criteria:**
- A singing segment can be extracted, stored, and replayed back bit-faithfully (or within acceptable lossy tolerance) on demand, independent of the live call still being active.

---

## Task 4 — Sync two musical signals

**Goal:** Given two Signals (e.g. from two different participants, or two takes), align them in time so they can be meaningfully compared or played together.

**Inputs:** Two Signal objects (Task 3 output).

**Outputs:**
- A full alignment mapping, not just a constant offset: `sync(signal_a, signal_b) -> { warp_path, confidence }` (resolved — see below).
- An "aligned playback" capability — both signals can be played together with the computed alignment applied.

**Depends on:** Task 3 (needs stored Signals to operate on).

**Suggested approach:**
- **Resolved: full tempo-drift handling is required, not just a constant offset.** Singers (e.g. a student drifting off the teacher's tempo) can speed up/slow down independently mid-performance, so use dynamic time warping (DTW) over chroma/pitch features as the primary method, not a fallback.
- A cross-correlation constant-offset pass can still be used as a fast initial estimate to seed/bound the DTW search, but DTW's warp path is the actual output this task must produce.
- Given the <1–2s streaming latency requirement (Practice/Performance modes — see cross-cutting notes), plan for an incremental/windowed DTW variant that can update the warp path on a rolling buffer rather than only running on a complete closed Signal.

**Acceptance criteria:**
- Two signals of the same performance (e.g. recorded slightly out of sync due to network latency) play back audibly in sync after alignment.
- A performance where one signal gradually drifts tempo relative to the other still stays audibly aligned throughout (not just at the start).

---

## Task 5 — Compare difference between two musical signals

**Goal:** Given two synced Signals, quantify how different they are — e.g. pitch deviation, timing deviation, dynamics — useful for feedback like "you're slightly sharp" or "you're rushing the beat."

**Inputs:** Two Signals + their alignment (Task 4 output).

**Outputs:**
- A difference report, e.g.:
  ```
  Comparison {
    pitch_deviation_cents: time-series or summary stats
    timing_deviation_ms: time-series or summary stats
    overall_similarity_score: float
  }
  ```

**Depends on:** Task 4 (comparison only makes sense on aligned signals).

**Suggested approach:**
- Extract pitch contours (see Task 7's pitch-tracking work — likely shares code) for both signals, then diff in cents.
- Use aligned onset times to compute per-note or per-beat timing deviation.
- **Resolved — scoring formula for Teach mode's 0–100% score:** `score = 0.7 * pitch_accuracy + 0.3 * timing_accuracy`, where each sub-score is itself normalized to 0–100% (e.g. `pitch_accuracy = clamp(100 - avg_abs_pitch_deviation_cents / k, 0, 100)` for some tuned constant `k`, and similarly for timing deviation in ms). Pick and document the exact normalization constants during implementation — the 70/30 weighting is fixed, the per-metric normalization curve is an implementation detail.

**Acceptance criteria:**
- Given two takes of the same melody with a known intentional pitch error, the comparison surfaces that deviation accurately.
- The 70/30 pitch/timing-weighted score produces intuitive results on a hand-checked test case (e.g. correct pitch + rushed timing scores noticeably higher than wrong pitch + correct timing).

---

## Task 6 — Sync multiple (<5) signals

**Goal:** Generalize Task 4's pairwise sync to a small group (2–4 signals) so a full ensemble can be aligned and played back together.

**Inputs:** 2–4 Signal objects.

**Outputs:**
- A group alignment: `sync_group(signals[]) -> { signal_id: offset_ms }[]` relative to a common reference point.
- Group playback capability.

**Depends on:** Task 4 (reuses/extends pairwise sync logic).

**Suggested approach:**
- Pick a reference signal (e.g. longest, or earliest start) and pairwise-sync every other signal to it using Task 4's method — avoid building a separate N-way algorithm unless pairwise composition proves inaccurate.

**Acceptance criteria:**
- 3+ signals of the same performance play back audibly in sync as a group.

---

## Task 7 — Signal cleaning (background noise removal)

**Goal:** Remove background noise from a Signal so that comparison (Task 5) and note conversion (Task 8) operate on a clean source.

**Inputs:** A raw Signal (Task 3 output).

**Outputs:**
- A cleaned Signal (same schema as Task 3's Signal, with `metadata.cleaned = true`).

**Depends on:** Task 3 (needs a stored Signal to clean). Can run independently of Tasks 4–6.

**Suggested approach:**
- Start with a spectral-subtraction or noise-gate baseline.
- If quality needs are higher, use a pretrained speech/music denoising model (e.g. RNNoise, Demucs for source separation if isolating a voice from a mixed recording).
- **Resolved: cleaning runs automatically on every extracted Signal**, not on-demand — confirmed by Practice mode needing a cleaned feed even for a lone singer with no sync partner yet (see Part 2). Wire this as a default step in the Task 3 pipeline (every Signal gets cleaned immediately after extraction) rather than a separately-invoked step.
- Given the <1–2s latency requirement for Practice/Performance, this needs a streaming-capable variant (see cross-cutting notes) — it can't only run as an offline post-process on a fully closed Signal.

**Acceptance criteria:**
- Audibly reduced background noise with minimal loss of the target voice/instrument's harmonic content (verify pitch-tracking accuracy isn't degraded by the cleaning step).
- Cleaning runs automatically and completes within the latency budget needed by whichever mode triggered it (immediate/streaming for Practice/Performance; can be marginally slower for Teach mode's per-turn batch case).

---

## Task 8 — Convert signal to displayable notes

**Goal:** Convert a (cleaned) Signal into discrete musical notes (pitch + duration + timing) suitable for visual display (e.g. sheet music, piano roll, or simple note-name overlay).

**Inputs:** A cleaned Signal (Task 7 output preferred; raw Signal acceptable as fallback).

**Outputs:**
- A note sequence:
  ```
  NoteEvent { pitch: (note_name or MIDI number), start_ms, duration_ms, velocity/confidence }[]
  ```
- Ideally exportable as MIDI and/or a simple JSON the frontend can render directly.

**Depends on:** Task 7 (cleaning improves accuracy) and Task 3 (Signal schema).

**Suggested approach:**
- Pitch detection: YIN/pYIN or CREPE for monophonic pitch tracking (fine for solo singing).
- Segment continuous pitch into discrete notes via onset detection + pitch quantization to nearest semitone.
- Reuse this pitch-tracking code as the shared building block for Task 5's pitch-deviation comparison too — don't implement pitch tracking twice.

**Acceptance criteria:**
- A simple monophonic melody (e.g. a sung scale) converts to the correct sequence of note names with reasonable timing.

---

## Cross-cutting notes for implementers

- **Shared building block:** pitch-tracking (used by Task 5 and Task 8) should be implemented once and shared, not duplicated.
- **Signal schema (Task 3) is the backbone.** Tasks 4–8 all consume/produce Signals or things derived from Signals. Keep the schema stable and versioned; if a task needs to extend it, add fields rather than changing existing ones.
- **Latency boundary — REVISED.** Tasks 1–2 are live/real-time-constrained, as originally scoped. However, **Practice mode and Performance mode also require near-real-time output (<1–2s end-to-end)** from the clean → sync → mix chain (Tasks 7, 6/4) — see "Part 2: Modes" below. This means Tasks 4, 6, and 7 cannot be purely batch/offline implementations operating only on fully-closed stored Signals; each needs a **streaming-capable variant** that can clean/sync/mix audio incrementally as it arrives, in addition to whatever offline/batch use they get in Teach mode. Design these tasks' interfaces to support both a batch call (operate on a complete stored Signal) and a streaming call (operate on a rolling buffer) from the start, rather than retrofitting streaming later.
- **Signal attribution.** Every Signal (Task 3) must carry `participant_id` and the room's current mode/role assignment at capture time, since downstream mode logic (Part 2) branches on "who sang" and "what role are they."

---

# Part 2: Modes (composition layer)

The 8 tasks above are building blocks. A meeting is created with exactly one **mode** selected up front, and mode determines which roles exist, how Tasks 1–8 wire together, and what each participant sees/hears. This section is the integration spec — implement it after the task-level building blocks exist, wiring them together rather than re-implementing their logic.

## Mode selection & roles (extends Task 1)

- At meeting creation, the creator picks one of: `teach`, `practice`, `performance`. This is stored on the room alongside the Task 1 room/session object.
- Role assignment is mode-specific and must be set before/at join time:
  - **Teach:** exactly one `teacher`, exactly one `student` (v1 scope — multi-student sessions are a future extension, not in scope now).
  - **Practice:** exactly two participants, both peers (no distinguished roles).
  - **Performance:** 2–4 participants flagged `performer` (one of whom is additionally flagged `lead`), plus an arbitrary number of participants flagged `listener`.
- Task 2's singing detection must attribute each singing event to a `participant_id`, and Part 2 logic below looks up that participant's role to decide what happens next.

## Teach mode

**Participants:** 1 teacher + 1 student.

**State:** the room keeps one piece of state — `current_reference: Signal | null` — the most recent thing the teacher sang.

**Flow:**
1. Someone sings → Task 2 detects it, Task 3 extracts/stores the Signal, attributed to teacher or student via `participant_id`.
2. **If the teacher sang:**
   - Run Task 7 (clean) → Task 8 (convert to notes) on the Signal.
   - Display the resulting notes on *both* participants' screens.
   - Set `current_reference` to this Signal (overwrites whatever was there before).
   - No grading happens for the teacher's own singing.
3. **If the student sang:**
   - Run Task 7 → Task 8 and display the student's own notes on both screens (same as teacher's case) — this always happens regardless of what follows.
   - **If `current_reference` is null** (student sang before the teacher ever has, this session): stop here — notes are shown, but there is nothing to grade against yet.
   - **If `current_reference` is set:** additionally run Task 4 (sync student's Signal to `current_reference`) → Task 5 (compare, using the resolved 70/30 pitch/timing-weighted formula above) → display the resulting percentage to both participants.
4. **Resolved — overlapping turns (teacher and student singing simultaneously):** do not ignore or reject overlap. Instead, treat it like Practice mode's mesh: clean both streams (Task 7) and sync them (Task 4, streaming), but with the **teacher's voice prioritized** in the resulting mix/reference — i.e., the teacher's Signal is what updates `current_reference` (and is what gets displayed as "the" notes), while the student's overlapping Signal is still captured and graded against that same reference, exactly as in the non-overlapping case. In effect, overlap doesn't create a new code path — it's the same flow as steps 2–3 above, just with both Signals' extraction/cleaning/sync happening concurrently instead of sequentially, and the teacher's Signal winning any conflict over what becomes the reference.

> **Open question:** none remaining for Teach mode's core flow. Note that this overlap-handling shares its clean+sync machinery with Practice mode — implement it once, reuse in both places rather than duplicating.

## Practice mode

**Participants:** exactly 2, both peers, no roles beyond that.

**Flow:**
1. Both participants' audio continues over the normal Task 1 call (unprocessed) as today — Practice mode adds an *additional* enhanced feed, it doesn't replace the base call.
2. As each participant sings, Task 2 detects it and Signals are captured (Task 3) **in streaming form** — this mode needs the streaming-capable variants noted above, not just the batch/stored-Signal path, since the point is a live meshed monitor.
3. Each singer's stream is cleaned (Task 7, streaming) and the two streams are synced (Task 4, streaming) continuously while both are singing.
4. The cleaned + synced mix is delivered back to both participants as a second audio feed (in addition to the raw call) within the <1–2s latency target, so they can hear a clearer blend of themselves together than the raw call alone provides.

**Resolved — single-singer case:** when only one of the two is singing, Practice mode still runs Task 7 (cleaning) on that solo stream and delivers the cleaned solo feed — it does not fall back to raw pass-through. Task 4 (sync) simply doesn't run yet since there's nothing to sync against. Once the second participant starts singing, sync kicks in and the feed becomes the full cleaned+synced mesh. This matches Task 7's resolved "always clean automatically" behavior above — there's no separate on/off switch for cleaning based on how many people are singing.

## Performance mode

**Participants:** 2–4 `performer`s (one flagged `lead`) + any number of `listener`s.

**Flow:**
1. Performers hear each other over the normal, unprocessed Task 1 call audio (low latency, direct — this is what lets them stay together while singing, same as any live ensemble call).
2. Each performer's singing is captured as a streaming Signal (Task 2 + Task 3, streaming) and cleaned (Task 7, streaming).
3. All performer streams are synced as a group (Task 6, streaming variant), using the `lead`'s stream as the sync anchor/reference point (confirmed: the lead has no other special treatment — not emphasized in the mix, just the alignment reference).
4. The cleaned, group-synced mix is delivered — within the <1–2s latency target — as a *separate* feed to `listener` participants. Listeners do **not** hear the raw/unprocessed performer call audio; they only get the processed mix.
5. Performers' own screens can also display Task 8's note output for their own singing if desired (reuses the Teach-mode display path) — not a hard requirement, but a natural reuse of existing building blocks.

**Resolved — performer dropout mid-piece:** Task 6 automatically re-syncs using only the currently-active performers whenever the active set changes (a performer stops singing or drops out). The listener feed continues uninterrupted with the remaining performers' synced mix — no freeze/hold and no fallback-to-raw. If the `lead` (sync anchor) is the one who drops, Task 6 must pick a new anchor from the remaining active performers (e.g. whoever has been singing longest in the current phrase) to keep the group aligned.

## New/updated requirements this section introduces

These aren't new numbered tasks — they're integration requirements that fall out of composing Tasks 1–8 into the three modes above, and should be tracked wherever the relevant task is implemented:

- **Task 1:** room-level `mode` field + role assignment at join time.
- **Task 5:** a defined formula for collapsing pitch/timing deviation into a single 0–100% score (Teach mode).
- **Tasks 4, 6, 7:** streaming-capable variants suitable for <1–2s end-to-end latency (Practice, Performance), in addition to the batch/offline path.
- **New capability — output routing/mixing:** the platform needs to deliver *different* audio feeds to different participants in the same room (e.g., Performance mode's performers-hear-raw / listeners-hear-processed split; Practice mode's raw-call-plus-enhanced-feed). This is a genuinely new piece of infrastructure not covered by Tasks 1–8 as originally written, and should likely be its own task once the above is validated — tentatively **Task 9: multi-feed audio routing**.

---

# Part 3: Team assignments (3-person split)

Three tracks, each owned by one person, ordered so each person's first task matches their assignment below. Tracks run in parallel where possible; cross-track dependencies are called out explicitly — agree on the **shared interfaces** (Signal schema, pitch-tracking module signature) early since both are consumed across tracks.

## Person A — Platform & Infrastructure track

1. **Task 1 — Video conversation platform.** (first task) Get a working multi-party call with per-participant audio tracks exposed for downstream processing. Sized for ~25 concurrent participants (SFU-backed).
2. **Task 9 — Multi-feed audio routing** *(new task, defined in Part 2)*. Extend the platform so different participants in the same room can receive different audio feeds — e.g. Performance mode's performers-hear-raw/listeners-hear-processed split, Practice mode's raw-call-plus-enhanced-feed.
3. **Mode & role infrastructure** (Part 2, "Mode selection & roles"). Add the room-level `mode` field (`teach` / `practice` / `performance`) and role assignment at join time (`teacher`/`student`, peer/peer, `performer`/`lead`/`listener`).
4. **Performance mode — integration owner** (see "Mode integration ownership" below).

## Person B — Audio Signal Intelligence track

1. **Task 2 — Singing detection.** (first task) Per-participant `is_singing` classification off Person A's raw audio tracks.
2. **Task 7 — Signal cleaning (noise removal).** Build both the batch variant (operates on a closed Signal) and the streaming variant (<1–2s latency, needed by Practice/Performance) — resolved to run automatically on every extracted Signal.
3. **Task 8 — Convert signal to notes.** Build the shared pitch-tracking module (YIN/pYIN/CREPE) as part of this task — **Person C's Task 5 depends on this module**, so land a stable function signature for it early and communicate it, even before Task 8's full note-display output is finished.
4. **Practice mode — integration owner** (see "Mode integration ownership" below).

## Person C — Signal Storage & Alignment track

1. **Task 3 — Signal extraction, storage, and replay.** (first task) Define and own the **Signal schema** — this is the contract everyone else's tasks consume, so land it early and communicate any changes immediately.
2. **Task 4 — Sync two signals.** Full DTW-based alignment (resolved — not just constant offset), with an incremental/streaming variant for the <1–2s latency modes.
3. **Task 6 — Sync multiple (<5) signals.** Extends Task 4 to a group, with a live reference-reassignment path for Performance mode's performer-dropout handling (resolved in Part 2).
4. **Task 5 — Compare two signals.** Depends on Task 4 (alignment) and on Person B's pitch-tracking module (Task 8) for the pitch-deviation metric. Implement the resolved `0.7 * pitch_accuracy + 0.3 * timing_accuracy` scoring formula.
5. **Teach mode — integration owner** (see "Mode integration ownership" below).

## Mode integration ownership

Each mode from Part 2 touches all three tracks' outputs, but rather than treat integration as one undifferentiated joint session, each mode gets a single driving owner — the person whose track that mode leans on most — who pulls in the other two tracks' primitives via the shared interfaces below. The other two people support their piece of each mode as consumers of their own already-built tasks, not as separate new work.

- **Teach mode → Person C owns.** Builds the `current_reference` state machine and the grading flow (Part 2's Teach mode steps 2–4, including the resolved overlap handling). Consumes: Person B's Task 7 (clean) + Task 8 (notes) to produce the displayed note output; Person A's room/role data to know who's the teacher vs. student. Person C already owns the scoring formula (Task 5), so this is a natural extension of that work.
- **Practice mode → Person B owns.** Builds the dual-stream capture → clean → sync → mix → deliver flow (Part 2's Practice mode), including the resolved solo-singer behavior. Consumes: Person C's Task 4 (streaming sync) for the alignment step; Person A's Task 9 (routing) to deliver the second "enhanced" feed alongside the raw call.
- **Performance mode → Person A owns.** Builds the group capture → clean → group-sync → route-to-listeners flow (Part 2's Performance mode), including the resolved lead-dropout reassignment. Consumes: Person B's Task 7 (streaming clean) per performer; Person C's Task 6 (group sync + reference reassignment). Natural fit since the listener/performer feed split is fundamentally a routing problem (Person A's Task 9).

Each mode owner is responsible for the end-to-end wiring and testing of their mode, but the underlying primitives (sync, clean, notes, routing) stay owned by whoever built them in their track — mode owners integrate, they don't reimplement.

## Shared interfaces to lock down early

- **Signal schema** (Person C, Task 3) — every other track's tasks read/write this. Treat changes to it as breaking changes requiring a heads-up to A and B.
- **Pitch-tracking module** (Person B, Task 8) — consumed by Person C's Task 5. Agree on a stub signature (e.g. `extract_pitch_contour(audio) -> [{time_ms, pitch_hz, confidence}]`) before either side needs the real implementation, so both can develop against it in parallel.
- **Streaming latency contract** — Tasks 4, 6, 7 (Person B/C) all need a <1–2s streaming variant; agree on a common "rolling buffer" input shape so these compose without each person inventing their own.

---

# Part 4: Project structure & integration contracts

**Are the tasks independently implementable?** Not in the sense of "zero dependencies" — B's Task 2 needs a raw audio track shape from A, C's Task 3 needs singing events from B, C's Task 5 needs the pitch-tracking module from B, and so on. They *are* independently implementable in the sense that matters for parallel work: every cross-track dependency is mediated by a **contract** (a schema or function signature), not by waiting on someone else's actual code. Once a contract is agreed and stubbed, both sides build against the stub and swap in the real implementation later without changing their own code. The structure below exists to make that swap-in safe and to make it obvious when a contract has drifted.

## Repo layout

```
cmuhacks26/
├── ROADMAP.md
├── shared/                          # the ONLY place cross-track contracts are defined — nothing else may redefine these types
│   ├── schemas/
│   │   ├── signal.schema.json           # Task 3 — Person C owns; A & B are consumers
│   │   ├── singing_event.schema.json    # Task 2 — Person B owns; C is a consumer
│   │   ├── pitch_contour.schema.json    # Task 8 — Person B owns; C (Task 5) is a consumer
│   │   ├── alignment_result.schema.json # Task 4/6 — Person C owns; A (routing/mix), B (mode owners) consume
│   │   ├── comparison_result.schema.json# Task 5 — Person C owns; A/UI consumes for display
│   │   ├── room_state.schema.json       # Task 1/mode infra — Person A owns; B & C consume for role/mode lookups
│   │   └── audio_track.schema.json      # Task 1 — Person A owns; B & C consume as their pipeline's raw input
│   ├── bindings/                    # generated or hand-written typed bindings derived FROM the schemas above (TS for platform/, Python for the audio backend) — never hand-edit a binding without regenerating from its schema
│   └── mocks/                       # fixture data + stub functions for every schema above, so no one is ever blocked waiting on someone else's real implementation
│       ├── mock_audio_track.*
│       ├── mock_singing_events.*
│       ├── mock_signal.*
│       └── mock_pitch_contour.*
│
├── platform/                        # Person A — Task 1, Task 9, mode/role infra, Performance mode
│   ├── src/                         # WebRTC/SFU room logic, UI
│   ├── api/                         # the one real network boundary in this project — what platform/ exposes to the audio backend (see "The one real boundary" below)
│   └── tests/
│
├── audio-intelligence/               # Person B — Task 2, 7, 8, Practice mode
│   ├── detection/                    # Task 2
│   ├── cleaning/                     # Task 7
│   ├── notes/                        # Task 8, including the shared pitch-tracking module
│   └── tests/
│
├── signal-processing/                # Person C — Task 3, 4, 5, 6, Teach mode
│   ├── storage/                      # Task 3, owns Signal schema
│   ├── sync/                         # Task 4, 6
│   ├── compare/                      # Task 5
│   └── tests/
│
├── modes/                             # orchestration layer, one owner per subfolder per Part 3
│   ├── teach/                         # Person C
│   ├── practice/                      # Person B
│   └── performance/                   # Person A
│
└── docs/
    └── integration-contracts.md       # human-readable restatement of every schema in shared/schemas — what it means, who owns it, who consumes it, and the change-review rule below
```

`audio-intelligence/` and `signal-processing/` operate on the same live audio pipeline in sequence (detect → extract/store → clean → sync/compare/notes) and will likely run inside one backend process for latency reasons — keep them as separate packages/modules with a clear function-call boundary between them, not separate network services. The **one real network/language boundary** in this project is between `platform/` (likely JS/TS, WebRTC-facing) and the combined audio backend (likely Python, ML-facing) — that boundary needs the most rigor, since it's the only place a schema mismatch can't be caught by a type checker on both sides automatically.

## The one real boundary: platform ↔ audio backend

Document this explicitly in `docs/integration-contracts.md` before either side is far along:
- How raw per-participant audio gets from `platform/` to the audio backend (e.g. a WebSocket streaming raw PCM chunks, keyed by `participant_id` + `room_id`).
- How processed results get back (note events for display, comparison scores, the Practice/Performance mixed feed) — likely a WebSocket channel per room, keyed by the same IDs, carrying JSON that matches `comparison_result.schema.json` / a note-event schema.
- Both directions should have a minimal example payload committed in `shared/mocks/`, so each side can build against it without the other side running.

## Rules that prevent incompatibility

1. **Single source of truth.** A type/shape used across a track boundary is defined once, in `shared/schemas/`, and nowhere else. If you find yourself hand-writing a matching struct/interface in your own track's code, import/generate it from the schema instead.
2. **Owner defines, consumers review.** Each schema has one owner (noted above), but changing a schema that others already consume requires a heads-up/review from every consumer before merging — not just a unilateral edit. A breaking change bumps a `version` field on the schema.
3. **Stub before you block.** Whoever owns a schema commits a mock/fixture for it in `shared/mocks/` as soon as the shape is agreed — before the real implementation exists. Downstream consumers build against the mock immediately; swapping the mock for the real implementation later should require no code changes on the consumer's side, only a wiring change.
4. **Integration tests live in `modes/`, run against real implementations.** Per-track unit tests (in each track's own `tests/`) can and should use mocks. But `modes/` — where all three tracks actually meet — should have integration tests that exercise the real implementations together as they land, to catch contract drift that unit tests against mocks would miss.

---

# Part 5: Status after the first integration pass, and round-2 assignments

The three tracks from Part 3 have been built, merged, and run live (video call across two real devices, LiveKit-backed, with the real audio backend attached and confirmed audible in Performance mode). This section replaces Part 3 as the active assignment — read it instead of re-deriving from Part 3 when picking up work now.

## Current status

| Task | Status |
|------|--------|
| 1 — Video conversation platform | **Done.** LiveKit wired for real (not just scaffolded) — `platform/src/call/LiveKitCallSession.ts` connects, publishes local mic+camera, subscribes to remote audio+video. `platform/src/main.ts` renders real `<video>` tiles. Confirmed working across two physical devices over a public tunnel. |
| 2 — Singing detection | **Done.** `audio-intelligence/detection/` (YIN-based). |
| 3 — Signal extraction, storage, replay | **Done.** `signal-processing/storage/`. |
| 4 — Sync two signals | **Done.** `signal-processing/sync/`. Includes the resolved DTW-warp-path requirement, a streaming variant, a timestamp-offset fallback for unrelated content, and (after two rounds of live-audio debugging) an emit-cursor + crossfade fix so streaming output doesn't stutter or click at chunk boundaries. |
| 5 — Compare two signals | **Done.** `signal-processing/compare/`. Implements the resolved 70/30 pitch/timing score; reuses Task 8's pitch tracker; explicitly refuses to score a `timestamp_offset`-fallback alignment rather than fabricating a number. |
| 6 — Sync multiple (<5) signals | **Stand-in only, not the real task.** `backend/worker.py`'s `PerformanceGroupSession` does pairwise composition (pick a reference, pairwise-align everyone else) — functionally works, but has no live sync-anchor reassignment on performer dropout the way Part 2's Performance mode spec requires. That logic exists only as an inert TypeScript stub (`modes/performance/stubs/groupSync.ts`) that never runs once a real backend is attached. **This is the top remaining gap for Performance mode.** |
| 7 — Signal cleaning | **Done.** `audio-intelligence/cleaning/` (spectral subtraction, batch + streaming). |
| 8 — Notes from signal | **Done** as a library (`audio-intelligence/notes/`), but **not surfaced anywhere in the UI.** Nobody sees note output on screen yet. |
| 9 — Multi-feed audio routing | **Done.** `platform/src/feedRouter.ts`, and — as of this pass — actually carrying real processed audio (not just unit-tested in isolation): `backend/worker.py` + `backend/supervisor.py` auto-attach to every room and publish real mixes through it. |

| Mode | Status |
|------|--------|
| Practice | **Working**, wired to the real backend end-to-end. |
| Performance | **Working, confirmed live** (this session's goal) — real video, real mic capture, real DTW-synced mix audible on a listener's device. Still has real gaps, listed below. |
| Teach | **Not built.** No code exists in `modes/teach/`. Task 5 (its main dependency) is now done, so this is unblocked. |

## New framework pieces built this pass (not in the original 8 tasks)

- `backend/worker.py` — the real out-of-process Python backend: connects to the platform as `role=processor`, runs real Task 2/4/7 (and the Task 6 stand-in) on real audio, replacing the in-process TypeScript stubs.
- `backend/supervisor.py` — auto-attaches a worker to every Practice/Performance room; no manual per-room command needed.
- `platform/api/server.ts` — the `role=processor` fan-out, a generalized `mix_chunk` message type, and a `processing` status field on `GET /rooms` so the UI can show whether real processing is actually running.
- `platform/src/main.ts` — real video tiles, mic tap → backend, processed-feed playback, a visible room code, and a "Real processing: ON/OFF" status badge.
- Two audible bugs found and fixed only by testing with real human audio (not synthetic test fixtures): chunk-boundary discontinuities (crossfade fix) and local mic → local speaker feedback + a browser autoplay-suspended `AudioContext` that could silently produce no sound.

## Known open gaps (tracked in `docs/integration-contracts.md`)

- Room state is in-memory only — an API server restart wipes every room. Caused real confusion during live testing this session.
- `audio_track.schema.json`'s metadata (`track_id`/`format`/`is_remote`) isn't threaded through the WS boundary into Person B's detector/cleaner inputs.
- `render_aligned_playback`'s time-warp resampling is naive linear interpolation (`np.interp`), not pitch-preserving (phase vocoder/WSOLA) — a residual source of small audio artifacts independent of the chunking/crossfade fixes.
- `backend/worker.py` assumes one canonical sample rate per session; two participants' browsers reporting different native rates isn't handled.
- Performance mode has only been exercised live with 2 performers + 1 listener, never 3-4 performers or a live performer dropout/reassignment.
- `PerformanceGroupSession` never writes to Task 3's `SignalStore` — only Practice mode's session does. Teach mode and any future replay/analysis need Performance's Signals captured too.

## Round-2 assignments

Each person keeps roughly the ownership area they already have context on, but the tasks themselves are different from Part 3 — this is hardening + one net-new mode, not the original 8 tasks.

### Person A — Platform & routing track

1. **Room persistence.** Rooms currently live only in `RoomStore`'s in-memory `Map`. Add a simple durable backing (SQLite is fine, matching `signal-processing/storage/`'s existing pattern) so an API server restart doesn't lose every active room. This is the fix most likely to unblock smooth live testing going forward.
2. **Multi-performer + dropout live testing.** Exercise Performance mode with 3-4 real performers and a real mid-session dropout (a performer leaving) against the real backend (`backend/worker.py`, once Person C's Task 6 lands below) — find and fix whatever routing/subscription issues show up; today this path is only unit-tested with the TS stub, never run live with a real backend attached.
3. **Task 8 note display in the UI.** `audio-intelligence/notes/` already produces note events; nothing shows them on screen. Add a channel (extend the existing `mix_chunk`/`feed_chunk` WS messages, or a new message type) to push note events to the right participants' screens, and render them (note names are enough — a piano-roll is a nice-to-have, not required).
4. **Dynamic feed re-subscription + clean participant-leave handling.** `startFeedPlayback` in `main.ts` only subscribes once, at join; if `room.feeds[participant_id]` changes later (e.g. a role change) the client never resubscribes. Also confirm a participant leaving mid-session doesn't leave a stale/broken worker-side session (`PracticeSession` in particular assumes exactly 2 participants for its lifetime).

### Person B — Audio intelligence & quality track

1. **Replace naive resampling with pitch-preserving time-stretch.** `signal-processing/sync/playback.py`'s `render_aligned_playback` uses raw `np.interp` on waveform samples. Swap in a phase vocoder or WSOLA-style approach for the actual audio people hear — should measurably reduce artifacts independent of the chunking fix already in place.
2. **Handle mismatched sample rates across participants.** `backend/worker.py` assumes one global rate per session (whatever the first chunk reports). Resample each incoming stream to a canonical rate before cleaning/aligning, so two different browsers/devices reporting different native rates doesn't silently corrupt the mix.
3. **Close the `audio_track.schema.json` round-trip gap.** Thread `track_id`/`format`/`is_remote` from the WS `audio_chunk` message through into `SingingDetector`/`StreamingCleaner`'s actual inputs, instead of the metadata existing only on the platform side.
4. **Wire Task 3 storage into Performance mode.** `modes/practice/pipeline.PracticeSession` already saves + auto-cleans Signals via `SignalStore`. `backend/worker.py`'s `PerformanceGroupSession` doesn't persist anything. Add the same capture-on-singing-stop behavior there, attributed per performer, so Performance sessions leave behind real Signals for replay/analysis/Teach-mode-style grading later.

### Person C — Signal alignment & Teach mode track (mine)

1. **Real Task 6.** Replace `PerformanceGroupSession`'s pairwise-composed stand-in with genuine streaming group sync: live sync-anchor reassignment when the current anchor (lead or otherwise) drops out, picking whoever's been singing longest among the remaining active performers — the exact rule Part 2's Performance mode section already specifies, currently only implemented (inertly) in the TypeScript stub. This is the top item for "Performance mode working well" beyond what's already confirmed live.
2. **Teach mode.** Build the `current_reference` state machine and grading flow from Part 2's spec: teacher sings → notes shown to both, sets `current_reference`; student sings → notes shown, graded against `current_reference` via Task 5's now-complete `compare_signals` if one exists; the resolved overlap-handling rule (teacher's Signal wins the reference on simultaneous singing). Wire into `modes/teach/`, `backend/worker.py`'s mode dispatch (currently only handles `"practice"`/`"performance"`), and the UI (score display).
3. **Integration test for the browser audio path.** `backend/tests/test_backend_worker_integration.py` proves the server-to-backend loop end-to-end but stands in for the browser with raw WebSocket messages. The two bugs found by live-testing this pass (mic echo, suspended `AudioContext`) weren't things any existing test could have caught. Add whatever automated coverage is practical here (a headless-browser test via Playwright is the most faithful option; a scripted check of `AudioTrackTap`'s graph wiring and `AudioContext.state` handling is a lighter-weight fallback) so audible-output regressions don't require a live two-device test to catch again.

---

# Part 6: Human verification pass — run this tomorrow

Every "Done" status in Part 5 is backed by automated tests plus, for a
handful of items, one informal live check. That's not enough: two real
audible bugs this session (local mic feeding back into local speakers, a
suspended `AudioContext` silently producing no sound) passed every
automated test and were only found by a human actually listening. Nothing
in Part 5 should be trusted as "working" for a live demo until a human has
actually run it end-to-end — this section is that pass, written to be
followed directly, not re-derived.

Assign one person to drive this (doesn't have to split three ways like
Part 5's dev work) with the other two available to join as the second/
third device for whichever scenario needs multiple people. Record results
directly in this file (turn each checkbox's status into ✅/❌ + a one-line
note) so tomorrow's findings aren't lost.

## Setup (once, before running any scenario)

1. Start the API server with real LiveKit credentials:
   `LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=... npm run api`
2. Start the frontend: `npm run dev`
3. Expose it over HTTPS (required for camera/mic on a non-`localhost`
   device): `cloudflared tunnel --url http://localhost:5173` — note the
   URL, it's random and different every time this command runs.
4. Start the real backend: `python backend/supervisor.py` (auto-attaches to
   every room; no per-room command needed).
5. Sanity check before involving other people: `curl <tunnel-url>/health`
   returns `{"ok":true,...}`, and creating a room via the UI shows
   `Real processing: OFF` until a worker attaches, then `ON` shortly after
   real audio starts flowing.
6. Have physical devices ready — **at least two genuinely different
   physical devices for every scenario below**, not two browser tabs on
   one machine. Two tabs on one machine won't catch the sample-rate/
   hardware-mismatch class of bug Part 5 flags as open.

## Test scenarios

Each one: exact steps, what a human should see/hear to call it a pass, and
current status going into tomorrow.

1. **Video call baseline (Task 1).** Create a room (any mode), join from
   two devices. Pass: each device shows the other's live camera video
   within a few seconds, both directions, audio+video roughly in sync.
   **Status: confirmed working** — re-run as a smoke test before the rest.
2. **Room code sharing.** Confirm the room code is prominently visible and
   copyable on screen; join a second device by pasting it (use "Join
   existing", not "Create room & join" — that makes a separate room).
   **Status: confirmed working.**
3. **Performance mode, 2 performers + 1 listener (Tasks 2, 4, 6-stand-in,
   7, 9).** Device A joins `lead`, device B joins `performer`, device C
   (or a third tab) joins `listener`. Both performers hum/sing the *same*
   simple phrase. Pass: the listener hears a mixed, audibly-synced result
   within roughly 1-2s of the performers singing, and the "Real
   processing" badge shows `ON` with a `performance_mix` update. Listen
   critically, not just for "did any sound come through" — does it
   actually sound aligned, or just mixed-and-hoped? **Status: confirmed
   once, informally ("it seems to work") — re-verify with real critical
   listening, this is the highest-value re-check tomorrow.**
4. **Performance mode, 3-4 performers.** Same as #3 with more performers.
   **Status: never tested.**
5. **Performance mode, performer dropout mid-session.** One performer
   leaves while singing continues among the rest. Pass: the listener's
   feed doesn't freeze or error; once Person C's real Task 6 lands, the
   sync anchor should reassign to a remaining active performer rather than
   staying pinned to whoever left. **Status: never tested — the
   reassignment half is blocked on Person C's Task 6 work in Part 5, but
   the "doesn't break" half can be checked against the current stand-in
   today.**
6. **Practice mode, 2 peers (Tasks 2, 4, 7, 9).** Two devices join as
   `peer`. Have them hum/sing the same phrase, then different phrases.
   Pass: both hear an "enhanced" mixed feed for the same-phrase case; for
   the different-phrase case, confirm it doesn't produce garbage (Task 4's
   `timestamp_offset` fallback should engage — check the processing badge's
   meta for `used_sync`). **Status: never tested with real humans/devices
   — only scripted synthetic audio has exercised this path. Practice
   didn't get the attention Performance did this pass; treat this as the
   top-priority new test tomorrow.**
7. **Teach mode.** **Blocked — not built.** Skip until Person C's Teach
   mode work in Part 5 lands; don't spend time on it tomorrow.
8. **Task 8 note display.** **Blocked — no UI yet** (Person A's Part 5
   item). Until then, the only available check is headless: run
   `audio-intelligence/notes/`'s extraction on a recorded clip and eyeball
   whether the output note names look reasonable. Not a substitute for
   seeing notes on screen once the UI lands.
9. **Task 3 replay — real sessions, not test scripts.** After running
   scenario #3 or #6, check the storage root on disk for newly-written WAV
   blobs and play a couple back directly. Pass: they contain real,
   recognizable audio from the session just run. Performance mode won't
   produce anything yet (Person B's Part 5 item covers wiring storage into
   it) — Practice mode should already write something.
10. **Cross-device audio quality.** Specifically using two *different*
    physical devices/hardware (not two tabs), listen for pitch weirdness,
    dropouts, or echo during scenarios #3 and #6 — this is where a sample-
    rate mismatch (Person B's open Part 5 item) would actually show up.

## Bug-reporting protocol

For anything that fails or sounds wrong, capture: (a) exact steps that
reproduce it, (b) expected vs. observed, (c) the "Real processing" badge's
state at the time, (d) any browser console errors, (e) which server log
(api / vite / supervisor) shows anything relevant. File it as a new bullet
under the relevant person's Part 5 assignment if it maps to an existing
gap, or as a new `> **Open question:**` callout if it's a design ambiguity
rather than a straightforward bug.

---

*Roadmap updated after the first live integration pass: video calling, Task 1-9 (Task 6 as a stand-in), and Performance mode confirmed working end-to-end across two real devices with the real audio backend attached. Task 5 (compare/score) completed this pass. Teach mode remains unbuilt. See Part 5 for current status and the round-2 3-person split, and Part 6 for the human verification pass to run before trusting any of it for a demo.*
