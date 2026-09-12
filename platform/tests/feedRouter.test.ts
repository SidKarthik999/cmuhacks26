import { describe, expect, it } from "vitest";
import { MultiFeedRouter } from "../src/feedRouter.js";

describe("MultiFeedRouter", () => {
  it("delivers chunks only to subscribers of that feed", () => {
    const router = new MultiFeedRouter();
    router.setSubscriptions("performer", ["raw_call"]);
    router.setSubscriptions("listener", ["performance_mix"]);

    const got: string[] = [];
    router.subscribe("performer", (c) => got.push(`p:${c.feed}`));
    router.subscribe("listener", (c) => got.push(`l:${c.feed}`));

    router.publish({
      feed: "performance_mix",
      room_id: "r",
      timestamp_ms: 0,
      sample_rate: 48000,
      channels: 1,
      format: "pcm_f32",
      samples: new Float32Array([0.1, 0.2]),
    });
    router.publish({
      feed: "raw_call",
      room_id: "r",
      timestamp_ms: 1,
      sample_rate: 48000,
      channels: 1,
      format: "pcm_f32",
      samples: new Float32Array([0.3]),
    });

    expect(got).toEqual(["l:performance_mix", "p:raw_call"]);
  });

  it("syncs subscriptions from room.feeds", () => {
    const router = new MultiFeedRouter();
    router.syncFromRoomFeeds({
      a: ["raw_call", "enhanced"],
      b: ["performance_mix"],
    });
    expect(router.getSubscriptions("a")).toEqual(["raw_call", "enhanced"]);
    expect(router.getSubscriptions("b")).toEqual(["performance_mix"]);
  });
});
