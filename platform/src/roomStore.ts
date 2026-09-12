import { randomUUID } from "node:crypto";
import type {
  FeedName,
  ParticipantRole,
  RoomMode,
  RoomParticipant,
  RoomState,
} from "../../shared/bindings/room_state.js";
import {
  assertRoleAllowedForMode,
  buildPerformanceState,
  defaultFeedsMap,
  isPerformerRole,
  RoleValidationError,
  validateRoomComposition,
} from "./roles.js";

export interface CreateRoomInput {
  mode: RoomMode;
  room_id?: string;
}

export interface JoinRoomInput {
  room_id: string;
  display_name: string;
  role: ParticipantRole;
  participant_id?: string;
}

export class RoomStore {
  private rooms = new Map<string, RoomState>();

  createRoom(input: CreateRoomInput): RoomState {
    const room_id = input.room_id ?? `room_${randomUUID().slice(0, 8)}`;
    if (this.rooms.has(room_id)) {
      throw new RoleValidationError(`Room ${room_id} already exists.`);
    }
    const room: RoomState = {
      version: 1,
      room_id,
      mode: input.mode,
      created_at: new Date().toISOString(),
      participants: [],
      feeds: {},
      performance:
        input.mode === "performance"
          ? {
              lead_participant_id: null,
              active_performer_ids: [],
              sync_anchor_id: null,
            }
          : null,
    };
    this.rooms.set(room_id, room);
    return structuredClone(room);
  }

  getRoom(room_id: string): RoomState | undefined {
    const room = this.rooms.get(room_id);
    return room ? structuredClone(room) : undefined;
  }

  listRooms(): RoomState[] {
    return [...this.rooms.values()].map((r) => structuredClone(r));
  }

  joinRoom(input: JoinRoomInput): RoomState {
    const room = this.rooms.get(input.room_id);
    if (!room) {
      throw new RoleValidationError(`Room ${input.room_id} not found.`);
    }

    assertRoleAllowedForMode(room.mode, input.role);

    const participant: RoomParticipant = {
      participant_id: input.participant_id ?? `part_${randomUUID().slice(0, 8)}`,
      display_name: input.display_name,
      role: input.role,
      joined_at: new Date().toISOString(),
    };

    if (room.participants.some((p) => p.participant_id === participant.participant_id)) {
      throw new RoleValidationError(
        `Participant ${participant.participant_id} already in room.`,
      );
    }

    const nextParticipants = [...room.participants, participant];
    validateRoomComposition(room.mode, nextParticipants, { requireFull: false });

    room.participants = nextParticipants;
    room.feeds = defaultFeedsMap(room.mode, room.participants);
    if (room.mode === "performance") {
      room.performance = buildPerformanceState(room.participants);
    }

    return structuredClone(room);
  }

  leaveRoom(room_id: string, participant_id: string): RoomState {
    const room = this.rooms.get(room_id);
    if (!room) {
      throw new RoleValidationError(`Room ${room_id} not found.`);
    }

    const leaving = room.participants.find(
      (p) => p.participant_id === participant_id,
    );
    const wasLead = leaving?.role === "lead";
    const prevAnchor = room.performance?.sync_anchor_id ?? null;

    room.participants = room.participants.filter(
      (p) => p.participant_id !== participant_id,
    );
    delete room.feeds[participant_id];
    room.feeds = defaultFeedsMap(room.mode, room.participants);

    if (room.mode === "performance") {
      const performance = buildPerformanceState(room.participants)!;
      if (wasLead || prevAnchor === participant_id) {
        performance.sync_anchor_id =
          performance.active_performer_ids[0] ?? null;
        if (wasLead) {
          performance.lead_participant_id = null;
        }
      }
      room.performance = performance;
    }

    return structuredClone(room);
  }

  setFeeds(
    room_id: string,
    participant_id: string,
    feeds: FeedName[],
  ): RoomState {
    const room = this.rooms.get(room_id);
    if (!room) {
      throw new RoleValidationError(`Room ${room_id} not found.`);
    }
    if (!room.participants.some((p) => p.participant_id === participant_id)) {
      throw new RoleValidationError(
        `Participant ${participant_id} not in room ${room_id}.`,
      );
    }
    room.feeds[participant_id] = [...feeds];
    return structuredClone(room);
  }

  setActivePerformers(room_id: string, active_ids: string[]): RoomState {
    const room = this.rooms.get(room_id);
    if (!room || room.mode !== "performance" || !room.performance) {
      throw new RoleValidationError(
        `Room ${room_id} is not a performance room.`,
      );
    }
    const performers = new Set(
      room.participants
        .filter((p) => isPerformerRole(p.role))
        .map((p) => p.participant_id),
    );
    for (const id of active_ids) {
      if (!performers.has(id)) {
        throw new RoleValidationError(
          `${id} is not a performer in room ${room_id}.`,
        );
      }
    }
    room.performance.active_performer_ids = [...active_ids];
    if (
      room.performance.sync_anchor_id &&
      !active_ids.includes(room.performance.sync_anchor_id)
    ) {
      room.performance.sync_anchor_id = active_ids[0] ?? null;
    }
    return structuredClone(room);
  }

  setSyncAnchor(room_id: string, anchor_id: string | null): RoomState {
    const room = this.rooms.get(room_id);
    if (!room || room.mode !== "performance" || !room.performance) {
      throw new RoleValidationError(
        `Room ${room_id} is not a performance room.`,
      );
    }
    if (
      anchor_id !== null &&
      !room.performance.active_performer_ids.includes(anchor_id)
    ) {
      throw new RoleValidationError(
        `Sync anchor ${anchor_id} must be an active performer.`,
      );
    }
    room.performance.sync_anchor_id = anchor_id;
    return structuredClone(room);
  }
}
