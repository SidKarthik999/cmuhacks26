/**
 * Platform HTTP + WebSocket API — the one real network boundary to the audio backend.
 */
import http from "node:http";
import { WebSocketServer, WebSocket } from "ws";
import { RoomStore } from "../src/roomStore.js";
import { MultiFeedRouter } from "../src/feedRouter.js";
import { mintLiveKitToken } from "./livekitTokens.js";
import { PerformanceOrchestrator } from "../../modes/performance/performanceMode.js";
import type { ParticipantRole, RoomMode } from "../../shared/bindings/room_state.js";
import { RoleValidationError } from "../src/roles.js";
import { float32ToBase64 } from "../src/audio/AudioTrackTap.js";

export interface PlatformApi {
  server: http.Server;
  store: RoomStore;
  router: MultiFeedRouter;
  orchestrators: Map<string, PerformanceOrchestrator>;
}

function readJson(req: http.IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      try {
        const raw = Buffer.concat(chunks).toString("utf8") || "{}";
        resolve(JSON.parse(raw));
      } catch (e) {
        reject(e);
      }
    });
    req.on("error", reject);
  });
}

function sendJson(
  res: http.ServerResponse,
  status: number,
  body: unknown,
): void {
  const data = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json",
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "content-type",
    "access-control-allow-methods": "GET,POST,OPTIONS",
  });
  res.end(data);
}

