import { describe, expect, it } from "vitest";
import { MockCallSession } from "../src/call/MockCallSession.js";

describe("MockCallSession (Task 1 audio track access)", () => {
  it("exposes individually addressable per-participant audio tracks", async () => {
    const session = new MockCallSession({
      room_id: "room1",
      participant_id: "me",
    });
    await session.connect();
    const local = await session.publishLocalMedia();
    const remote = session.simulateRemoteTrack("other");

    const tracks = session.listAudioTracks();
    expect(tracks).toHaveLength(2);
    expect(local?.participant_id).toBe("me");
    expect(remote.participant_id).toBe("other");
    expect(tracks.map((t) => t.participant_id).sort()).toEqual(["me", "other"]);
    expect(tracks.every((t) => t.room_id === "room1")).toBe(true);
    expect(tracks.every((t) => t.format === "pcm_f32")).toBe(true);
  });

  it("notifies handlers when tracks appear", async () => {
    const session = new MockCallSession({
      room_id: "room1",
      participant_id: "me",
    });
    const seen: string[] = [];
    session.onAudioTrack((t) => seen.push(t.track_id));
    await session.connect();
    await session.publishLocalMedia();
    session.simulateRemoteTrack("r1");
    expect(seen).toHaveLength(2);
  });
});
