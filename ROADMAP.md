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

*Roadmap updated with the three-mode composition layer (Teach, Practice, Performance). All previously flagged open questions are resolved (max participants, tempo-drift/DTW requirement, cleaning-automatic behavior, scoring formula, overlap handling, Practice mode single-singer behavior, Performance mode dropout handling). No open questions remain as of this revision — future design changes should be added as new `> **Open question:**` callouts as they arise.*
