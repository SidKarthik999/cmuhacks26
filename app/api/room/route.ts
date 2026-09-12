import { env } from "cloudflare:workers";

type RoomRole = "host" | "performer" | "audience";
type SignalKind = "offer" | "answer" | "ice" | "bye";
let schemaReady: Promise<void> | null = null;

function database() {
  if (!env.DB) throw new Error("The room database is not connected.");
  return env.DB;
}

async function ensureSchema(db: D1Database) {
  if (!schemaReady) schemaReady = db.batch([
    db.prepare("CREATE TABLE IF NOT EXISTS rooms (id TEXT PRIMARY KEY NOT NULL, title TEXT NOT NULL, conductor_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'lobby', start_at INTEGER, audience_delay_ms INTEGER NOT NULL DEFAULT 420, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"),
    db.prepare("CREATE TABLE IF NOT EXISTS room_participants (id TEXT PRIMARY KEY NOT NULL, room_id TEXT NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'ready', latency_ms INTEGER NOT NULL DEFAULT 0, joined_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL)"),
    db.prepare("CREATE TABLE IF NOT EXISTS room_signals (id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, room_id TEXT NOT NULL, sender_id TEXT NOT NULL, recipient_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL)"),
    db.prepare("CREATE INDEX IF NOT EXISTS idx_room_participants_room_seen ON room_participants (room_id, last_seen_at)"),
    db.prepare("CREATE INDEX IF NOT EXISTS idx_room_signals_recipient_id ON room_signals (room_id, recipient_id, id)"),
    db.prepare("PRAGMA optimize"),
  ]).then(() => undefined).catch((error) => { schemaReady = null; throw error; });
  await schemaReady;
}

function cleanText(value: unknown, maximum: number) {
  return typeof value === "string" ? value.trim().slice(0, maximum) : "";
}

function roomCode() {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(6));
  return Array.from(bytes, (byte) => alphabet[byte % alphabet.length]).join("");
}

function jsonError(message: string, status = 400) {
  return Response.json({ error: message }, { status });
}

async function participantExists(db: D1Database, roomId: string, participantId: string) {
  return db.prepare("SELECT id, role FROM room_participants WHERE id = ? AND room_id = ?").bind(participantId, roomId).first<{ id: string; role: RoomRole }>();
}

