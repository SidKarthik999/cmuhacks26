import { h } from "../lib/dom";
import { mount, token, hairline, label as drawLabel, type Surface } from "../lib/canvas";
import { GLYPHS } from "../lib/glyphs";
import type { Participant } from "../types";

/* The video roster — the Zoom-shaped part of the product.
 *
 * These tiles are honest about what they are. There is no webcam in a
 * headless analysis engine, so instead of a fake photograph of a fake
 * violinist each tile renders that participant's own signal: a live bar field
 * driven by their reported level, seeded from their name so the same person
 * always looks the same, tinted by their part. It reads as a call at a glance
 * and it is showing real state — mute, gain, level, role — rather than
 * decoration pretending to be a feed.
 */

const GLYPH_FOR: Record<string, keyof typeof GLYPHS> = {
  teacher: "trebleClef",
  student: "quarterNote",
  lead: "fermata",
  performer: "beamedPair",
  singer: "beamedPair",
  audience: "staveMark",
};

function seed(name: string): number {
  let acc = 2166136261;
  for (let i = 0; i < name.length; i += 1) {
    acc ^= name.charCodeAt(i);
    acc = Math.imul(acc, 16777619);
  }
  return (acc >>> 0) / 4294967295;
}

export interface Tile {
  el: HTMLElement;
  update(p: Participant, speaking: boolean): void;
  dispose(): void;
}

export function tile(p: Participant, opts: { big?: boolean } = {}): Tile {
  let current = p;
  let speaking = false;
  const canvas = h("canvas", { class: "tile__feed" });
  const s0 = seed(p.name);

  const dispose = mount(
    canvas,
    (s, t) => drawFeed(s, t, current, speaking, s0),
    { animate: true, height: opts.big ? 260 : 132 },
  );

  const nameEl = h("span", { class: "tile__name" }, p.name);
  const partEl = h("span", { class: "tile__part label" }, p.part ?? p.role);
  const stateEl = h("span", { class: "tile__state label" });

  const el = h(
    "figure",
    { class: `tile${opts.big ? " tile--big" : ""}${p.role === "lead" ? " tile--lead" : ""}` },
    canvas,
    h(
      "span",
      { class: "tile__glyph", "aria-hidden": "true" },
      svgFor(p.role),
    ),
    h(
      "figcaption",
      { class: "tile__meta" },
      h("span", { class: "tile__id" }, nameEl, partEl),
      stateEl,
    ),
  );

  const update = (next: Participant, isSpeaking: boolean): void => {
    current = next;
    speaking = isSpeaking;
    nameEl.textContent = next.name;
    // The scene names performers "Lead (alto)", so the part is usually
    // already in the name; repeating it under it just makes the caption wrap.
    const part = next.part ?? next.role;
    partEl.textContent = next.name.toLowerCase().includes(part.toLowerCase())
      ? next.role
      : part;
    stateEl.textContent = next.muted
      ? "muted"
      : `${next.level_db.toFixed(0)} dB${next.gain_db ? ` · ${next.gain_db > 0 ? "+" : ""}${next.gain_db.toFixed(0)}` : ""}`;
    el.classList.toggle("tile--muted", next.muted);
    el.classList.toggle("tile--speaking", isSpeaking && !next.muted);
  };
  update(p, false);

  return { el, update, dispose };
}

function svgFor(role: string): SVGElement {
  const name = GLYPH_FOR[role] ?? "micMark";
  const wrap = document.createElement("div");
  wrap.innerHTML = GLYPHS[name];
  return wrap.firstElementChild as SVGElement;
}

function drawFeed(
  s: Surface,
  time: number,
  p: Participant,
  speaking: boolean,
  s0: number,
): void {
  const { ctx, width, height } = s;

  ctx.fillStyle = token("--ink-100");
  ctx.fillRect(0, 0, width, height);

  // A faint reference stave, so even an idle tile says "music".
  for (let i = 1; i <= 5; i += 1) {
    hairline(ctx, 0, (height * i) / 6, width, (height * i) / 6, token("--rule"));
  }

  const energy = p.muted ? 0.06 : Math.min(Math.max((p.level_db + 46) / 46, 0.08), 1);
  const active = speaking && !p.muted ? 1 : 0.42;
  const bars = Math.max(Math.floor(width / 6), 8);
  const mid = height * 0.56;

  ctx.save();
  ctx.fillStyle = p.role === "lead" ? token("--cold-100") : token("--cold-200");
  ctx.globalAlpha = 0.2 + 0.7 * active;
  for (let i = 0; i < bars; i += 1) {
    const u = i / bars;
    // Two incommensurate sines plus the name seed: enough variation that no
    // two tiles pulse in lockstep, cheap enough to run on eight canvases.
    const wobble =
      Math.sin(time * 2.1 + u * 9 + s0 * 31) * 0.5 +
      Math.sin(time * 3.7 - u * 14 + s0 * 12) * 0.3 +
      Math.sin(u * 27 + s0 * 40) * 0.2;
    const amp = Math.max(Math.abs(wobble) * energy, 0.02) * height * 0.34;
    ctx.fillRect(Math.round(u * width) + 1, mid - amp, 3, amp * 2);
  }
  ctx.restore();

  if (p.muted) {
    drawLabel(ctx, "muted", width / 2, height / 2, {
      align: "center",
      size: 10,
      color: token("--alert"),
    });
  }
}

/* -------------------------------------------------------------- the grid */

export interface Roster {
  el: HTMLElement;
  render(
    participants: Participant[],
    opts?: { speaking?: string | null; audience?: number },
  ): void;
  dispose(): void;
}

export function roster(): Roster {
  const grid = h("div", { class: "roster__grid" });
  const audienceEl = h("div", { class: "roster__audience" });
  const el = h("div", { class: "roster" }, grid, audienceEl);
  const tiles = new Map<string, Tile>();

  return {
    el,
    render(participants, view = {}) {
      const seen = new Set<string>();
      participants.forEach((p) => {
        seen.add(p.name);
        let t = tiles.get(p.name);
        if (!t) {
          // Only a solo occupant gets the large tile. Promoting the first of
          // two made a teacher and a student render at different sizes, which
          // reads as a layout fault rather than as emphasis.
          t = tile(p, { big: participants.length === 1 });
          tiles.set(p.name, t);
          grid.appendChild(t.el);
        }
        t.update(p, view.speaking === p.name);
      });
      for (const [name, t] of tiles) {
        if (seen.has(name)) continue;
        t.dispose();
        t.el.remove();
        tiles.delete(name);
      }
      // Two columns at most. The roster lives in a 280px rail, and a third
      // column leaves 90px per tile — not enough for "Harmony (soprano)",
      // which then truncates to "H..". Five performers in two columns leaves
      // one tile alone on the last row, which is a better outcome than five
      // tiles nobody can read.
      grid.style.setProperty("--cols", participants.length <= 1 ? "1" : "2");

      const audience = view.audience ?? 0;
      audienceEl.hidden = audience <= 0;
      if (audience > 0) {
        audienceEl.replaceChildren(
          h("span", { class: "label" }, "audience"),
          h("span", { class: "roster__count num" }, String(audience)),
          h(
            "span",
            { class: "roster__dots", "aria-hidden": "true" },
            ...Array.from({ length: Math.min(audience, 60) }, () => h("i", {})),
          ),
          h(
            "span",
            { class: "roster__note" },
            `listening on the mixed downlink — ${audience} receivers, one stream each`,
          ),
        );
      }
    },
    dispose() {
      for (const t of tiles.values()) t.dispose();
      tiles.clear();
    },
  };
}
