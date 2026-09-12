import { append, h } from "../lib/dom";
import { mount, token } from "../lib/canvas";
import { drawWave } from "../draw/waveform";
import type { TakeView } from "../types";

type Child = Node | string | number | null | undefined | false;

/**
 * A numbered section.
 *
 * Panels get long — a lesson scorecard is six sections deep — and a long dark
 * page without landmarks is unreadable. The number is the landmark; the
 * subtitle is always a claim about the data, never a label.
 */
export function section(
  kicker: string,
  subtitle: string,
  ...children: Child[]
): HTMLElement {
  const el = h(
    "section",
    { class: "sec" },
    h(
      "header",
      { class: "sec__head" },
      h("span", { class: "label label--accent" }, kicker),
      h("h2", { class: "d-3 sec__title" }, subtitle),
    ),
  );
  const body = h("div", { class: "sec__body" });
  append(body, children);
  el.appendChild(body);
  return el;
}

/** One take's vital signs, with its envelope. */
export function takeStrip(take: TakeView, color?: string): HTMLElement {
  const canvas = h("canvas", {});
  mount(
    canvas,
    (s) =>
      drawWave(s, take.wave, {
        color: color ?? token("--cold-200"),
        durationMs: take.duration_ms,
        marks: take.notes.map((n) => n.start_ms),
      }),
    { height: 72 },
  );

  return h(
    "div",
    { class: "take" },
    h(
      "header",
      { class: "take__head" },
      h("span", { class: "take__name" }, take.name),
      h("span", { class: "take__role label" }, take.role),
    ),
    canvas,
    h(
      "dl",
      { class: "take__facts" },
      fact(`${(take.duration_ms / 1000).toFixed(2)} s`, "length"),
      fact(`${take.lufs.toFixed(1)}`, "LUFS"),
      fact(`${take.peak.toFixed(2)}`, "peak"),
      fact(`${take.voiced_percent.toFixed(0)}%`, "voiced"),
      fact(String(take.notes.length), "notes"),
    ),
    take.notes.length > 0
      ? h(
          "p",
          { class: "take__notes" },
          h("span", { class: "label" }, "written out"),
          h("span", { class: "take__seq num" }, take.notes.map((n) => n.note).join(" ")),
        )
      : null,
  );
}

function fact(value: string, key: string): HTMLElement {
  return h(
    "div",
    { class: "take__fact" },
    h("dt", { class: "num" }, value),
    h("dd", { class: "label" }, key),
  );
}
