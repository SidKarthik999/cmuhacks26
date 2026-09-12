/**
 * Typed binding derived from shared/schemas/room_state.schema.json.
 * Person A owns — do not hand-edit shape without updating the schema.
 */
export const ROOM_STATE_SCHEMA_VERSION = 1 as const;

export type RoomMode = "teach" | "practice" | "performance";

export type ParticipantRole =
  | "teacher"
  | "student"
  | "peer"
  | "performer"
  | "lead"
  | "listener";

export type FeedName = "raw_call" | "enhanced" | "performance_mix";

export interface RoomParticipant {
  participant_id: string;
  display_name: string;
  role: ParticipantRole;
  joined_at: string;
}

export interface PerformanceState {
  lead_participant_id: string | null;
  active_performer_ids: string[];
  sync_anchor_id: string | null;
}

export interface RoomState {
  version?: typeof ROOM_STATE_SCHEMA_VERSION;
  room_id: string;
  mode: RoomMode;
  created_at: string;
  participants: RoomParticipant[];
  feeds: Record<string, FeedName[]>;
  performance?: PerformanceState | null;
}
