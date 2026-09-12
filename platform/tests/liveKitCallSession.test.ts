import { describe, expect, it } from "vitest";
import { LiveKitCallSession } from "../src/call/LiveKitCallSession.js";

function fakeMediaStreamTrack(id: string): MediaStreamTrack {
  // Minimal stand-in -- LiveKitCallSession only reads/stores it, never
  // calls MediaStreamTrack methods directly.
  return { id } as unknown as MediaStreamTrack;
}

function makeFakeRoom() {
  const listeners = new Map<string, ((...args: unknown[]) => void)[]>();
  const publications = new Map<
    string,
    { track?: { mediaStreamTrack?: MediaStreamTrack; sid?: string } }
  >();

  const room = {
    connect: async () => {},
    disconnect: async () => {},
    localParticipant: {
      setMicrophoneEnabled: async () => {
        publications.set("microphone", {
          track: { mediaStreamTrack: fakeMediaStreamTrack("local-mic"), sid: "trk_local_mic" },
        });
      },
      setCameraEnabled: async () => {
        publications.set("camera", {
          track: { mediaStreamTrack: fakeMediaStreamTrack("local-cam"), sid: "trk_local_cam" },
        });
      },
      getTrackPublication: (source: string) => publications.get(source),
    },
    on: (event: string, cb: (...args: unknown[]) => void) => {
      const arr = listeners.get(event) ?? [];
      arr.push(cb);
      listeners.set(event, arr);
    },
    remoteParticipants: new Map(),
    // Test helper: fire a fake trackSubscribed event.
    _emitTrackSubscribed(track: unknown, publication: unknown, participant: unknown) {
      for (const cb of listeners.get("trackSubscribed") ?? []) cb(track, publication, participant);
    },
  };
  return room;
}

describe("LiveKitCallSession video support", () => {
  it("registers the local camera track alongside the mic on publishLocalMedia", async () => {
    const room = makeFakeRoom();
    const session = new LiveKitCallSession({
      room_id: "room1",
      participant_id: "me",
      display_name: "Me",
      url: "wss://example.livekit.cloud",
      token: "tok",
      createRoom: () => room,
    });

    await session.connect();
    const local = await session.publishLocalMedia();

    expect(local?.participant_id).toBe("me");
    expect(local?.is_remote).toBe(false);

    const videoTracks = session.listVideoTracks();
    expect(videoTracks).toHaveLength(1);
    expect(videoTracks[0]!.participant_id).toBe("me");
    expect(videoTracks[0]!.is_remote).toBe(false);

    const videoMedia = session.getMediaStreamTrack(videoTracks[0]!.track_id);
    expect(videoMedia?.id).toBe("local-cam");
  });

  it("registers a remote participant's subscribed video track", async () => {
    const room = makeFakeRoom();
    const session = new LiveKitCallSession({
      room_id: "room1",
      participant_id: "me",
      display_name: "Me",
      url: "wss://example.livekit.cloud",
      token: "tok",
      createRoom: () => room,
    });

    const seen: string[] = [];
    session.onVideoTrack((t) => seen.push(`${t.participant_id}:${t.is_remote}`));

    await session.connect();
    room._emitTrackSubscribed(
      { kind: "video", mediaStreamTrack: fakeMediaStreamTrack("remote-cam"), sid: "trk_remote_cam" },
      {},
      { identity: "other" },
    );

    expect(seen).toContain("other:true");
    const videoTracks = session.listVideoTracks();
    expect(videoTracks.some((t) => t.participant_id === "other" && t.is_remote)).toBe(true);
  });

  it("keeps audio and video tracks in separate listings but resolves both via getMediaStreamTrack", async () => {
    const room = makeFakeRoom();
    const session = new LiveKitCallSession({
      room_id: "room1",
      participant_id: "me",
      display_name: "Me",
      url: "wss://example.livekit.cloud",
      token: "tok",
      createRoom: () => room,
    });

    await session.connect();
    await session.publishLocalMedia();

    const audioTracks = session.listAudioTracks();
    const videoTracks = session.listVideoTracks();
    expect(audioTracks).toHaveLength(1);
    expect(videoTracks).toHaveLength(1);
    expect(audioTracks[0]!.track_id).not.toBe(videoTracks[0]!.track_id);

    expect(session.getMediaStreamTrack(audioTracks[0]!.track_id)?.id).toBe("local-mic");
    expect(session.getMediaStreamTrack(videoTracks[0]!.track_id)?.id).toBe("local-cam");
  });

  it("clears video tracks on disconnect", async () => {
    const room = makeFakeRoom();
    const session = new LiveKitCallSession({
      room_id: "room1",
      participant_id: "me",
      display_name: "Me",
      url: "wss://example.livekit.cloud",
      token: "tok",
      createRoom: () => room,
    });

    await session.connect();
    await session.publishLocalMedia();
    expect(session.listVideoTracks()).toHaveLength(1);

    await session.disconnect();
    expect(session.listVideoTracks()).toHaveLength(0);
  });
});