export function createPlatformApi(opts?: { port?: number }): PlatformApi {
  const store = new RoomStore();
  const router = new MultiFeedRouter();
  const orchestrators = new Map<string, PerformanceOrchestrator>();

  const server = http.createServer(async (req, res) => {
    if (!req.url || !req.method) {
      sendJson(res, 400, { error: "bad request" });
      return;
    }

    if (req.method === "OPTIONS") {
      sendJson(res, 204, {});
      return;
    }

    try {
      const url = new URL(req.url, "http://localhost");

      if (req.method === "GET" && url.pathname === "/health") {
        sendJson(res, 200, { ok: true, service: "platform" });
        return;
      }

      if (req.method === "GET" && url.pathname === "/rooms") {
        sendJson(res, 200, { rooms: store.listRooms() });
        return;
      }

      if (req.method === "POST" && url.pathname === "/rooms") {
        const body = (await readJson(req)) as { mode?: RoomMode; room_id?: string };
        if (!body.mode) {
          sendJson(res, 400, { error: "mode is required" });
          return;
        }
        const room = store.createRoom({ mode: body.mode, room_id: body.room_id });
        if (room.mode === "performance") {
          const orch = new PerformanceOrchestrator({
            roomStore: store,
            router,
            room_id: room.room_id,
          });
          orchestrators.set(room.room_id, orch);
        }
        sendJson(res, 201, room);
        return;
      }

      const roomMatch = url.pathname.match(/^\/rooms\/([^/]+)$/);
      if (req.method === "GET" && roomMatch) {
        const room = store.getRoom(decodeURIComponent(roomMatch[1]!));
        if (!room) {
          sendJson(res, 404, { error: "room not found" });
          return;
        }
        sendJson(res, 200, room);
        return;
      }

      const joinMatch = url.pathname.match(/^\/rooms\/([^/]+)\/join$/);
      if (req.method === "POST" && joinMatch) {
        const room_id = decodeURIComponent(joinMatch[1]!);
        const body = (await readJson(req)) as {
          display_name?: string;
          role?: ParticipantRole;
          participant_id?: string;
        };
        if (!body.display_name || !body.role) {
          sendJson(res, 400, { error: "display_name and role are required" });
          return;
        }
        const room = store.joinRoom({
          room_id,
          display_name: body.display_name,
          role: body.role,
          participant_id: body.participant_id,
        });
        router.syncFromRoomFeeds(room.feeds);
        const joined = room.participants[room.participants.length - 1]!;
        const token = mintLiveKitToken({
          room_id,
          participant_id: joined.participant_id,
          display_name: joined.display_name,
        });
        sendJson(res, 200, { room, participant: joined, livekit: token });
        return;
      }

      const leaveMatch = url.pathname.match(
        /^\/rooms\/([^/]+)\/participants\/([^/]+)\/leave$/,
      );
      if (req.method === "POST" && leaveMatch) {
        const room_id = decodeURIComponent(leaveMatch[1]!);
        const participant_id = decodeURIComponent(leaveMatch[2]!);
        const orch = orchestrators.get(room_id);
        if (orch) {
          try {
            orch.onPerformerInactive(participant_id);
          } catch {
            // listener leave, or already inactive — fine
          }
        }
        const room = store.leaveRoom(room_id, participant_id);
        router.syncFromRoomFeeds(room.feeds);
        sendJson(res, 200, room);
        return;
      }

      sendJson(res, 404, { error: "not found" });
    } catch (e) {
      if (e instanceof RoleValidationError) {
        sendJson(res, 400, { error: e.message });
        return;
      }
      console.error(e);
      sendJson(res, 500, { error: "internal error" });
    }
  });

  const wss = new WebSocketServer({ server, path: "/ws/audio" });

  wss.on("connection", (ws, req) => {
    const url = new URL(req.url ?? "", "http://localhost");
    const room_id = url.searchParams.get("room_id");
    const participant_id = url.searchParams.get("participant_id");
    const direction = url.searchParams.get("role") ?? "backend";

    if (!room_id) {
      ws.close(1008, "room_id required");
      return;
    }

    // Client / listener feed subscription over WS
    if (direction === "client" && participant_id) {
      const unsub = router.subscribe(participant_id, (chunk) => {
        if (ws.readyState !== WebSocket.OPEN) return;
        ws.send(
          JSON.stringify({
            type: "feed_chunk",
            feed: chunk.feed,
            room_id: chunk.room_id,
            seq: chunk.seq,
            timestamp_ms: chunk.timestamp_ms,
            sample_rate: chunk.sample_rate,
            channels: chunk.channels,
            format: chunk.format,
            pcm_base64: float32ToBase64(chunk.samples),
            meta: chunk.meta ?? {},
          }),
        );
      });
      ws.on("close", () => unsub());
      return;
    }

    // Audio backend: receives raw PCM, may push processed mix (Practice) —
    // Performance path can also be driven server-side via orchestrator below.
    ws.on("message", (data) => {
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(data.toString());
      } catch {
        return;
      }

      if (msg.type === "audio_chunk" && typeof msg.participant_id === "string") {
        const orch = orchestrators.get(room_id);
        if (!orch) return;
        const pcm_base64 = String(msg.pcm_base64 ?? "");
        const buf = Buffer.from(pcm_base64, "base64");
        const samples = new Float32Array(
          buf.buffer,
          buf.byteOffset,
          Math.floor(buf.byteLength / 4),
        );
        const result = orch.ingestPerformerFrame({
          participant_id: msg.participant_id,
          timestamp_ms: Number(msg.timestamp_ms ?? 0),
          samples,
          sample_rate: Number(msg.sample_rate ?? 48000),
        });
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(
            JSON.stringify({
              type: "ingest_ack",
              delivered_to: result.delivered_to,
              anchor_participant_id:
                result.room.performance?.sync_anchor_id ?? null,
            }),
          );
        }
      }

      if (msg.type === "performance_mix_chunk") {
        // Backend pushing a mix it computed itself (non-stub path later).
        const pcm_base64 = String(msg.pcm_base64 ?? "");
        const buf = Buffer.from(pcm_base64, "base64");
        const samples = new Float32Array(
          buf.buffer,
          buf.byteOffset,
          Math.floor(buf.byteLength / 4),
        );
        const room = store.getRoom(room_id);
        if (room) router.syncFromRoomFeeds(room.feeds);
        router.publish({
          feed: "performance_mix",
          room_id,
          timestamp_ms: Number(msg.timestamp_ms ?? 0),
          sample_rate: Number(msg.sample_rate ?? 48000),
          channels: 1,
          format: "pcm_f32",
          samples,
          meta: {
            anchor_participant_id: msg.anchor_participant_id,
            contributor_ids: msg.contributor_ids,
          },
        });
      }
    });
  });

  return { server, store, router, orchestrators };
}

export function startPlatformApi(port = Number(process.env.PORT ?? 8787)): PlatformApi {
  const api = createPlatformApi({ port });
  api.server.listen(port, () => {
    console.log(`platform api listening on :${port}`);
  });
  return api;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  startPlatformApi();
}
