import "./styles.css";
import type { ParticipantRole, RoomMode, RoomState } from "../../shared/bindings/room_state.js";
import type { AudioTrack } from "../../shared/bindings/audio_track.js";
import type { CallSession } from "./call/CallSession.js";
import { MockCallSession } from "./call/MockCallSession.js";
import { LiveKitCallSession } from "./call/LiveKitCallSession.js";
import { AudioTrackTap, float32ToBase64 } from "./audio/AudioTrackTap.js";

// Empty string -> requests resolve relative to the page's own origin (via
// Vite's /rooms, /health, /ws proxy to the API server), which is what makes
// this work from a second device through a tunnel -- "localhost:8787"
// would mean THAT device's own localhost, not this machine's.
const API =
  (typeof import.meta !== "undefined" &&
    (import.meta as ImportMeta & { env?: { VITE_PLATFORM_API?: string } }).env
      ?.VITE_PLATFORM_API) ||
  "";

/** Same-origin WebSocket base -- works whether the page loads from a plain
 * LAN address or through an HTTPS tunnel, since it rides Vite's /ws proxy
 * (or whatever proxies the deployed page) rather than hardcoding a host. */
function wsBase(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}`;
}

/** Additive field platform/api/server.ts attaches to GET /rooms responses
 * -- whether a real backend/worker.py is attached to this room (vs. the
 * in-process TypeScript stub) and what it last reported. Not part of
 * room_state.schema.json (UI-facing only). */
type ProcessingStatus = {
  active: boolean;
  last_meta: Record<string, unknown> | null;
  last_feed: string | null;
  last_update_ms: number | null;
};
type RoomWithProcessing = RoomState & { processing?: ProcessingStatus };

/** One finished (or failed) "record a take" result -- Practice mode only.
 * See platform/api/server.ts's start_recording/stop_recording/
 * recording_ready handling and backend/worker.py's TakeRecorder. */
type RecordingMeta = {
  recording_id: string;
  signal_id: string | null;
  participant_ids: string[];
  duration_ms: number;
  created_at: number;
  status: "ready" | "failed";
  reason?: string;
};

type Session = {
  room: RoomWithProcessing;
  participant_id: string;
  display_name: string;
  role: ParticipantRole;
  livekit: { token: string; url: string; mock: boolean };
  call: CallSession;
  audioTap: AudioTrackTap | null;
  ingestSocket: WebSocket | null;
  feedSocket: WebSocket | null;
  playCtx: AudioContext | null;
  playCursor: Map<string, number>;
  isRecording: boolean;
  recordings: RecordingMeta[];
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

  const participant_id = joined.participant.participant_id;

  const call: CallSession = joined.livekit.mock
    ? new MockCallSession({ room_id, participant_id })
    : new LiveKitCallSession({
        room_id,
        participant_id,
        display_name,
        url: joined.livekit.url,
        token: joined.livekit.token,
      });

  await call.connect();
  // Listeners (Performance mode) don't sing/perform -- no mic or camera to
  // publish, they only receive the processed mix.
  const local =
    role === "listener" ? null : await call.publishLocalMedia({ audio: true, video: true });

  if (call instanceof MockCallSession) {
    // No real signaling in mock mode -- simulate other roster members'
    // tracks so the UI has something to show without LiveKit configured.
    for (const p of joined.room.participants) {
      if (p.participant_id !== participant_id) {
        call.simulateRemoteTrack(p.participant_id);
      }
    }
  }

  session = {
    room: joined.room,
    participant_id,
    display_name: joined.participant.display_name,
    role,
    livekit: joined.livekit,
    call,
    audioTap: null,
    ingestSocket: null,
    feedSocket: null,
    playCtx: null,
    playCursor: new Map(),
    isRecording: false,
    recordings: [],
  };

  call.onAudioTrack(() => renderCall());
  call.onVideoTrack(() => renderCall());

  if (local) startAudioTap(session, local);
  startFeedPlayback(session);

  renderCall();
}

/** Taps the local mic and forwards PCM to the real audio backend
 * (backend/worker.py, via platform/api/server.ts's role=processor fan-out)
 * as audio_chunk messages -- see docs/integration-contracts.md. */
function startAudioTap(s: Session, local: AudioTrack): void {
  const media = s.call.getMediaStreamTrack(local.track_id);
  if (!media) return; // e.g. mock mode, or camera/mic permission denied

  const ws = new WebSocket(
    `${wsBase()}/ws/audio?room_id=${encodeURIComponent(s.room.room_id)}&role=ingest`,
  );
  s.ingestSocket = ws;

  const tap = new AudioTrackTap(
    { track_id: local.track_id, participant_id: s.participant_id, room_id: s.room.room_id },
    (chunk) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      ws.send(
        JSON.stringify({
          type: "audio_chunk",
          room_id: chunk.room_id,
          participant_id: chunk.participant_id,
          track_id: chunk.track_id,
          seq: chunk.seq,
          timestamp_ms: chunk.timestamp_ms,
          sample_rate: chunk.sample_rate,
          channels: chunk.channels,
          format: chunk.format,
          pcm_base64: float32ToBase64(chunk.samples),
        }),
      );
    },
  );
  s.audioTap = tap;
  tap.start(media);
}

/** Subscribes to whichever processed feed(s) room.feeds assigns this
 * participant (Practice's "enhanced", Performance listeners'
 * "performance_mix") and plays them back. "raw_call" isn't included here --
 * that's the normal LiveKit call audio, already playing via each remote
 * participant's <video>/<audio> element. */
function startFeedPlayback(s: Session): void {
  const feeds = (s.room.feeds[s.participant_id] ?? []).filter((f) => f !== "raw_call");
  if (feeds.length === 0 || s.feedSocket) return;

  const AudioCtx =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  s.playCtx = s.playCtx ?? new AudioCtx();
  // Browsers can create an AudioContext in a "suspended" state (autoplay
  // policy) and never produce sound until explicitly resumed. This call is
  // still within the user-gesture chain from the Join/Create button click,
  // which is when resume() is most reliably allowed to succeed.
  void s.playCtx.resume();

  const ws = new WebSocket(
    `${wsBase()}/ws/audio?room_id=${encodeURIComponent(s.room.room_id)}&role=client&participant_id=${encodeURIComponent(s.participant_id)}`,
  );
  ws.onmessage = (ev) => {
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(String(ev.data));
    } catch {
      return;
    }
    if (msg.type === "feed_chunk" && typeof msg.feed === "string") {
      playFeedChunk(s, msg.feed, msg);
    }
  };
  s.feedSocket = ws;
}

/** Shared by live feed playback and finished-recording playback. */
function decodeBase64Pcm(b64: string): Float32Array<ArrayBuffer> {
  if (!b64) return new Float32Array(new ArrayBuffer(0));
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const view = new Float32Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 4));
  // Copy into a plain, concretely ArrayBuffer-backed array -- `view`
  // aliases `bytes.buffer` (typed ArrayBufferLike), which
  // AudioBuffer.copyToChannel doesn't accept directly.
  const out = new Float32Array(new ArrayBuffer(view.byteLength));
  out.set(view);
  return out;
}

function playFeedChunk(s: Session, feed: string, msg: Record<string, unknown>): void {
  const ctx = s.playCtx;
  if (!ctx) return;
  if (ctx.state === "suspended") void ctx.resume();
  const samples = decodeBase64Pcm(String(msg.pcm_base64 ?? ""));
  if (samples.length === 0) return;

  const sampleRate = Number(msg.sample_rate ?? 48000);
  const buffer = ctx.createBuffer(1, samples.length, sampleRate);
  buffer.copyToChannel(samples, 0);

  const src = ctx.createBufferSource();
  src.buffer = buffer;
  src.connect(ctx.destination);

  const now = ctx.currentTime;
  const cursor = s.playCursor.get(feed) ?? now;
  const startAt = Math.max(now, cursor);
  src.start(startAt);
  s.playCursor.set(feed, startAt + buffer.duration);
}

/** Record/stop a Practice-mode take: tells the real backend
 * (backend/worker.py's TakeRecorder) to start/stop accumulating both
 * peers' full cleaned audio, batch-align + mix it on stop, and save it as
 * a real Task 3 Signal. Sent over the same ingest socket audio_chunk
 * already flows through. */
function toggleRecording(s: Session): void {
  if (!s.ingestSocket || s.ingestSocket.readyState !== WebSocket.OPEN) return;
  const type = s.isRecording ? "stop_recording" : "start_recording";
  s.ingestSocket.send(JSON.stringify({ type, room_id: s.room.room_id }));
  s.isRecording = !s.isRecording;
  renderCall();
}

async function refreshRecordings(s: Session): Promise<void> {
  try {
    const result = await api<{ recordings: RecordingMeta[] }>(
      `/rooms/${encodeURIComponent(s.room.room_id)}/recordings`,
    );
    if (session !== s) return;
    s.recordings = result.recordings;
    renderCall();
  } catch {
    // transient network hiccup -- next poll will retry
  }
}

async function playRecording(s: Session, recording_id: string): Promise<void> {
  const AudioCtx =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  s.playCtx = s.playCtx ?? new AudioCtx();
  if (s.playCtx.state === "suspended") await s.playCtx.resume();

  const audio = await api<{ sample_rate: number; format: string; pcm_base64: string }>(
    `/rooms/${encodeURIComponent(s.room.room_id)}/recordings/${encodeURIComponent(recording_id)}`,
  );
  const samples = decodeBase64Pcm(audio.pcm_base64);
  if (samples.length === 0) return;

  const buffer = s.playCtx.createBuffer(1, samples.length, audio.sample_rate);
  buffer.copyToChannel(samples, 0);
  const src = s.playCtx.createBufferSource();
  src.buffer = buffer;
  src.connect(s.playCtx.destination);
  src.start();
}

/** Human-readable detail line for the processing-status badge -- which
 * feed the real backend last produced, and a couple of its meta fields
 * (e.g. "used_sync", "anchor_participant_id"), or how long ago it was
 * seen if it's gone quiet. */
function describeProcessingDetail(processing?: ProcessingStatus): string {
  if (!processing?.active) {
    return "no backend/worker.py attached to this room yet";
  }
  const parts: string[] = [];
  if (processing.last_feed) parts.push(`feed=${processing.last_feed}`);
  for (const [k, v] of Object.entries(processing.last_meta ?? {})) {
    if (v === null || v === undefined || v === "") continue;
    parts.push(`${k}=${Array.isArray(v) ? v.join(",") : v}`);
  }
  if (processing.last_update_ms) {
    const ageSec = Math.max(0, Math.round((Date.now() - processing.last_update_ms) / 1000));
    parts.push(`${ageSec}s ago`);
  }
  return parts.join(" · ");
}

function renderCall(): void {
  if (!session) return;
  const { room, participant_id, call, livekit } = session;
  const me = room.participants.find((p) => p.participant_id === participant_id);

  const audioTracks = call.listAudioTracks();
  const videoTracks = call.listVideoTracks();

  const tiles = room.participants.map((p) => {
    const isLocal = p.participant_id === participant_id;
    const videoInfo = videoTracks.find((v) => v.participant_id === p.participant_id);
    const audioInfo = audioTracks.find((a) => a.participant_id === p.participant_id);
    const videoMedia = videoInfo ? call.getMediaStreamTrack(videoInfo.track_id) : null;
    const audioMedia = audioInfo ? call.getMediaStreamTrack(audioInfo.track_id) : null;

    const videoEl = el("video", {
      autoplay: "",
      playsinline: "",
      className: "tile-video",
      ...(isLocal ? { muted: "" } : {}),
    }) as HTMLVideoElement;
    const mediaTracks: MediaStreamTrack[] = [];
    if (videoMedia) mediaTracks.push(videoMedia);
    // Never attach our own mic to our own tile -- would echo.
    if (audioMedia && !isLocal) mediaTracks.push(audioMedia);
    if (mediaTracks.length) videoEl.srcObject = new MediaStream(mediaTracks);

    return el("div", { className: "tile" }, [
      videoEl,
      el("div", {}, [
        el("strong", {}, [p.display_name]),
        " ",
        el("span", { className: "badge" }, [p.role]),
      ]),
      el("div", { className: "feeds" }, [
        `Feeds: ${(room.feeds[p.participant_id] ?? []).join(", ") || "—"}`,
      ]),
    ]);
  });

  const copyBtn = el("button", { className: "secondary", type: "button" }, ["Copy room code"]);
  copyBtn.addEventListener("click", () => {
    void navigator.clipboard?.writeText(room.room_id).then(
      () => {
        copyBtn.textContent = "Copied!";
        setTimeout(() => {
          copyBtn.textContent = "Copy room code";
        }, 1500);
      },
      () => undefined,
    );
  });

  const recordingsSidebar =
    room.mode === "practice"
      ? el("aside", { className: "recordings-sidebar" }, [
          el("h2", {}, ["Record a take"]),
          el("button", {
            className: session.isRecording ? "danger" : "primary",
            type: "button",
            onClick: () => session && toggleRecording(session),
          }, [session.isRecording ? "● Stop recording" : "● Record"]),
          el("div", { className: "recordings-list" }, [
            session.recordings.length === 0
              ? el("p", { className: "meta" }, ["No takes recorded yet."])
              : "",
            ...session.recordings
              .slice()
              .reverse()
              .map((r) => {
                if (r.status === "failed") {
                  return el("div", { className: "recording-row recording-failed" }, [
                    "⚠ ",
                    r.reason ?? "recording failed",
                  ]);
                }
                const playBtn = el("button", { className: "icon-button", type: "button", title: "Play this take" }, ["▶"]);
                playBtn.addEventListener("click", () => {
                  if (session) void playRecording(session, r.recording_id);
                });
                return el("div", { className: "recording-row" }, [
                  playBtn,
                  el("span", {}, [
                    `${(r.duration_ms / 1000).toFixed(1)}s · ${r.participant_ids.join(" + ")}`,
                  ]),
                ]);
              }),
          ]),
        ])
      : "";

  app.replaceChildren(
    el("section", { className: "call" }, [
      el("div", { className: "room-code-banner" }, [
        el("div", {}, [
          el("span", { className: "room-code-label" }, ["Room code — share with others to join:"]),
          el("code", { className: "room-code" }, [room.room_id]),
        ]),
        copyBtn,
      ]),
      el("div", { className: "call-header" }, [
        el("div", {}, [
          el("h1", {}, ["Ensemble"]),
          el("div", { className: "meta" }, [
            `${room.mode} · you are ${me?.role ?? "?"} (${participant_id})`,
          ]),
          el("div", { className: "meta" }, [
            livekit.mock
              ? "LiveKit credentials not set — using mock SFU session (no real audio/video)."
              : `LiveKit: ${livekit.url}`,
          ]),
          el("div", { className: "meta processing-meta" }, [
            el("span", {
              className: `badge ${room.processing?.active ? "badge-active" : "badge-inactive"}`,
            }, [room.processing?.active ? "Real processing: ON" : "Real processing: OFF"]),
            " ",
            describeProcessingDetail(room.processing),
          ]),
        ]),
        el("button", {
          className: "secondary",
          type: "button",
          onClick: () => void leave(),
        }, ["Leave"]),
      ]),
      el("div", { className: "call-layout" }, [
        el("div", { className: "call-main" }, [
          el("div", { className: "grid" }, tiles),
          el("div", { className: "tracks" }, [
            el("div", {}, ["Addressable audio tracks (Task 1 output):"]),
            ...audioTracks.map((t) =>
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
        recordingsSidebar,
      ]),
    ]),
  );
}

async function leave(): Promise<void> {
  if (!session) return;
  const { room, participant_id, call, audioTap, ingestSocket, feedSocket } = session;
  audioTap?.stop();
  ingestSocket?.close();
  feedSocket?.close();
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

// Poll room state while in call so joins from other tabs/devices appear.
setInterval(() => {
  if (!session) return;
  void api<RoomWithProcessing>(`/rooms/${encodeURIComponent(session.room.room_id)}`)
    .then((room) => {
      if (!session) return;
      session.room = room;
      if (session.call instanceof MockCallSession) {
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
      }
      renderCall();
      if (session.room.mode === "practice") void refreshRecordings(session);
    })
    .catch(() => undefined);
}, 2000);
