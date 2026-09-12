/**
 * PCM tap: turns a MediaStreamTrack into rolling Float32 chunks for the
 * platform → audio-backend WebSocket (see docs/integration-contracts.md).
 */
export interface PcmChunk {
  track_id: string;
  participant_id: string;
  room_id: string;
  seq: number;
  timestamp_ms: number;
  sample_rate: number;
  channels: 1;
  format: "pcm_f32";
  samples: Float32Array;
}

export type PcmChunkHandler = (chunk: PcmChunk) => void;

export class AudioTrackTap {
  private ctx: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private silentGain: GainNode | null = null;
  private seq = 0;
  private startedAt = 0;

  constructor(
    private readonly meta: {
      track_id: string;
      participant_id: string;
      room_id: string;
    },
    private readonly onChunk: PcmChunkHandler,
    private readonly bufferSize = 4096,
  ) {}

  start(mediaTrack: MediaStreamTrack, sampleRate = 48000): void {
    const AudioCtx =
      globalThis.AudioContext ||
      (globalThis as unknown as { webkitAudioContext: typeof AudioContext })
        .webkitAudioContext;
    this.ctx = new AudioCtx({ sampleRate });
    const stream = new MediaStream([mediaTrack]);
    this.source = this.ctx.createMediaStreamSource(stream);
    // ScriptProcessor is deprecated but widely available; swap to AudioWorklet later.
    this.processor = this.ctx.createScriptProcessor(this.bufferSize, 1, 1);
    this.startedAt = this.ctx.currentTime;
    this.processor.onaudioprocess = (ev) => {
      const input = ev.inputBuffer.getChannelData(0);
      const samples = new Float32Array(input.length);
      samples.set(input);
      const chunk: PcmChunk = {
        track_id: this.meta.track_id,
        participant_id: this.meta.participant_id,
        room_id: this.meta.room_id,
        seq: this.seq++,
        timestamp_ms: Math.round(
          (this.ctx!.currentTime - this.startedAt) * 1000,
        ),
        sample_rate: this.ctx!.sampleRate,
        channels: 1,
        format: "pcm_f32",
        samples,
      };
      this.onChunk(chunk);
    };
    this.source.connect(this.processor);
    // ScriptProcessorNode only fires onaudioprocess while connected to a
    // destination -- but connecting straight to ctx.destination would loop
    // the local mic back to the local speakers (audible echo/feedback).
    // Route through a zero-gain node instead: fires the callback, produces
    // no sound.
    this.silentGain = this.ctx.createGain();
    this.silentGain.gain.value = 0;
    this.processor.connect(this.silentGain);
    this.silentGain.connect(this.ctx.destination);
  }

  stop(): void {
    this.processor?.disconnect();
    this.source?.disconnect();
    this.silentGain?.disconnect();
    void this.ctx?.close();
    this.processor = null;
    this.source = null;
    this.silentGain = null;
    this.ctx = null;
  }
}

/** Encode Float32 PCM to base64 for JSON WebSocket frames. */
export function float32ToBase64(samples: Float32Array): string {
  const bytes = new Uint8Array(samples.buffer, samples.byteOffset, samples.byteLength);
  if (typeof Buffer !== "undefined") {
    return Buffer.from(bytes).toString("base64");
  }
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]!);
  return btoa(binary);
}
