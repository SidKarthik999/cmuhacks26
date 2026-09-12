import { clear, h, polish } from "../lib/dom";
import { createRoom, getRoom, runConcert, runDuet, runLesson, sendChat } from "../api";
import { logPanel } from "../components/log";
import { roster } from "../components/roster";
import { GLYPHS } from "../lib/glyphs";
import { teachPanel } from "./teach";
import { practicePanel } from "./practice";
import { performPanel } from "./perform";
import type { Ctx } from "../main";
import type { ConcertResult, DuetResult, LessonResult, Room, SceneDescription } from "../types";

/* The call.
 *
 * One shell for all three modes, because that is the actual claim: this is a
 * conferencing platform with the analysis inside it, not three analysis demos
 * with a call bolted on. The roster, the log, the clock and the telemetry are
 * the same objects whichever mode is running; only the middle column changes.
 *
 * The middle column is where a demo is usually thinnest, so it is built
 * around a scene: every reference scene has a known answer baked in by the
 * fixture generator, which means the panel can print what the engine measured
 * next to what was actually done to the audio.
 */

export type CallMode = "teach" | "practice" | "perform";

const MODE_PREFIX: Record<CallMode, string> = {
  teach: "lesson",
  practice: "duet",
  perform: "concert",
};

/* Which scene a mode opens on.
 *
 * Not the first alphabetically. A scale is the hardest case for the note
 * segmenter and therefore the most useful test, but it is the least
 * interesting thing to look at, and `duet:unison` happens to be a pair of
 * takes that were already nearly together — a true result and a dull one.
 * These three are the scenes where the feature has something to show. Every
 * other scene stays one click away in the picker, including the ones the
 * engine handles less well.
 */
const MODE_DEFAULT: Record<CallMode, string> = {
  teach: "lesson:phrase",
  practice: "duet:thirds",
  perform: "concert:band",
};

const MODE_COPY: Record<CallMode, { title: string; blurb: string; glyph: string }> = {
  teach: {
    title: "Teach",
    blurb:
      "The teacher's take is the reference. The student's is stretched onto " +
      "it and scored 70 / 30 on pitch and volume.",
    glyph: GLYPHS.trebleClef,
  },
  practice: {
    title: "Practice",
    blurb:
      "Two takes recorded apart, neither singer hearing the other. Levels " +
      "matched, one warped onto the other, mixed.",
    glyph: GLYPHS.beamedPair,
  },
  perform: {
    title: "Perform",
    blurb:
      "The lead is advanced by the delay the others sang behind. Every " +
      "performer keeps their own entry, and their own fader.",
    glyph: GLYPHS.fermata,
  },
};

export interface CallView {
  el: HTMLElement;
  dispose: () => void;
}

type Panel = { el: HTMLElement; dispose?: () => void };

