import { describe, expect, it } from "vitest";
import { createPlatformApi } from "../api/server.js";
import type { AddressInfo } from "node:net";

async function withServer<T>(
  fn: (base: string) => Promise<T>,
): Promise<T> {
  const api = createPlatformApi();
  await new Promise<void>((resolve) => api.server.listen(0, resolve));
  const { port } = api.server.address() as AddressInfo;
  try {
    return await fn(`http://127.0.0.1:${port}`);
  } finally {
    await new Promise<void>((resolve, reject) =>
      api.server.close((err) => (err ? reject(err) : resolve())),
    );
  }
}

describe("platform API", () => {
  it("creates a room and joins with a LiveKit token payload", async () => {
    await withServer(async (base) => {
      const created = await fetch(`${base}/rooms`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ mode: "practice" }),
      }).then((r) => r.json());
      expect(created.mode).toBe("practice");

      const joined = await fetch(`${base}/rooms/${created.room_id}/join`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ display_name: "A", role: "peer" }),
      }).then((r) => r.json());

      expect(joined.participant.role).toBe("peer");
      expect(joined.livekit.token).toBeTruthy();
      expect(joined.room.feeds[joined.participant.participant_id]).toEqual([
        "raw_call",
        "enhanced",
      ]);
    });
  });

  it("rejects invalid join roles", async () => {
    await withServer(async (base) => {
      const created = await fetch(`${base}/rooms`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ mode: "teach", room_id: "teach1" }),
      }).then((r) => r.json());

      const res = await fetch(`${base}/rooms/${created.room_id}/join`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ display_name: "X", role: "listener" }),
      });
      expect(res.status).toBe(400);
    });
  });
});
