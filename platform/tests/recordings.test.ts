import { describe, expect, it } from "vitest";
import { WebSocket } from "ws";
import { createPlatformApi } from "../api/server.js";
import type { AddressInfo } from "node:net";

async function withServer<T>(fn: (base: string, wsBase: string) => Promise<T>): Promise<T> {
  const api = createPlatformApi();
  await new Promise<void>((resolve) => api.server.listen(0, resolve));
  const { port } = api.server.address() as AddressInfo;
  try {
    return await fn(`http://127.0.0.1:${port}`, `ws://127.0.0.1:${port}`);
  } finally {
    await new Promise<void>((resolve, reject) =>
      api.server.close((err) => (err ? reject(err) : resolve())),
    );
  }
}

function waitForOpen(ws: WebSocket): Promise<void> {
  return new Promise((resolve) => ws.once("open", () => resolve()));
}

function waitForMessage(ws: WebSocket, predicate: (msg: any) => boolean, timeoutMs = 2000): Promise<any> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("timed out waiting for message")), timeoutMs);
    ws.on("message", (data) => {
      const msg = JSON.parse(data.toString());
      if (predicate(msg)) {
        clearTimeout(timer);
        resolve(msg);
      }
    });
  });
}

describe("record-a-take (Practice mode)", () => {
  it("forwards start/stop_recording to the attached processor and stores the result", async () => {
    await withServer(async (base, wsBase) => {
      const created = await fetch(`${base}/rooms`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ mode: "practice", room_id: "room_rec_test" }),
      }).then((r) => r.json());
      expect(created.mode).toBe("practice");

      // Stand in for backend/worker.py's role=processor connection.
      const processor = new WebSocket(`${wsBase}/ws/audio?room_id=room_rec_test&role=processor`);
      await waitForOpen(processor);

      const startPromise = waitForMessage(processor, (m) => m.type === "start_recording");

      // Stand in for the browser's ingest connection sending the control message.
      const ingest = new WebSocket(`${wsBase}/ws/audio?room_id=room_rec_test&role=ingest`);
      await waitForOpen(ingest);
      ingest.send(JSON.stringify({ type: "start_recording", room_id: "room_rec_test" }));

      await startPromise; // processor actually received it

      // Nothing recorded yet.
      const before = await fetch(`${base}/rooms/room_rec_test/recordings`).then((r) => r.json());
      expect(before.recordings).toEqual([]);

      const stopPromise = waitForMessage(processor, (m) => m.type === "stop_recording");
      ingest.send(JSON.stringify({ type: "stop_recording", room_id: "room_rec_test" }));
      await stopPromise;

      // Processor reports the finished, synced recording back to the server.
      processor.send(
        JSON.stringify({
          type: "recording_ready",
          room_id: "room_rec_test",
          recording_id: "rec_test_1",
          signal_id: "sig_test_1",
          participant_ids: ["alice", "bob"],
          duration_ms: 2500,
          sample_rate: 16000,
          format: "pcm_f32",
          pcm_base64: "AAAAAA==",
        }),
      );

      // Give the server a tick to process the message.
      await new Promise((r) => setTimeout(r, 100));

      const after = await fetch(`${base}/rooms/room_rec_test/recordings`).then((r) => r.json());
      expect(after.recordings).toHaveLength(1);
      expect(after.recordings[0]).toMatchObject({
        recording_id: "rec_test_1",
        signal_id: "sig_test_1",
        participant_ids: ["alice", "bob"],
        status: "ready",
      });

      const audio = await fetch(`${base}/rooms/room_rec_test/recordings/rec_test_1`).then((r) => r.json());
      expect(audio.sample_rate).toBe(16000);
      expect(audio.pcm_base64).toBe("AAAAAA==");

      const missing = await fetch(`${base}/rooms/room_rec_test/recordings/does_not_exist`);
      expect(missing.status).toBe(404);

      processor.close();
      ingest.close();
    });
  });
});