export function callView(ctx: Ctx, mode: CallMode): CallView {
  const scenes = ctx.scenes.filter((s) => s.key.startsWith(MODE_PREFIX[mode]));
  const preferred = MODE_DEFAULT[mode];
  let sceneKey = scenes.some((s) => s.key === preferred)
    ? preferred
    : (scenes[0]?.key ?? "");
  let room: Room | null = null;
  let panel: Panel | null = null;
  let poll: ReturnType<typeof globalThis.setInterval>;
  let busy = false;

  const board = roster();
  const log = logPanel({
    onSend: (text) => {
      if (!room) return;
      const who = room.participants[0]?.name ?? "You";
      void sendChat(room.id, who, text).then(refreshRoom);
    },
  });

  const stage = h("div", { class: "stage" });
  const clockEl = h("span", { class: "call__clock num" }, "00:00");
  const telemetryEl = h("div", { class: "call__telemetry" });
  const runBtn = h("button", { class: "btn btn--primary", type: "button" }, "run analysis") as HTMLButtonElement;

  const picker = h(
    "select",
    {
      "aria-label": "Reference scene",
      onchange: (e: Event) => {
        sceneKey = (e.target as HTMLSelectElement).value;
        void openRoom();
      },
    },
    ...scenes.map((s) =>
      h("option", { value: s.key, selected: s.key === sceneKey }, s.title),
    ),
  ) as HTMLSelectElement;

  const sceneNote = h("p", { class: "call__scene prose" });

  const el = h(
    "div",
    { class: `call call--${mode}` },
    h(
      "header",
      { class: "call__bar" },
      h(
        "div",
        { class: "call__id" },
        h("a", { class: "call__home", href: "#/" }, "Swarlink"),
        h("span", { class: "call__room label" }, "connecting"),
      ),
      h(
        "nav",
        { class: "call__modes", "aria-label": "Modes" },
        ...(["teach", "practice", "perform"] as CallMode[]).map((m) =>
          h(
            "a",
            {
              class: `call__mode${m === mode ? " call__mode--on" : ""}`,
              href: `#/${m}`,
              "aria-current": m === mode ? "page" : undefined,
            },
            MODE_COPY[m].title,
          ),
        ),
      ),
      h(
        "div",
        { class: "call__right" },
        h("span", { class: "dot" }),
        clockEl,
        h("span", { class: "label" }, ctx.offline ? "captured" : "live engine"),
      ),
    ),
    h(
      "div",
      { class: "call__grid" },
      h(
        "aside",
        { class: "call__left" },
        h(
          "div",
          { class: "call__modehead" },
          h("span", { class: "call__modeglyph", html: MODE_COPY[mode].glyph, "aria-hidden": "true" }),
          h("h1", { class: "d-3" }, MODE_COPY[mode].title),
        ),
        h("p", { class: "prose call__blurb" }, MODE_COPY[mode].blurb),
        h("hr", { class: "rule" }),
        board.el,
        telemetryEl,
      ),
      h(
        "main",
        { class: "call__main", id: "main" },
        h(
          "div",
          { class: "call__controls" },
          h("span", { class: "label" }, "reference scene"),
          picker,
          runBtn,
        ),
        sceneNote,
        stage,
      ),
      h("aside", { class: "call__log" }, log.el),
    ),
  );

  const roomLabel = el.querySelector<HTMLElement>(".call__room");

  function refreshRoom(): Promise<void> {
    if (!room) return Promise.resolve();
    return getRoom(room.id)
      .then((next) => {
        room = next;
        paintRoom(next);
      })
      .catch(() => {
        /* a dropped poll is not worth a banner */
      });
  }

  function paintRoom(next: Room): void {
    if (roomLabel) roomLabel.textContent = `${next.name} · ${next.participants.length + next.audience_count} in the room`;
    board.render(next.participants, { audience: next.audience_count });
    log.render(next.log);
    polish(log.el);
    const t = next.telemetry;
    clear(telemetryEl);
    telemetryEl.append(
      h("span", { class: "label" }, "transport"),
      h(
        "ul",
        { class: "call__tel" },
        h("li", {}, h("b", { class: "num" }, String(t.uplink_streams)), " uplinks"),
        h("li", {}, h("b", { class: "num" }, String(t.mix_streams)), " mixed stream out"),
        h("li", {}, h("b", { class: "num" }, String(t.downlink_streams)), " receivers"),
        // In a two-person practice room the mesh comparison is two streams,
        // and printing it in alert red next to a paragraph about ten thousand
        // makes the transport panel look like it cannot count. The argument
        // only applies once a room is big enough for it to bite.
        t.participants >= 8 &&
          h(
            "li",
            { class: "call__tel--alert" },
            h("b", { class: "num" }, t.mesh_streams_avoided.toLocaleString("en-US")),
            " streams a mesh would need",
          ),
      ),
      t.participants >= 8
        ? h("p", { class: "call__telnote" }, t.note)
        : h(
            "p",
            { class: "call__telnote" },
            "Both takes are analysed server-side and returned as one mixed " +
              "programme, which is the same path a hundred-person concert uses.",
          ),
    );
    polish(telemetryEl);
  }

  function describeScene(scene: SceneDescription | undefined): void {
    if (!scene) {
      sceneNote.textContent = "";
      return;
    }
    clear(sceneNote);
    sceneNote.append(
      h("strong", {}, "What was done to this audio: "),
      scene.summary + ". ",
      h(
        "span",
        { class: "call__truth" },
        "Every figure below is the engine's own measurement — nothing here is read back from that description.",
      ),
    );
    polish(sceneNote);
  }

  function setBusy(next: boolean): void {
    busy = next;
    runBtn.disabled = next;
    picker.disabled = next;
    runBtn.textContent = next ? "analysing…" : "run analysis";
    stage.classList.toggle("stage--busy", next);
  }

  async function openRoom(): Promise<void> {
    setBusy(true);
    clear(stage);
    stage.appendChild(placeholder(mode));
    try {
      room = await createRoom(sceneKey, roomName(mode));
      paintRoom(room);
      describeScene(ctx.scenes.find((s) => s.key === sceneKey));
    } finally {
      setBusy(false);
    }
  }

  async function run(): Promise<void> {
    if (busy) return;
    setBusy(true);
    clear(stage);
    stage.appendChild(working());
    try {
      panel?.dispose?.();
      panel = await buildPanel(mode, sceneKey, room?.id);
      clear(stage);
      stage.appendChild(panel.el);
      polish(panel.el);
      await refreshRoom();
    } catch (err) {
      clear(stage);
      stage.appendChild(
        h(
          "div",
          { class: "stage__error" },
          h("span", { class: "label label--accent" }, "analysis failed"),
          h("p", { class: "prose" }, err instanceof Error ? err.message : String(err)),
        ),
      );
    } finally {
      setBusy(false);
    }
  }

  runBtn.addEventListener("click", () => void run());

  void openRoom().then(() => run());

  // The room clock is the only thing that has to tick, and it ticks once a
  // second rather than per frame.
  poll = globalThis.setInterval(() => {
    if (!room) return;
    const secs = Math.floor((room.elapsed_ms + 1000) / 1000);
    room.elapsed_ms += 1000;
    clockEl.textContent = `${String(Math.floor(secs / 60)).padStart(2, "0")}:${String(secs % 60).padStart(2, "0")}`;
  }, 1000);

  return {
    el,
    dispose() {
      globalThis.clearInterval(poll);
      board.dispose();
      panel?.dispose?.();
    },
  };
}

