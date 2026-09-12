/**
 * Stub for Person C Task 6 — sync multiple signals (streaming variant)
 * with live reference reassignment for Performance-mode dropout.
 */
export interface PerformerStreamChunk {
  participant_id: string;
  timestamp_ms: number;
  samples: Float32Array;
}

export interface GroupSyncState {
  anchor_id: string | null;
  /** When each active performer started the current singing phrase. */
  singing_started_at: Map<string, number>;
}

export interface GroupSyncResult {
  anchor_id: string | null;
  aligned: PerformerStreamChunk[];
  /** Stub: constant 0 offsets until real DTW lands. */
  offsets_ms: Record<string, number>;
}

export function groupSync(input: {
  streams: PerformerStreamChunk[];
  active_performer_ids: string[];
  state: GroupSyncState;
}): GroupSyncResult {
  let anchor = input.state.anchor_id;
  if (!anchor || !input.active_performer_ids.includes(anchor)) {
    // Reassign: longest-singing active performer.
    let best: string | null = null;
    let bestStarted = Infinity;
    for (const id of input.active_performer_ids) {
      const started = input.state.singing_started_at.get(id) ?? Infinity;
      if (started < bestStarted) {
        bestStarted = started;
        best = id;
      }
    }
    anchor = best;
    input.state.anchor_id = anchor;
  }

  const offsets_ms: Record<string, number> = {};
  for (const id of input.active_performer_ids) {
    offsets_ms[id] = 0;
  }

  const activeSet = new Set(input.active_performer_ids);
  const aligned = input.streams.filter((s) => activeSet.has(s.participant_id));

  return { anchor_id: anchor, aligned, offsets_ms };
}
