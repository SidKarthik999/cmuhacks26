import { hairline, label, scale, token, type Surface } from "../lib/canvas";
import type { ContourPoint, NoteRow } from "../types";

/* Pitch, in staff-like coordinates.
 *
 * The vertical axis is MIDI number, not hertz, because a semitone is a fixed
 * distance in MIDI and a wildly varying one in hertz — an octave at the
 * bottom of a bass line would be a fifth of the height of an octave at the
 * top of a soprano line, and the eye would read the same interval as two
 * different sizes. Gridlines land on note names for the same reason a stave
 * has lines: a contour floating in space tells you nothing.
 *
 * Unvoiced frames arrive as `null` and are drawn as gaps rather than
 * interpolated through. A line drawn across a breath is a lie about what the
 * singer did, and the gaps are diagnostically useful in themselves.
 */

const NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

export function midiName(midi: number): string {
  const m = Math.round(midi);
  return `${NAMES[((m % 12) + 12) % 12]}${Math.floor(m / 12) - 1}`;
}

export interface ContourOpts {
  /** A second contour, drawn dimmer — the teacher behind the student. */
  reference?: ContourPoint[];
  referenceColor?: string;
  color?: string;
  notes?: NoteRow[];
  durationMs?: number;
  /** Forces a shared vertical range so two charts can be compared. */
  range?: [number, number];
}

/**
 * A vertical range wide enough for the singing and narrow enough to read.
 *
 * Percentiles, not the extremes. A pitch tracker that drops an octave for
 * three frames puts a point twelve semitones below the phrase, and a range
 * taken from the minimum then squeezes a one-octave melody into the top
 * third of the panel with two thirds of empty space below it — the panel ends
 * up describing the error rather than the performance. Outliers still get
 * drawn; `drawContour` clamps them to the rail so they read as excursions.
 */
export function midiRange(
  series: ContourPoint[][],
  pad = 2.5,
): [number, number] {
  const live = series
    .flat()
    .map((p) => p.midi)
    .filter((m): m is number => m !== null)
    .sort((a, b) => a - b);
  if (live.length === 0) return [55, 75];
  const at = (q: number): number =>
    live[Math.min(Math.max(Math.round(q * (live.length - 1)), 0), live.length - 1)] ?? 0;
  let lo = at(0.02);
  let hi = at(0.98);
  // A phrase sitting on one note would otherwise get a zero-height axis and
  // every vibrato wobble would fill the panel.
  if (hi - lo < 5) {
    const mid = (hi + lo) / 2;
    lo = mid - 2.5;
    hi = mid + 2.5;
  }
  return [lo - pad, hi + pad];
}

export function drawContour(
  s: Surface,
  contour: ContourPoint[],
  opts: ContourOpts = {},
): void {
  const { ctx, width, height } = s;
  const pad = { l: 34, r: 8, t: 8, b: 16 };
  const plotW = width - pad.l - pad.r;
  const plotH = height - pad.t - pad.b;
  const [lo, hi] =
    opts.range ??
    midiRange(opts.reference ? [contour, opts.reference] : [contour]);
  const duration =
    opts.durationMs ??
    Math.max(contour.at(-1)?.t ?? 1000, opts.reference?.at(-1)?.t ?? 0, 1);

  const y = (midi: number): number =>
    pad.t + scale(Math.min(Math.max(midi, lo), hi), hi, lo, 0, plotH);
  const x = (t: number): number => pad.l + (t / duration) * plotW;

  // Stave: gridlines on musical degrees rather than every nth MIDI number,
  // brighter on C. Which degrees depends on how much range has to fit —
  // a two-octave chorale gets root, third and fifth; a five-semitone phrase
  // gets every note.
  const octaves = (hi - lo) / 12;
  const degrees =
    octaves > 2 ? [0] : octaves > 1.2 ? [0, 4, 7] : [0, 2, 4, 5, 7, 9, 11];
  for (let m = Math.ceil(lo); m <= hi; m += 1) {
    const pc = ((m % 12) + 12) % 12;
    if (!degrees.includes(pc)) continue;
    const isC = pc === 0;
    hairline(
      ctx,
      pad.l,
      y(m),
      width - pad.r,
      y(m),
      isC ? token("--rule-strong") : token("--rule"),
    );
    label(ctx, midiName(m), pad.l - 6, y(m), {
      align: "right",
      size: 8,
      color: isC ? token("--fg-200") : token("--fg-300"),
    });
  }

  // Note boxes from the quantiser, so the reader can see where the engine
  // decided one note ended and the next began.
  if (opts.notes) {
    ctx.save();
    for (const n of opts.notes) {
      const x0 = x(n.start_ms);
      const w = Math.max(x(n.start_ms + n.duration_ms) - x0, 1);
      const top = y(n.midi + 0.5);
      const boxH = y(n.midi - 0.5) - top;
      // Opaque, not alpha-blended. On a near-black canvas a half-transparent
      // dark teal lands within a couple of levels of the background and the
      // boxes simply are not there; the whole point of drawing them is that a
      // reader can see where the engine decided one note stopped.
      ctx.fillStyle = token("--cold-400");
      ctx.fillRect(x0, top, w, boxH);
      ctx.fillStyle = token("--cold-300");
      ctx.fillRect(x0, top, w, 1);
      ctx.fillRect(x0, top + boxH - 1, w, 1);
      ctx.fillRect(x0, top, 1, boxH);
    }
    ctx.globalAlpha = 0.9;
    for (const n of opts.notes) {
      if (n.duration_ms < duration / 30) continue;
      label(ctx, n.note, x(n.start_ms) + 3, y(n.midi + 0.5) - 7, {
        size: 8,
        color: token("--cold-100"),
      });
    }
    ctx.restore();
  }

  if (opts.reference) {
    stroke(ctx, opts.reference, x, y, opts.referenceColor ?? token("--fg-300"), 1.4, 0.75);
  }
  stroke(ctx, contour, x, y, opts.color ?? token("--cold-100"), 1.8, 1);

  hairline(ctx, pad.l, height - pad.b, width - pad.r, height - pad.b, token("--rule"));
  label(ctx, `${(duration / 1000).toFixed(1)}s`, width - pad.r, height - pad.b + 8, {
    align: "right",
    size: 8,
  });
}

