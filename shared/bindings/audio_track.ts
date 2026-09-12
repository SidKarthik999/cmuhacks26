/**
 * Typed binding derived from shared/schemas/audio_track.schema.json.
 * Person A owns — do not hand-edit shape without updating the schema.
 */
export const AUDIO_TRACK_SCHEMA_VERSION = 1 as const;

export type AudioSampleRate = 16000 | 24000 | 44100 | 48000;
export type AudioFormat = "pcm_f32" | "pcm_s16" | "opus";

export interface AudioTrack {
  version?: typeof AUDIO_TRACK_SCHEMA_VERSION;
  track_id: string;
  participant_id: string;
  room_id: string;
  sample_rate: AudioSampleRate;
  channels: 1 | 2;
  format: AudioFormat;
  is_remote: boolean;
  media_stream_track_id?: string | null;
}
