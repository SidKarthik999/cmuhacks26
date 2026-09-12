import { describe, expect, it, beforeEach } from "vitest";
import { RoomStore } from "../src/roomStore.js";
import {
  RoleValidationError,
  assertRoleAllowedForMode,
  defaultFeedsForRole,
  validateRoomComposition,
} from "../src/roles.js";
import type { RoomParticipant } from "../../shared/bindings/room_state.js";

describe("role validation", () => {
  it("rejects invalid roles per mode", () => {
    expect(() => assertRoleAllowedForMode("teach", "listener")).toThrow(
      RoleValidationError,
    );
    expect(() => assertRoleAllowedForMode("practice", "teacher")).toThrow(
      RoleValidationError,
    );
  });

  it("allows partial teach room while joining", () => {
    expect(() =>
      validateRoomComposition("teach", [p("a", "teacher")], {
        requireFull: false,
      }),
    ).not.toThrow();
  });

  it("requires full teach composition when asked", () => {
    expect(() =>
      validateRoomComposition("teach", [p("a", "teacher")], {
        requireFull: true,
      }),
    ).toThrow(RoleValidationError);
  });

  it("caps practice at two peers", () => {
    expect(() =>
      validateRoomComposition("practice", [
        p("a", "peer"),
        p("b", "peer"),
        p("c", "peer"),
      ]),
    ).toThrow(/exactly two/);
  });

  it("requires one lead among performance performers", () => {
    expect(() =>
      validateRoomComposition("performance", [
        p("a", "performer"),
        p("b", "performer"),
      ]),
    ).toThrow(/lead/);
  });
});

describe("default feeds (Task 9)", () => {
  it("gives listeners performance_mix only", () => {
    expect(defaultFeedsForRole("performance", "listener")).toEqual([
      "performance_mix",
    ]);
  });

  it("gives performers raw_call only", () => {
    expect(defaultFeedsForRole("performance", "lead")).toEqual(["raw_call"]);
    expect(defaultFeedsForRole("performance", "performer")).toEqual([
      "raw_call",
    ]);
  });

  it("gives practice peers raw + enhanced", () => {
    expect(defaultFeedsForRole("practice", "peer")).toEqual([
      "raw_call",
      "enhanced",
    ]);
  });
});

describe("RoomStore", () => {
  let store: RoomStore;

  beforeEach(() => {
    store = new RoomStore();
  });

  it("creates and joins a performance room with correct feeds", () => {
    const room = store.createRoom({ mode: "performance", room_id: "r1" });
    expect(room.mode).toBe("performance");
    store.joinRoom({
      room_id: "r1",
      display_name: "L",
      role: "lead",
      participant_id: "lead1",
    });
    store.joinRoom({
      room_id: "r1",
      display_name: "P",
      role: "performer",
      participant_id: "p2",
    });
    const withListener = store.joinRoom({
      room_id: "r1",
      display_name: "A",
      role: "listener",
      participant_id: "aud",
    });
    expect(withListener.feeds.lead1).toEqual(["raw_call"]);
    expect(withListener.feeds.aud).toEqual(["performance_mix"]);
    expect(withListener.performance?.lead_participant_id).toBe("lead1");
    expect(withListener.performance?.sync_anchor_id).toBe("lead1");
  });

  it("rejects a second teacher in teach mode", () => {
    store.createRoom({ mode: "teach", room_id: "t1" });
    store.joinRoom({
      room_id: "t1",
      display_name: "T",
      role: "teacher",
      participant_id: "t",
    });
    expect(() =>
      store.joinRoom({
        room_id: "t1",
        display_name: "T2",
        role: "teacher",
        participant_id: "t2",
      }),
    ).toThrow(RoleValidationError);
  });
});

function p(id: string, role: RoomParticipant["role"]): RoomParticipant {
  return {
    participant_id: id,
    display_name: id,
    role,
    joined_at: new Date().toISOString(),
  };
}
