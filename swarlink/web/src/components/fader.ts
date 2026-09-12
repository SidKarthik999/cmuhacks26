import { h } from "../lib/dom";

/* Performer faders.
 *
 * The brief asks for manual control of every performer's volume, so these are
 * real: each one posts a gain in decibels to the remix endpoint, which re-sums
 * the already-corrected stems and hands back a new mix. Moving a fader does
 * not re-run the analysis — cleaning and alignment are the expensive part and
 * they do not depend on the gains.
 *
 * The scale is decibels rather than 0–100 because the underlying operation is
 * a multiplication in amplitude: −6 dB is half the amplitude wherever the
 * fader started, and a linear percentage would make the same drag mean
 * different things at different positions.
 */

export interface FaderBank {
  el: HTMLElement;
  values(): number[];
  set(index: number, db: number): void;
  reset(): void;
  setBusy(busy: boolean): void;
}

export interface FaderRow {
  name: string;
  part?: string | null;
  lead?: boolean;
  db: number;
}

export function faderBank(
  rows: FaderRow[],
  onChange: (gains: number[]) => void,
  opts: { min?: number; max?: number } = {},
): FaderBank {
  const min = opts.min ?? -24;
  const max = opts.max ?? 12;
  const base = rows.map((r) => r.db);
  const gains = [...base];
  const inputs: HTMLInputElement[] = [];
  const readouts: HTMLElement[] = [];

  const emit = (): void => onChange([...gains]);

  const strips = rows.map((row, i) => {
    const readout = h("span", { class: "fader__db num" }, fmt(gains[i] ?? 0));
    const input = h("input", {
      type: "range",
      min: String(min),
      max: String(max),
      step: "0.5",
      value: String(gains[i] ?? 0),
      "aria-label": `${row.name} volume in decibels`,
      oninput: (e: Event) => {
        const v = Number((e.target as HTMLInputElement).value);
        gains[i] = v;
        readout.textContent = fmt(v);
      },
      onchange: emit,
    }) as HTMLInputElement;
    inputs.push(input);
    readouts.push(readout);

    return h(
      "div",
      { class: `fader${row.lead ? " fader--lead" : ""}` },
      h(
        "div",
        { class: "fader__head" },
        h("span", { class: "fader__name" }, row.name),
        row.lead ? h("span", { class: "fader__tag label" }, "lead") : null,
      ),
      row.part ? h("span", { class: "fader__part label" }, row.part) : null,
      h("div", { class: "fader__track" }, input),
      h(
        "div",
        { class: "fader__foot" },
        h("span", { class: "label" }, `${min}`),
        readout,
        h("span", { class: "label" }, `+${max}`),
      ),
    );
  });

  const el = h(
    "div",
    { class: "faders" },
    h(
      "header",
      { class: "faders__head" },
      h("span", { class: "label" }, "performer faders"),
      h(
        "button",
        {
          class: "btn",
          type: "button",
          onclick: () => {
            reset();
            emit();
          },
        },
        "reset to matched",
      ),
    ),
    h("div", { class: "faders__row" }, ...strips),
    h(
      "p",
      { class: "faders__note" },
      "Zero is the level the engine matched each voice to. These are true " +
        "decibels: −6 halves the amplitude, +6 doubles it.",
    ),
  );

  function reset(): void {
    base.forEach((db, i) => {
      gains[i] = db;
      const input = inputs[i];
      const readout = readouts[i];
      if (input) input.value = String(db);
      if (readout) readout.textContent = fmt(db);
    });
  }

  return {
    el,
    values: () => [...gains],
    set(index, db) {
      if (index < 0 || index >= gains.length) return;
      gains[index] = db;
      const input = inputs[index];
      const readout = readouts[index];
      if (input) input.value = String(db);
      if (readout) readout.textContent = fmt(db);
    },
    reset,
    setBusy(busy) {
      el.classList.toggle("faders--busy", busy);
      for (const input of inputs) input.disabled = busy;
    },
  };
}

function fmt(db: number): string {
  return `${db > 0 ? "+" : ""}${db.toFixed(1)} dB`;
}
