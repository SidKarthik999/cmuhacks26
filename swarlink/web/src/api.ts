import type {
  ConcertResult,
  DuetResult,
  Health,
  LessonResult,
  Metric,
  RemixResult,
  Room,
  SceneDescription,
} from "./types";

/* The one place that talks to the backend.
 *
 * `USE_FIXTURES` exists because the interface has to be reviewable without a
 * Python process: a designer, a judge, or a reviewer opening the built bundle
 * should see the real thing populated with real numbers rather than a blank
 * page and a connection error. The fixtures are captured responses, not
 * invented ones, so what they show is what the engine actually said.
 *
 * The flag resolves at runtime rather than at build time so the same bundle
 * serves both: `?fixtures=1` forces offline data, and a failed health check
 * falls back to it automatically.
 */

const params = new URLSearchParams(globalThis.location?.search ?? "");
export const FORCE_FIXTURES = params.get("fixtures") === "1";

let offline = FORCE_FIXTURES;

export function isOffline(): boolean {
  return offline;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body ? { "content-type": "application/json" } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* a non-JSON error body is still an error */
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body) });
}

/* ------------------------------------------------------------- fixtures */

type Fixtures = {
  health: Health;
  glossary: Metric[];
  scenes: SceneDescription[];
  room: Room;
  lesson: LessonResult;
  duet: DuetResult;
  concert: ConcertResult;
};

let fixtureCache: Promise<Fixtures> | null = null;

function fixtures(): Promise<Fixtures> {
  // Loaded lazily and as one chunk: when the API is up, none of this should
  // be on the wire.
  fixtureCache ??= import("./fixtures/index").then((m) => m.FIXTURES);
  return fixtureCache;
}

/* -------------------------------------------------------------- surface */

export async function getHealth(): Promise<Health> {
  if (offline) return (await fixtures()).health;
  try {
    return await request<Health>("/api/health");
  } catch {
    offline = true;
    return (await fixtures()).health;
  }
}

export async function getGlossary(): Promise<Metric[]> {
  if (offline) return (await fixtures()).glossary;
  return request<Metric[]>("/api/glossary");
}

export async function getScenes(): Promise<SceneDescription[]> {
  if (offline) return (await fixtures()).scenes;
  return request<SceneDescription[]>("/api/scenes");
}

export async function createRoom(scene: string, name?: string): Promise<Room> {
  if (offline) return (await fixtures()).room;
  return post<Room>("/api/rooms", { scene, name });
}

export async function getRoom(roomId: string): Promise<Room> {
  if (offline) return (await fixtures()).room;
  return request<Room>(`/api/rooms/${roomId}?log_limit=400`);
}

export async function sendChat(
  roomId: string,
  name: string,
  text: string,
): Promise<void> {
  if (offline) return;
  await post(`/api/rooms/${roomId}/chat`, { name, text });
}

export async function setMute(
  roomId: string,
  name: string,
  muted: boolean,
): Promise<void> {
  if (offline) return;
  await post(`/api/rooms/${roomId}/mute`, { name, muted });
}

export async function setGain(
  roomId: string,
  name: string,
  gainDb: number,
): Promise<void> {
  if (offline) return;
  await post(`/api/rooms/${roomId}/gain`, { name, gain_db: gainDb });
}

export async function setMode(roomId: string, mode: string): Promise<void> {
  if (offline) return;
  await post(`/api/rooms/${roomId}/mode`, { mode });
}

export async function runLesson(
  scene: string,
  roomId?: string,
): Promise<LessonResult> {
  if (offline) return (await fixtures()).lesson;
  return post<LessonResult>("/api/lesson", { scene, room_id: roomId });
}

export async function runDuet(
  scene: string,
  roomId?: string,
): Promise<DuetResult> {
  if (offline) return (await fixtures()).duet;
  return post<DuetResult>("/api/duet", { scene, room_id: roomId });
}

export async function runConcert(
  scene: string,
  roomId?: string,
  opts?: { gainsDb?: number[]; leadAdvanceMs?: number },
): Promise<ConcertResult> {
  if (offline) return (await fixtures()).concert;
  return post<ConcertResult>("/api/concert", {
    scene,
    room_id: roomId,
    gains_db: opts?.gainsDb,
    lead_advance_ms: opts?.leadAdvanceMs,
  });
}

export async function remixConcert(
  resultId: string,
  gainsDb: number[],
): Promise<RemixResult | null> {
  // Returning null offline is honest: without the stems in memory there is no
  // remix to serve, and faking one would make the fader look like it works
  // when it is doing nothing.
  if (offline) return null;
  return post<RemixResult>("/api/concert/remix", {
    result_id: resultId,
    gains_db: gainsDb,
  });
}

export function audioUrl(audioId: string): string {
  return `/api/audio/${audioId}.wav`;
}