async function buildPanel(
  mode: CallMode,
  scene: string,
  roomId: string | undefined,
): Promise<Panel> {
  if (mode === "teach") {
    const result: LessonResult = await runLesson(scene, roomId);
    return teachPanel(result);
  }
  if (mode === "practice") {
    const result: DuetResult = await runDuet(scene, roomId);
    return practicePanel(result);
  }
  const result: ConcertResult = await runConcert(scene, roomId);
  return performPanel(result);
}

function roomName(mode: CallMode): string {
  return mode === "teach"
    ? "Studio 1 · lesson"
    : mode === "practice"
      ? "Practice room 2"
      : "Swarlink Hall";
}

function placeholder(mode: CallMode): HTMLElement {
  return h(
    "div",
    { class: "stage__empty" },
    h("span", { class: "label" }, "ready"),
    h("p", { class: "prose" }, MODE_COPY[mode].blurb),
  );
}

function working(): HTMLElement {
  return h(
    "div",
    { class: "stage__working" },
    h("span", { class: "label label--accent" }, "analysing"),
    h(
      "ul",
      { class: "stage__steps" },
      h("li", {}, "spectral cleaning, each voice separately"),
      h("li", {}, "pitch tracking and note segmentation"),
      h("li", {}, "offset, tempo and time-warp search"),
      h("li", {}, "scoring, weighting and mixdown"),
    ),
    h("div", { class: "stage__bar" }, h("i", {})),
  );
}
