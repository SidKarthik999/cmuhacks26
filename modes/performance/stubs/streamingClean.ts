/**
 * Stub for Person B Task 7 — streaming signal cleaning.
 * Identity pass-through with a metadata flag so Performance mode can develop
 * against the contract before the real denoiser lands.
 */
export interface CleanedStreamChunk {
  participant_id: string;
  timestamp_ms: number;
  sample_rate: number;
  samples: Float32Array;
  metadata: { cleaned: true; stub: true };
}

export function streamingClean(input: {
  participant_id: string;
  timestamp_ms: number;
  sample_rate: number;
  samples: Float32Array;
}): CleanedStreamChunk {
  // Stub: copy buffer (real impl would denoise in <1–2s budget).
  const samples = new Float32Array(input.samples.length);
  samples.set(input.samples);
  return {
    participant_id: input.participant_id,
    timestamp_ms: input.timestamp_ms,
    sample_rate: input.sample_rate,
    samples,
    metadata: { cleaned: true, stub: true },
  };
}
