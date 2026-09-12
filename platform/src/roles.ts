import type {
  FeedName,
  ParticipantRole,
  RoomMode,
  RoomParticipant,
  RoomState,
} from "../../shared/bindings/room_state.js";

export class RoleValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RoleValidationError";
  }
}

export function isPerformerRole(role: ParticipantRole): boolean {
  return role === "performer" || role === "lead";
}

/** Mode-specific role allowed at join time. */
export function assertRoleAllowedForMode(
  mode: RoomMode,
  role: ParticipantRole,
): void {
  const allowed: Record<RoomMode, ParticipantRole[]> = {
    teach: ["teacher", "student"],
    practice: ["peer"],
    performance: ["performer", "lead", "listener"],
  };
  if (!allowed[mode].includes(role)) {
    throw new RoleValidationError(
      `Role "${role}" is not valid for mode "${mode}". Allowed: ${allowed[mode].join(", ")}`,
    );
  }
}

/**
 * Validate room composition after a join/leave.
 * Teach: exactly 1 teacher + 1 student (room may be partially filled while joining).
 * Practice: at most 2 peers.
 * Performance: 2–4 performers (exactly one lead among them when ≥1 performer), any listeners.
 */
export function validateRoomComposition(
  mode: RoomMode,
  participants: RoomParticipant[],
  opts: { requireFull?: boolean } = {},
): void {
  const requireFull = opts.requireFull ?? false;
  const byRole = (r: ParticipantRole) =>
    participants.filter((p) => p.role === r);

  if (mode === "teach") {
    if (byRole("teacher").length > 1) {
      throw new RoleValidationError("Teach mode allows at most one teacher.");
    }
    if (byRole("student").length > 1) {
      throw new RoleValidationError("Teach mode allows at most one student.");
    }
    if (participants.length > 2) {
      throw new RoleValidationError("Teach mode allows at most two participants.");
    }
    if (requireFull) {
      if (byRole("teacher").length !== 1 || byRole("student").length !== 1) {
        throw new RoleValidationError(
          "Teach mode requires exactly one teacher and one student.",
        );
      }
    }
    return;
  }

  if (mode === "practice") {
    if (participants.some((p) => p.role !== "peer")) {
      throw new RoleValidationError("Practice mode participants must be peers.");
    }
    if (participants.length > 2) {
      throw new RoleValidationError("Practice mode allows exactly two participants.");
    }
    if (requireFull && participants.length !== 2) {
      throw new RoleValidationError("Practice mode requires exactly two participants.");
    }
    return;
  }

  // performance
  const performers = participants.filter((p) => isPerformerRole(p.role));
  const leads = byRole("lead");
  const listeners = byRole("listener");

  if (leads.length > 1) {
    throw new RoleValidationError("Performance mode allows at most one lead.");
  }
  if (performers.length > 4) {
    throw new RoleValidationError("Performance mode allows at most 4 performers.");
  }
  if (requireFull) {
    if (performers.length < 2) {
      throw new RoleValidationError(
        "Performance mode requires 2–4 performers (one lead).",
      );
    }
    if (leads.length !== 1) {
      throw new RoleValidationError(
        "Performance mode requires exactly one lead among performers.",
      );
    }
  } else if (performers.length >= 1 && leads.length !== 1) {
    // Once any performer has joined, lead must be designated.
    throw new RoleValidationError(
      "Performance mode requires exactly one lead among performers.",
    );
  }

  // listeners are unrestricted
  void listeners;
}

/** Default Task 9 feed subscriptions by mode + role. */
export function defaultFeedsForRole(
  mode: RoomMode,
  role: ParticipantRole,
): FeedName[] {
  if (mode === "practice") {
    // Practice: raw call + enhanced second feed for everyone.
    return ["raw_call", "enhanced"];
  }
  if (mode === "performance") {
    if (isPerformerRole(role)) {
      return ["raw_call"];
    }
    // listeners hear only the processed mix
    return ["performance_mix"];
  }
  // teach: everyone on the raw call (grading/notes are visual)
  return ["raw_call"];
}

export function defaultFeedsMap(
  mode: RoomMode,
  participants: RoomParticipant[],
): Record<string, FeedName[]> {
  const feeds: Record<string, FeedName[]> = {};
  for (const p of participants) {
    feeds[p.participant_id] = defaultFeedsForRole(mode, p.role);
  }
  return feeds;
}

export function buildPerformanceState(
  participants: RoomParticipant[],
): RoomState["performance"] {
  const lead = participants.find((p) => p.role === "lead");
  const performers = participants.filter((p) => isPerformerRole(p.role));
  return {
    lead_participant_id: lead?.participant_id ?? null,
    active_performer_ids: performers.map((p) => p.participant_id),
    sync_anchor_id: lead?.participant_id ?? null,
  };
}
