import "./styles.css";
import type { ParticipantRole, RoomMode, RoomState } from "../../shared/bindings/room_state.js";
import type { AudioTrack } from "../../shared/bindings/audio_track.js";
import { MockCallSession } from "./call/MockCallSession.js";

const API =
  (typeof import.meta !== "undefined" &&
    (import.meta as ImportMeta & { env?: { VITE_PLATFORM_API?: string } }).env
      ?.VITE_PLATFORM_API) ||
  "http://localhost:8787";

type Session = {
  room: RoomState;
  participant_id: string;
  display_name: string;
  livekit: { token: string; url: string; mock: boolean };
  call: MockCallSession;
  tracks: AudioTrack[];
};

const app = document.querySelector<HTMLDivElement>("#app")!;
let session: Session | null = null;

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Record<string, unknown> = {},
  children: (Node | string)[] = [],
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "className") node.className = String(v);
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v as EventListener);
    } else if (v !== undefined && v !== null) {
      node.setAttribute(k, String(v));
    }
  }
  for (const child of children) {
    node.append(child instanceof Node ? child : document.createTextNode(child));
  }
  return node;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error ?? res.statusText);
  return body as T;
}

function renderLanding(error?: string): void {
  app.replaceChildren(
    el("section", { className: "hero" }, [
      el("header", {}, [
        el("h1", { className: "brand" }, ["Ensemble"]),
        el("p", { className: "tagline" }, [
          "Join a room, pick a mode, and collaborate live — per-participant audio ready for the musical pipeline.",
        ]),
      ]),
      el("div", { className: "panel" }, [
        el("label", {}, [
          "Display name",
          el("input", { id: "name", value: "Musician", autocomplete: "nickname" }),
        ]),
        el("label", {}, [
          "Mode",
          el("select", { id: "mode" }, [
            el("option", { value: "performance" }, ["Performance"]),
            el("option", { value: "practice" }, ["Practice"]),
            el("option", { value: "teach" }, ["Teach"]),
          ]),
        ]),
        el("label", {}, [
          "Role",
          el("select", { id: "role" }, roleOptions("performance")),
        ]),
        el("label", {}, [
          "Room ID (optional to create; required to join)",
          el("input", { id: "room", placeholder: "leave blank to create" }),
        ]),
        el("div", { className: "actions" }, [
          el("button", { id: "create", type: "button" }, ["Create room & join"]),
          el("button", { id: "join", type: "button", className: "secondary" }, [
            "Join existing",
          ]),
        ]),
        error ? el("p", { className: "error" }, [error]) : "",
      ]),
    ]),
  );

  const modeSel = app.querySelector<HTMLSelectElement>("#mode")!;
  const roleSel = app.querySelector<HTMLSelectElement>("#role")!;
  modeSel.addEventListener("change", () => {
    roleSel.replaceChildren(...roleOptions(modeSel.value as RoomMode));
  });

  app.querySelector("#create")!.addEventListener("click", () => void createAndJoin());
  app.querySelector("#join")!.addEventListener("click", () => void joinExisting());
}

function roleOptions(mode: RoomMode): HTMLOptionElement[] {
  const roles: Record<RoomMode, ParticipantRole[]> = {
    teach: ["teacher", "student"],
    practice: ["peer"],
    performance: ["lead", "performer", "listener"],
  };
  return roles[mode].map((r) => el("option", { value: r }, [r]));
}

function formValues() {
  return {
    name: (app.querySelector("#name") as HTMLInputElement).value.trim() || "Musician",
    mode: (app.querySelector("#mode") as HTMLSelectElement).value as RoomMode,
    role: (app.querySelector("#role") as HTMLSelectElement).value as ParticipantRole,
    room: (app.querySelector("#room") as HTMLInputElement).value.trim(),
  };
}

async function createAndJoin(): Promise<void> {
  try {
    const v = formValues();
    const room = await api<RoomState>("/rooms", {
      method: "POST",
      body: JSON.stringify({ mode: v.mode, room_id: v.room || undefined }),
    });
    await enterRoom(room.room_id, v.name, v.role);
  } catch (e) {
    renderLanding(e instanceof Error ? e.message : String(e));
  }
}

