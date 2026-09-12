import { index, integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const rooms = sqliteTable("rooms", {
  id: text("id").primaryKey(),
  title: text("title").notNull(),
  conductorId: text("conductor_id").notNull(),
  status: text("status").notNull().default("lobby"),
  startAt: integer("start_at"),
  audienceDelayMs: integer("audience_delay_ms").notNull().default(420),
  createdAt: integer("created_at").notNull(),
  updatedAt: integer("updated_at").notNull(),
});

export const roomParticipants = sqliteTable("room_participants", {
  id: text("id").primaryKey(),
  roomId: text("room_id").notNull(),
  name: text("name").notNull(),
  role: text("role").notNull(),
  state: text("state").notNull().default("ready"),
  latencyMs: integer("latency_ms").notNull().default(0),
  joinedAt: integer("joined_at").notNull(),
  lastSeenAt: integer("last_seen_at").notNull(),
}, (table) => [index("idx_room_participants_room_seen").on(table.roomId, table.lastSeenAt)]);

export const roomSignals = sqliteTable("room_signals", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  roomId: text("room_id").notNull(),
  senderId: text("sender_id").notNull(),
  recipientId: text("recipient_id").notNull(),
  kind: text("kind").notNull(),
  payload: text("payload").notNull(),
  createdAt: integer("created_at").notNull(),
}, (table) => [index("idx_room_signals_recipient_id").on(table.roomId, table.recipientId, table.id)]);
