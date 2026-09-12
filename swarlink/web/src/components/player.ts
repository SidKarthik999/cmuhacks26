import { h } from "../lib/dom";
import { mount, repaint } from "../lib/canvas";
import { drawWave } from "../draw/waveform";
import { token } from "../lib/canvas";

/* Audio playback over a waveform.
 *
 * The product's whole claim in practice and perform mode is "listen to this
 * and hear that it is together", so the mixes have to be audible, not merely
 * plotted. Each player streams one WAV straight off the analysis endpoint and
 * draws a playhead across the same envelope the charts use, so what is heard
 * and what is drawn are the same object.
 *
 * A/B exists because the only convincing evidence for the correction is the
 * uncorrected version: switching between them mid-phrase, at the same
 * position, is what makes a 100 ms offset audible.
 */

export interface PlayerOpts {
  wave: number[];
  durationMs: number;
  url: string | null;
  title: string;
  /** The uncorrected counterpart, for A/B. */
  compare?: { wave: number[]; url: string | null; title: string };
  color?: string;
  marks?: number[];
}

export interface Player {
  el: HTMLElement;
  stop(): void;
  dispose(): void;
}

export function player(opts: PlayerOpts): Player {
  let showing: 0 | 1 = 0;
  let head: number | null = null;

  const audio = new Audio();
  audio.preload = "none";

  const canvas = h("canvas", { class: "player__wave" });
  const disposeCanvas = mount(
    canvas,
    (s) => {
      const active = showing === 0 ? opts.wave : (opts.compare?.wave ?? opts.wave);
      const ghost = showing === 0 ? opts.compare?.wave : opts.wave;
      drawWave(s, active, {
        color: showing === 0 ? (opts.color ?? token("--cold-200")) : token("--warm"),
        ghost,
        ghostColor: token("--fg-400"),
        durationMs: opts.durationMs,
        marks: opts.marks,
        playhead: head,
        ruler: true,
      });
    },
    { height: 104 },
  );

  const titleEl = h("span", { class: "player__title label" }, opts.title);
  const timeEl = h("span", { class: "player__time num" }, "0.00 s");

  const playBtn = h(
    "button",
    { class: "btn btn--primary", type: "button" },
    "play",
  ) as HTMLButtonElement;

  const sourceUrl = (): string | null =>
    showing === 0 ? opts.url : (opts.compare?.url ?? null);

  const load = (): void => {
    const url = sourceUrl();
    playBtn.disabled = url === null;
    if (url === null) {
      titleEl.textContent = `${opts.title} — audio unavailable offline`;
      return;
    }
    const wasPlaying = !audio.paused;
    const at = audio.currentTime;
    audio.src = url;
    audio.currentTime = 0;
    if (wasPlaying) {
      audio.addEventListener(
        "loadedmetadata",
        () => {
          audio.currentTime = Math.min(at, audio.duration || at);
          void audio.play();
        },
        { once: true },
      );
    }
  };

  playBtn.addEventListener("click", () => {
    if (audio.paused) {
      if (!audio.src) load();
      void audio.play();
    } else {
      audio.pause();
    }
  });

  audio.addEventListener("play", () => {
    playBtn.textContent = "pause";
  });
  audio.addEventListener("pause", () => {
    playBtn.textContent = "play";
  });
  audio.addEventListener("ended", () => {
    head = null;
    repaint(canvas);
  });
  audio.addEventListener("timeupdate", () => {
    const total = audio.duration || opts.durationMs / 1000;
    head = total > 0 ? audio.currentTime / total : null;
    timeEl.textContent = `${audio.currentTime.toFixed(2)} s`;
    repaint(canvas);
  });

  canvas.addEventListener("click", (e) => {
    const rect = canvas.getBoundingClientRect();
    const u = (e.clientX - rect.left) / rect.width;
    if (!audio.src) load();
    const seek = (): void => {
      audio.currentTime = u * (audio.duration || opts.durationMs / 1000);
    };
    if (Number.isFinite(audio.duration)) seek();
    else audio.addEventListener("loadedmetadata", seek, { once: true });
  });

  const toggle = opts.compare
    ? (h(
        "button",
        {
          class: "btn",
          type: "button",
          onclick: (e: Event) => {
            showing = showing === 0 ? 1 : 0;
            const btn = e.currentTarget as HTMLButtonElement;
            btn.textContent =
              showing === 0 ? `hear ${opts.compare?.title}` : `hear ${opts.title}`;
            btn.classList.toggle("btn--on", showing === 1);
            titleEl.textContent =
              showing === 0 ? opts.title : (opts.compare?.title ?? opts.title);
            load();
            repaint(canvas);
          },
        },
        `hear ${opts.compare.title}`,
      ) as HTMLButtonElement)
    : null;

  const el = h(
    "div",
    { class: "player" },
    h("header", { class: "player__head" }, titleEl, timeEl),
    canvas,
    h("div", { class: "player__controls" }, playBtn, toggle),
  );

  return {
    el,
    stop() {
      audio.pause();
    },
    dispose() {
      audio.pause();
      audio.src = "";
      disposeCanvas();
    },
  };
}