async function joinExisting(): Promise<void> {
  try {
    const v = formValues();
    if (!v.room) throw new Error("Room ID is required to join.");
    await enterRoom(v.room, v.name, v.role);
  } catch (e) {
    renderLanding(e instanceof Error ? e.message : String(e));
  }
}

async function enterRoom(
  room_id: string,
  display_name: string,
  role: ParticipantRole,
): Promise<void> {
  const joined = await api<{
    room: RoomState;
    participant: { participant_id: string; display_name: string };
    livekit: { token: string; url: string; mock: boolean };
  }>(`/rooms/${encodeURIComponent(room_id)}/join`, {
    method: "POST",
    body: JSON.stringify({ display_name, role }),
  });

  const call = new MockCallSession({
    room_id,
    participant_id: joined.participant.participant_id,
  });
  await call.connect();
  const local = await call.publishLocalMedia();
  // Simulate other participants' tracks from room roster for demo/dev.
  for (const p of joined.room.participants) {
    if (p.participant_id !== joined.participant.participant_id) {
      call.simulateRemoteTrack(p.participant_id);
    }
  }

  session = {
    room: joined.room,
    participant_id: joined.participant.participant_id,
    display_name: joined.participant.display_name,
    livekit: joined.livekit,
    call,
    tracks: call.listAudioTracks(),
  };
  void local;
  renderCall();
}

function renderCall(): void {
  if (!session) return;
  const { room, participant_id, call, livekit } = session;
  const me = room.participants.find((p) => p.participant_id === participant_id);

  app.replaceChildren(
    el("section", { className: "call" }, [
      el("div", { className: "call-header" }, [
        el("div", {}, [
          el("h1", {}, ["Ensemble"]),
          el("div", { className: "meta" }, [
            `Room ${room.room_id} · ${room.mode} · you are ${me?.role ?? "?"} (${participant_id})`,
          ]),
          el("div", { className: "meta" }, [
            livekit.mock
              ? "LiveKit credentials not set — using mock SFU session (per-track audio still exposed)."
              : `LiveKit: ${livekit.url}`,
          ]),
        ]),
        el("button", {
          className: "secondary",
          type: "button",
          onClick: () => void leave(),
        }, ["Leave"]),
      ]),
      el("div", { className: "grid" }, [
        ...room.participants.map((p) =>
          el("div", { className: "tile" }, [
            el("div", {}, [
              el("strong", {}, [p.display_name]),
              " ",
              el("span", { className: "badge" }, [p.role]),
            ]),
            el("div", { className: "feeds" }, [
              `Feeds: ${(room.feeds[p.participant_id] ?? []).join(", ") || "—"}`,
            ]),
          ]),
        ),
      ]),
      el("div", { className: "tracks" }, [
        el("div", {}, ["Addressable audio tracks (Task 1 output):"]),
        ...call.listAudioTracks().map((t) =>
          el("div", {}, [
            el("code", {}, [
              `${t.track_id} ← ${t.participant_id} (${t.is_remote ? "remote" : "local"}, ${t.sample_rate}Hz ${t.format})`,
            ]),
          ]),
        ),
        room.mode === "performance"
          ? el("div", { className: "meta" }, [
              `Sync anchor: ${room.performance?.sync_anchor_id ?? "none"} · active performers: ${(room.performance?.active_performer_ids ?? []).join(", ") || "none"}`,
            ])
          : "",
      ]),
    ]),
  );
}

async function leave(): Promise<void> {
  if (!session) return;
  const { room, participant_id, call } = session;
  await call.disconnect();
  try {
    await api(
      `/rooms/${encodeURIComponent(room.room_id)}/participants/${encodeURIComponent(participant_id)}/leave`,
      { method: "POST", body: "{}" },
    );
  } catch {
    // ignore
  }
  session = null;
  renderLanding();
}

renderLanding();

// Poll room state while in call so joins from other tabs appear.
setInterval(() => {
  if (!session) return;
  void api<RoomState>(`/rooms/${encodeURIComponent(session.room.room_id)}`)
    .then((room) => {
      if (!session) return;
      session.room = room;
      for (const p of room.participants) {
        if (
          p.participant_id !== session.participant_id &&
          !session.call
            .listAudioTracks()
            .some((t) => t.participant_id === p.participant_id)
        ) {
          session.call.simulateRemoteTrack(p.participant_id);
        }
      }
      renderCall();
    })
    .catch(() => undefined);
}, 2000);
