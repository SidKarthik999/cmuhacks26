/**
 * LiveKit-backed CallSession.
 *
 * Requires LIVEKIT_URL + a join token from platform/api.
 * Falls back gracefully when livekit-client is unavailable at build time for tests.
 */
import type { AudioTrack } from "../../../shared/bindings/audio_track.js";
import type { CallSession, TrackHandler } from "./CallSession.js";

type LiveKitRoomLike = {
  connect(url: string, token: string): Promise<void>;
  disconnect(): Promise<void>;
  localParticipant: {
    setMicrophoneEnabled(enabled: boolean): Promise<unknown>;
    setCameraEnabled(enabled: boolean): Promise<unknown>;
    getTrackPublication(source: string):
      | { track?: { mediaStreamTrack?: MediaStreamTrack; sid?: string } }
      | undefined;
  };
  on(event: string, cb: (...args: unknown[]) => void): void;
  remoteParticipants: Map<
    string,
    {
      identity: string;
      audioTrackPublications: Map<
        string,
        { track?: { mediaStreamTrack?: MediaStreamTrack; sid?: string } }
      >;
    }
  >;
};

export interface LiveKitCallSessionOpts {
  room_id: string;
  participant_id: string;
  display_name: string;
  url: string;
  token: string;
  /** Injected for tests; defaults to dynamic import of livekit-client. */
  createRoom?: () => LiveKitRoomLike;
}

export class LiveKitCallSession implements CallSession {
  readonly room_id: string;
  readonly participant_id: string;
  private url: string;
  private token: string;
  private createRoom: () => Promise<LiveKitRoomLike>;
  private room: LiveKitRoomLike | null = null;
  private tracks = new Map<string, AudioTrack>();
  private media = new Map<string, MediaStreamTrack | null>();
  private handlers = new Set<TrackHandler>();

  constructor(opts: LiveKitCallSessionOpts) {
    this.room_id = opts.room_id;
    this.participant_id = opts.participant_id;
    this.url = opts.url;
    this.token = opts.token;
    this.createRoom =
      opts.createRoom != null
        ? async () => opts.createRoom!()
        : async () => {
            const lk = await import("livekit-client");
            return new lk.Room() as unknown as LiveKitRoomLike;
          };
  }

  async connect(): Promise<void> {
    this.room = await this.createRoom();
    this.room.on("trackSubscribed", (...args: unknown[]) => {
      const track = args[0] as {
        kind?: string;
        mediaStreamTrack?: MediaStreamTrack;
        sid?: string;
      };
      const participant = args[2] as { identity?: string };
      if (track?.kind !== "audio") return;
      this.registerTrack({
        track_id: track.sid ?? `trk_${participant.identity}_audio`,
        participant_id: participant.identity ?? "unknown",
        is_remote: true,
        media: track.mediaStreamTrack,
      });
    });
    await this.room.connect(this.url, this.token);
  }

  async disconnect(): Promise<void> {
    await this.room?.disconnect();
    this.room = null;
    this.tracks.clear();
    this.media.clear();
  }

  async publishLocalMedia(opts?: {
    audio?: boolean;
    video?: boolean;
  }): Promise<AudioTrack | null> {
    if (!this.room) throw new Error("LiveKitCallSession not connected");
    const audio = opts?.audio ?? true;
    const video = opts?.video ?? true;
    if (audio) await this.room.localParticipant.setMicrophoneEnabled(true);
    if (video) await this.room.localParticipant.setCameraEnabled(true);

    const pub = this.room.localParticipant.getTrackPublication("microphone");
    const mst = pub?.track?.mediaStreamTrack;
    const track = this.registerTrack({
      track_id: pub?.track?.sid ?? `trk_${this.participant_id}_audio`,
      participant_id: this.participant_id,
      is_remote: false,
      media: mst,
    });
    return track;
  }

  private registerTrack(input: {
    track_id: string;
    participant_id: string;
    is_remote: boolean;
    media?: MediaStreamTrack;
  }): AudioTrack {
    const track: AudioTrack = {
      version: 1,
      track_id: input.track_id,
      participant_id: input.participant_id,
      room_id: this.room_id,
      sample_rate: 48000,
      channels: 1,
      format: "pcm_f32",
      is_remote: input.is_remote,
      media_stream_track_id: input.media?.id ?? null,
    };
    this.tracks.set(track.track_id, track);
    this.media.set(track.track_id, input.media ?? null);
    for (const h of this.handlers) h(track, input.media);
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
}
