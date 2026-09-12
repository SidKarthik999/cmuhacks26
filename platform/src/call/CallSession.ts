/**
 * Abstraction over the SFU-backed call so Task 2 can tap per-participant audio
 * without knowing about LiveKit/signaling.
 */
import type { AudioTrack } from "../../../shared/bindings/audio_track.js";

export type TrackHandler = (track: AudioTrack, media?: MediaStreamTrack) => void;

/**
 * Video tracks are UI-only (rendering a call, nothing more) -- unlike
 * AudioTrack, they're never sent over the platform<->audio-backend
 * boundary, so this deliberately isn't a shared/schemas contract type.
 */
export interface VideoTrackInfo {
  track_id: string;
  participant_id: string;
  is_remote: boolean;
}
export type VideoTrackHandler = (track: VideoTrackInfo, media?: MediaStreamTrack) => void;

export interface CallSession {
  readonly room_id: string;
  readonly participant_id: string;
  connect(): Promise<void>;
  disconnect(): Promise<void>;
  /** Publish local mic/camera. */
  publishLocalMedia(opts?: { audio?: boolean; video?: boolean }): Promise<AudioTrack | null>;
  /** Individually addressable remote+local audio tracks. */
  listAudioTracks(): AudioTrack[];
  onAudioTrack(handler: TrackHandler): () => void;
  /** Individually addressable remote+local video tracks (UI display only). */
  listVideoTracks(): VideoTrackInfo[];
  onVideoTrack(handler: VideoTrackHandler): () => void;
  /** Raw MediaStreamTrack for a known track_id (audio or video), when in-browser. */
  getMediaStreamTrack(track_id: string): MediaStreamTrack | null;
}

export interface CallSessionFactory {
  create(opts: {
    room_id: string;
    participant_id: string;
    display_name: string;
    token?: string;
    url?: string;
  }): CallSession;
}
