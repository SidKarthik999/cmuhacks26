/**
 * Performance mode orchestration — Person A owns.
 *
 * Wires: group capture → streaming clean (Person B Task 7 stub) →
 * group sync (Person C Task 6 stub) → Task 9 route-to-listeners.
 * Lead dropout reassigns the sync anchor from remaining active performers.
 */
import type { RoomState } from "../../shared/bindings/room_state.js";
import { MultiFeedRouter, type AudioFeedChunk } from "../../platform/src/feedRouter.js";
import { isPerformerRole } from "../../platform/src/roles.js";
import type { RoomStore } from "../../platform/src/roomStore.js";
import {
  streamingClean,
  type CleanedStreamChunk,
} from "./stubs/streamingClean.js";
import {
  groupSync,
  type GroupSyncState,
  type PerformerStreamChunk,
} from "./stubs/groupSync.js";

export interface PerformanceOrchestratorOpts {
  roomStore: RoomStore;
  router: MultiFeedRouter;
  room_id: string;
}

export class PerformanceOrchestrator {
  private roomStore: RoomStore;
  private router: MultiFeedRouter;
  private room_id: string;
  private sync: GroupSyncState = {
    anchor_id: null,
    singing_started_at: new Map(),
  };
  private mixSeq = 0;

  constructor(opts: PerformanceOrchestratorOpts) {
    this.roomStore = opts.roomStore;
    this.router = opts.router;
    this.room_id = opts.room_id;
  }

  getRoom(): RoomState {
    const room = this.roomStore.getRoom(this.room_id);
    if (!room || room.mode !== "performance") {
      throw new Error(`Room ${this.room_id} is not in performance mode.`);
    }
    return room;
  }

  /** Call when Task 2 reports singing start for a performer. */
  onSingingStarted(participant_id: string, at_ms = Date.now()): RoomState {
    const room = this.getRoom();
    const p = room.participants.find((x) => x.participant_id === participant_id);
    if (!p || !isPerformerRole(p.role)) {
      throw new Error(`${participant_id} is not a performer.`);
    }
    this.sync.singing_started_at.set(participant_id, at_ms);
    const active = new Set(room.performance?.active_performer_ids ?? []);
    active.add(participant_id);
    return this.roomStore.setActivePerformers(this.room_id, [...active]);
  }

  /** Call when a performer stops singing or drops out. */
  onPerformerInactive(participant_id: string): RoomState {
    const room = this.getRoom();
    const remaining = (room.performance?.active_performer_ids ?? []).filter(
      (id) => id !== participant_id,
    );
    this.sync.singing_started_at.delete(participant_id);
    let updated = this.roomStore.setActivePerformers(this.room_id, remaining);

    const wasAnchor =
      updated.performance?.sync_anchor_id === participant_id ||
      updated.performance?.lead_participant_id === participant_id;

    if (wasAnchor || !remaining.includes(updated.performance?.sync_anchor_id ?? "")) {
      const nextAnchor = this.pickNewAnchor(remaining);
      this.sync.anchor_id = nextAnchor;
      updated = this.roomStore.setSyncAnchor(this.room_id, nextAnchor);
    }
    return updated;
  }

  /**
   * Pick new sync anchor: prefer whoever has been singing longest in the
   * current phrase (resolved Performance-mode dropout rule).
   */
  pickNewAnchor(active_ids: string[]): string | null {
    if (active_ids.length === 0) return null;
    let best: string | null = null;
    let bestStarted = Infinity;
    for (const id of active_ids) {
      const started = this.sync.singing_started_at.get(id) ?? Infinity;
      if (started < bestStarted) {
        bestStarted = started;
        best = id;
      }
    }
    return best ?? active_ids[0] ?? null;
  }

  /**
   * Ingest one raw performer PCM frame → clean (B) → group-sync (C) →
   * publish performance_mix to listeners via Task 9.
   */
  ingestPerformerFrame(input: {
    participant_id: string;
    timestamp_ms: number;
    samples: Float32Array;
    sample_rate?: number;
  }): { delivered_to: string[]; cleaned: CleanedStreamChunk; room: RoomState } {
    let room = this.getRoom();
    this.router.syncFromRoomFeeds(room.feeds);

    if (!this.sync.singing_started_at.has(input.participant_id)) {
      room = this.onSingingStarted(input.participant_id, input.timestamp_ms);
    }

    const cleaned = streamingClean({
      participant_id: input.participant_id,
      timestamp_ms: input.timestamp_ms,
      samples: input.samples,
      sample_rate: input.sample_rate ?? 48000,
    });

    const frame: PerformerStreamChunk = {
      participant_id: cleaned.participant_id,
      timestamp_ms: cleaned.timestamp_ms,
      samples: cleaned.samples,
    };

    const active = room.performance?.active_performer_ids ?? [];
    let anchor = room.performance?.sync_anchor_id ?? null;
    if (!anchor || !active.includes(anchor)) {
      anchor = this.pickNewAnchor(active);
      room = this.roomStore.setSyncAnchor(this.room_id, anchor);
    }
    this.sync.anchor_id = anchor;

    const synced = groupSync({
      streams: [frame],
      active_performer_ids: active,
      state: this.sync,
    });

    const mixSamples = mixDown(synced.aligned.map((s) => s.samples));
    const chunk: Omit<AudioFeedChunk, "seq"> = {
      feed: "performance_mix",
      room_id: this.room_id,
      timestamp_ms: input.timestamp_ms,
      sample_rate: cleaned.sample_rate,
      channels: 1,
      format: "pcm_f32",
      samples: mixSamples,
      meta: {
        anchor_participant_id: synced.anchor_id,
        contributor_ids: synced.aligned.map((s) => s.participant_id),
        mix_seq: this.mixSeq++,
      },
    };

    const delivered_to = this.router.publish(chunk);
    return { delivered_to, cleaned, room: this.getRoom() };
  }
}

function mixDown(buffers: Float32Array[]): Float32Array {
  if (buffers.length === 0) return new Float32Array(0);
  const len = Math.max(...buffers.map((b) => b.length));
  const out = new Float32Array(len);
  for (const b of buffers) {
    for (let i = 0; i < b.length; i++) {
      out[i]! += b[i]! / buffers.length;
    }
  }
  return out;
}
