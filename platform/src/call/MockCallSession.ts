/**
 * In-memory mock SFU session for tests and local UI without LiveKit credentials.
 * Still exposes per-participant AudioTrack handles as required by Task 1 outputs.
 */
import type { AudioTrack } from "../../../shared/bindings/audio_track.js";
import type { CallSession, TrackHandler, VideoTrackHandler, VideoTrackInfo } from "./CallSession.js";

export class MockCallSession implements CallSession {
  readonly room_id: string;
  readonly participant_id: string;
  private tracks = new Map<string, AudioTrack>();
  private media = new Map<string, MediaStreamTrack | null>();
  private handlers = new Set<TrackHandler>();
  private connected = false;

  constructor(opts: { room_id: string; participant_id: string }) {
    this.room_id = opts.room_id;
    this.participant_id = opts.participant_id;
  }

  async connect(): Promise<void> {
    this.connected = true;
  }

  async disconnect(): Promise<void> {
    this.tracks.clear();
    this.media.clear();
    this.connected = false;
  }

  async publishLocalMedia(): Promise<AudioTrack | null> {
    if (!this.connected) throw new Error("MockCallSession not connected");
    const track: AudioTrack = {
      version: 1,
      track_id: `trk_${this.participant_id}_audio`,
      participant_id: this.participant_id,
      room_id: this.room_id,
      sample_rate: 48000,
      channels: 1,
      format: "pcm_f32",
      is_remote: false,
      media_stream_track_id: null,
    };
    this.tracks.set(track.track_id, track);
    this.media.set(track.track_id, null);
    for (const h of this.handlers) h(track, undefined);
    return track;
  }

  /** Test helper: simulate a remote participant publishing audio. */
  simulateRemoteTrack(participant_id: string): AudioTrack {
    const track: AudioTrack = {
      version: 1,
      track_id: `trk_${participant_id}_audio`,
      participant_id,
      room_id: this.room_id,
      sample_rate: 48000,
      channels: 1,
      format: "pcm_f32",
      is_remote: true,
      media_stream_track_id: null,
    };
    this.tracks.set(track.track_id, track);
    this.media.set(track.track_id, null);
    for (const h of this.handlers) h(track, undefined);
    return track;
  }

  listAudioTracks(): AudioTrack[] {
    return [...this.tracks.values()];
  }

  onAudioTrack(handler: TrackHandler): () => void {
    this.handlers.add(handler);
    for (const t of this.tracks.values()) {
      handler(t, this.media.get(t.track_id) ?? undefined);
    }
    return () => this.handlers.delete(handler);
  }

  getMediaStreamTrack(track_id: string): MediaStreamTrack | null {
    return this.media.get(track_id) ?? null;
  }

  // No real WebRTC media in mock mode -- nothing to show as video.
  listVideoTracks(): VideoTrackInfo[] {
    return [];
  }

  onVideoTrack(_handler: VideoTrackHandler): () => void {
    return () => {};
  }
}