export async function GET(request: Request) {
  try {
    const db = database();
    await ensureSchema(db);
    const url = new URL(request.url);
    const roomId = cleanText(url.searchParams.get("room"), 12).toUpperCase();
    const participantId = cleanText(url.searchParams.get("participant"), 64);
    const after = Math.max(0, Number(url.searchParams.get("after") ?? 0) || 0);
    if (!roomId || !participantId) return jsonError("room and participant are required");
    if (!await participantExists(db, roomId, participantId)) return jsonError("This participant is not in the room.", 403);
    const now = Date.now();
    await db.batch([
      db.prepare("UPDATE room_participants SET last_seen_at = ? WHERE id = ? AND room_id = ?").bind(now, participantId, roomId),
      db.prepare("DELETE FROM room_signals WHERE created_at < ?").bind(now - 10 * 60 * 1000),
    ]);
    const room = await db.prepare("SELECT id, title, conductor_id AS conductorId, status, start_at AS startAt, audience_delay_ms AS audienceDelayMs, created_at AS createdAt FROM rooms WHERE id = ?").bind(roomId).first();
    if (!room) return jsonError("Room not found.", 404);
    const participants = await db.prepare("SELECT id, name, role, state, latency_ms AS latencyMs, joined_at AS joinedAt, last_seen_at AS lastSeenAt FROM room_participants WHERE room_id = ? AND last_seen_at >= ? ORDER BY joined_at ASC LIMIT 32").bind(roomId, now - 25_000).all();
    const signals = await db.prepare("SELECT id, sender_id AS senderId, recipient_id AS recipientId, kind, payload, created_at AS createdAt FROM room_signals WHERE room_id = ? AND recipient_id = ? AND id > ? ORDER BY id ASC LIMIT 200").bind(roomId, participantId, after).all();
    return Response.json({ room, participants: participants.results, signals: signals.results, serverNow: now }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return jsonError(error instanceof Error ? error.message : "Room state failed.", 500);
  }
}

export async function POST(request: Request) {
  try {
    const db = database();
    await ensureSchema(db);
    const body = await request.json() as Record<string, unknown>;
    const action = cleanText(body.action, 24);
    const now = Date.now();

    if (action === "create") {
      const name = cleanText(body.name, 40) || "Conductor";
      const title = cleanText(body.title, 60) || "Swarlink Live Room";
      const participantId = crypto.randomUUID();
      let id = roomCode();
      for (let attempt = 0; attempt < 4; attempt += 1) {
        const exists = await db.prepare("SELECT id FROM rooms WHERE id = ?").bind(id).first();
        if (!exists) break;
        id = roomCode();
      }
      await db.batch([
        db.prepare("INSERT INTO rooms (id, title, conductor_id, status, start_at, audience_delay_ms, created_at, updated_at) VALUES (?, ?, ?, 'lobby', NULL, 420, ?, ?)").bind(id, title, participantId, now, now),
        db.prepare("INSERT INTO room_participants (id, room_id, name, role, state, latency_ms, joined_at, last_seen_at) VALUES (?, ?, ?, 'host', 'ready', 0, ?, ?)").bind(participantId, id, name, now, now),
      ]);
      return Response.json({ roomId: id, participantId, role: "host" }, { status: 201 });
    }

    if (action === "join") {
      const roomId = cleanText(body.roomId, 12).toUpperCase();
      const name = cleanText(body.name, 40) || "Guest";
      const role = cleanText(body.role, 16) as RoomRole;
      if (!roomId || !["performer", "audience"].includes(role)) return jsonError("A valid room and role are required.");
      const room = await db.prepare("SELECT id, status FROM rooms WHERE id = ?").bind(roomId).first<{ id: string; status: string }>();
      if (!room) return jsonError("That room does not exist.", 404);
      if (room.status === "ended") return jsonError("That performance has ended.", 409);
      const participantId = crypto.randomUUID();
      await db.prepare("INSERT INTO room_participants (id, room_id, name, role, state, latency_ms, joined_at, last_seen_at) VALUES (?, ?, ?, ?, 'ready', 0, ?, ?)").bind(participantId, roomId, name, role, now, now).run();
      return Response.json({ roomId, participantId, role }, { status: 201 });
    }

    const roomId = cleanText(body.roomId, 12).toUpperCase();
    const participantId = cleanText(body.participantId, 64);
    const participant = await participantExists(db, roomId, participantId);
    if (!participant) return jsonError("This participant is not in the room.", 403);

    if (action === "signal") {
      const recipientId = cleanText(body.recipientId, 64);
      const kind = cleanText(body.kind, 16) as SignalKind;
      const payload = typeof body.payload === "string" ? body.payload : JSON.stringify(body.payload ?? null);
      if (!recipientId || !["offer", "answer", "ice", "bye"].includes(kind) || payload.length > 65_536) return jsonError("Invalid signaling message.");
      if (!await participantExists(db, roomId, recipientId)) return jsonError("Signal recipient is not active.", 404);
      const result = await db.prepare("INSERT INTO room_signals (room_id, sender_id, recipient_id, kind, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)").bind(roomId, participantId, recipientId, kind, payload, now).run();
      return Response.json({ signalId: result.meta.last_row_id });
    }

    if (action === "heartbeat") {
      const latencyMs = Math.round(Math.max(0, Math.min(2000, Number(body.latencyMs) || 0)));
      const state = ["ready", "connecting", "live", "muted"].includes(cleanText(body.state, 16)) ? cleanText(body.state, 16) : "ready";
      await db.prepare("UPDATE room_participants SET latency_ms = ?, state = ?, last_seen_at = ? WHERE id = ? AND room_id = ?").bind(latencyMs, state, now, participantId, roomId).run();
      return Response.json({ ok: true, serverNow: now });
    }

    if (action === "control") {
      const room = await db.prepare("SELECT conductor_id AS conductorId FROM rooms WHERE id = ?").bind(roomId).first<{ conductorId: string }>();
      if (!room || room.conductorId !== participantId) return jsonError("Only the conductor can control this room.", 403);
      const status = cleanText(body.status, 16);
      if (!["lobby", "countdown", "live", "ended"].includes(status)) return jsonError("Invalid room state.");
      const startAt = status === "countdown" ? now + 6000 : status === "live" ? Number(body.startAt) || now : null;
      await db.prepare("UPDATE rooms SET status = ?, start_at = ?, updated_at = ? WHERE id = ?").bind(status, startAt, now, roomId).run();
      return Response.json({ ok: true, status, startAt, serverNow: now });
    }
    return jsonError("Unknown room action.");
  } catch (error) {
    return jsonError(error instanceof Error ? error.message : "Room request failed.", 500);
  }
}