function stroke(
  ctx: CanvasRenderingContext2D,
  contour: ContourPoint[],
  x: (t: number) => number,
  y: (m: number) => number,
  color: string,
  lineWidth: number,
  alpha: number,
): void {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.lineJoin = "round";
  ctx.globalAlpha = alpha;
  ctx.beginPath();
  let open = false;
  for (const p of contour) {
    if (p.midi === null) {
      open = false;
      continue;
    }
    const px = x(p.t);
    const py = y(p.midi);
    if (open) ctx.lineTo(px, py);
    else ctx.moveTo(px, py);
    open = true;
  }
  ctx.stroke();
  ctx.restore();
}

/* ------------------------------------------------------- deviation charts */

export interface SeriesOpts {
  /** Symmetric vertical half-range in the series' own unit. */
  span: number;
  unit: string;
  /** Shaded band marking "close enough", in the same unit. */
  tolerance?: number;
  color?: string;
  durationMs?: number;
}

/**
 * A signed deviation over time: cents sharp/flat, or decibels loud/quiet.
 * Zero is a hard line through the middle, because the whole question these
 * charts answer is "how far from zero, and in which direction".
 */
export function drawDeviation(
  s: Surface,
  series: { t: number; v: number | null }[],
  opts: SeriesOpts,
): void {
  const { ctx, width, height } = s;
  const pad = { l: 40, r: 8, t: 6, b: 14 };
  const plotH = height - pad.t - pad.b;
  const plotW = width - pad.l - pad.r;
  const duration = opts.durationMs ?? Math.max(series.at(-1)?.t ?? 1, 1);
  const live = series.filter((p) => p.v !== null) as { t: number; v: number }[];

  // Scale to the 95th percentile, not the maximum. A pitch tracker that slips
  // an octave for two frames produces a 1,200-cent reading, and scaling to it
  // squashes the 30 cents of real information into three pixels. Outliers are
  // still drawn — clipped to the edge and flagged — rather than dropped,
  // because "the tracker lost the note here" is worth seeing.
  const magnitudes = live.map((p) => Math.abs(p.v)).sort((a, b) => a - b);
  const p95 = magnitudes.length
    ? (magnitudes[Math.min(Math.floor(magnitudes.length * 0.95), magnitudes.length - 1)] ?? 0)
    : 0;
  const span = Math.max(opts.span, p95 * 1.2);

  const y = (v: number): number =>
    pad.t + scale(Math.min(Math.max(v, -span), span), span, -span, 0, plotH);
  const x = (t: number): number => pad.l + (t / duration) * plotW;

  if (opts.tolerance) {
    ctx.save();
    ctx.fillStyle = token("--cold-400");
    ctx.globalAlpha = 0.35;
    ctx.fillRect(pad.l, y(opts.tolerance), plotW, y(-opts.tolerance) - y(opts.tolerance));
    ctx.restore();
    label(ctx, `±${opts.tolerance} ${opts.unit}`, pad.l + 4, y(opts.tolerance) - 7, {
      align: "left",
      size: 8,
      color: token("--cold-300"),
    });
  }

  for (const v of [span, 0, -span]) {
    hairline(
      ctx,
      pad.l,
      y(v),
      width - pad.r,
      y(v),
      v === 0 ? token("--rule-strong") : token("--rule"),
    );
    label(
      ctx,
      v === 0 ? `0 ${opts.unit}` : `${v > 0 ? "+" : "−"}${Math.round(Math.abs(v))}`,
      pad.l - 6,
      y(v),
      { align: "right", size: 8, color: v === 0 ? token("--fg-200") : token("--fg-300") },
    );
  }

  ctx.save();
  ctx.strokeStyle = opts.color ?? token("--cold-100");
  ctx.lineWidth = 1.6;
  ctx.lineJoin = "round";
  ctx.beginPath();
  let open = false;
  for (const p of series) {
    if (p.v === null) {
      open = false;
      continue;
    }
    const px = x(p.t);
    const py = y(p.v);
    if (open) ctx.lineTo(px, py);
    else ctx.moveTo(px, py);
    open = true;
  }
  ctx.stroke();

  // Anything the axis had to clip gets a mark on the rail it ran off.
  ctx.fillStyle = token("--alert");
  let clipped = 0;
  for (const p of live) {
    if (Math.abs(p.v) <= span) continue;
    clipped += 1;
    ctx.fillRect(x(p.t) - 1, p.v > 0 ? pad.t : height - pad.b - 3, 2, 3);
  }
  ctx.restore();
  if (clipped > 0) {
    label(
      ctx,
      `${clipped} frame${clipped === 1 ? "" : "s"} beyond ±${Math.round(span)}`,
      width - pad.r,
      pad.t + 4,
      { align: "right", size: 8, color: token("--alert") },
    );
  }
}
