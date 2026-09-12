import { describe, expect, it, beforeEach } from "vitest";
import { RoomStore } from "../../../platform/src/roomStore.js";
import { MultiFeedRouter } from "../../../platform/src/feedRouter.js";
import { PerformanceOrchestrator } from "../performanceMode.js";

describe("Performance mode orchestration", () => {
  let store: RoomStore;
  let router: MultiFeedRouter;
  let orch: PerformanceOrchestrator;

  beforeEach(() => {
    store = new RoomStore();
    router = new MultiFeedRouter();
    store.createRoom({ mode: "performance", room_id: "perf1" });
    store.joinRoom({
      room_id: "perf1",
      display_name: "Lead",
      role: "lead",
      participant_id: "lead",
    });
    store.joinRoom({
      room_id: "perf1",
      display_name: "P2",
      role: "performer",
      participant_id: "p2",
    });
    store.joinRoom({
      room_id: "perf1",
      display_name: "Listener",
      role: "listener",
      participant_id: "aud",
    });
    const room = store.getRoom("perf1")!;
    router.syncFromRoomFeeds(room.feeds);
    orch = new PerformanceOrchestrator({
      roomStore: store,
      router,
      room_id: "perf1",
    });
  });

  it("routes cleaned mix to listeners only, not performers", () => {
    const received: { who: string; feed: string }[] = [];
    router.subscribe("aud", (c) => received.push({ who: "aud", feed: c.feed }));
    router.subscribe("lead", (c) =>
      received.push({ who: "lead", feed: c.feed }),
    );

    const result = orch.ingestPerformerFrame({
      participant_id: "lead",
      timestamp_ms: 100,
      samples: new Float32Array([0.5, -0.5, 0.25]),
    });

    expect(result.delivered_to).toContain("aud");
    expect(result.delivered_to).not.toContain("lead");
    expect(result.cleaned.metadata.cleaned).toBe(true);
    expect(received).toEqual([{ who: "aud", feed: "performance_mix" }]);
  });

  it("reassigns sync anchor when lead drops out", () => {
    orch.onSingingStarted("lead", 1000);
    orch.onSingingStarted("p2", 1500);
    expect(store.getRoom("perf1")?.performance?.sync_anchor_id).toBe("lead");

    const after = orch.onPerformerInactive("lead");
    expect(after.performance?.active_performer_ids).toEqual(["p2"]);
    expect(after.performance?.sync_anchor_id).toBe("p2");
  });

  it("picks longest-singing performer as new anchor", () => {
    orch.onSingingStarted("lead", 5000);
    orch.onSingingStarted("p2", 1000);
    // Force active set both; then drop lead — p2 has been singing longer.
    store.setActivePerformers("perf1", ["lead", "p2"]);
    store.setSyncAnchor("perf1", "lead");
    const after = orch.onPerformerInactive("lead");
    expect(after.performance?.sync_anchor_id).toBe("p2");
  });
});
