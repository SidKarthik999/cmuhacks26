/**
 * Task 9 — Multi-feed audio routing.
 *
 * Delivers different named audio feeds to different participants in the same room.
 * Does not mix audio itself; it decides *who receives which feed* and fans out
 * buffer chunks to the subscribed set.
 */
import type { FeedName } from "../../shared/bindings/room_state.js";

export interface AudioFeedChunk {
  feed: FeedName;
  room_id: string;
  seq: number;
  timestamp_ms: number;
  sample_rate: number;
  channels: number;
  format: "pcm_f32" | "pcm_s16" | "opus";
  /** Float32 samples (mono interleaved if channels>1) or empty for signaling-only tests. */
  samples: Float32Array;
  meta?: Record<string, unknown>;
}

export type FeedListener = (chunk: AudioFeedChunk) => void;

export class MultiFeedRouter {
  /** participant_id -> subscribed feeds */
  private subscriptions = new Map<string, Set<FeedName>>();
  /** participant_id -> listeners */
  private listeners = new Map<string, Set<FeedListener>>();
  private seqByFeed = new Map<FeedName, number>();

  setSubscriptions(participant_id: string, feeds: FeedName[]): void {
    this.subscriptions.set(participant_id, new Set(feeds));
  }

  getSubscriptions(participant_id: string): FeedName[] {
    return [...(this.subscriptions.get(participant_id) ?? [])];
  }

  subscribe(participant_id: string, listener: FeedListener): () => void {
    let set = this.listeners.get(participant_id);
    if (!set) {
      set = new Set();
      this.listeners.set(participant_id, set);
    }
    set.add(listener);
    return () => {
      set!.delete(listener);
      if (set!.size === 0) this.listeners.delete(participant_id);
    };
  }

  /** Publish a chunk on a named feed; only participants subscribed to that feed receive it. */
  publish(chunk: Omit<AudioFeedChunk, "seq"> & { seq?: number }): string[] {
    const seq =
      chunk.seq ??
      (this.seqByFeed.get(chunk.feed) ?? 0);
    this.seqByFeed.set(chunk.feed, seq + 1);

    const full: AudioFeedChunk = { ...chunk, seq, samples: chunk.samples };
    const deliveredTo: string[] = [];

    for (const [participant_id, feeds] of this.subscriptions) {
      if (!feeds.has(chunk.feed)) continue;
      const ls = this.listeners.get(participant_id);
      if (!ls || ls.size === 0) {
        deliveredTo.push(participant_id);
        continue;
      }
      for (const listener of ls) {
        listener(full);
      }
      deliveredTo.push(participant_id);
    }
    return deliveredTo;
  }

  /** Helper: sync subscriptions from room.feeds map. */
  syncFromRoomFeeds(feeds: Record<string, FeedName[]>): void {
    this.subscriptions.clear();
    for (const [pid, names] of Object.entries(feeds)) {
      this.setSubscriptions(pid, names);
    }
  }
}
