import { hairline, label, token, type Surface } from "../lib/canvas";

/* Waveform drawing.
 *
 * The backend sends a 900-point peak envelope, not raw samples, so each value
 * is the loudest absolute sample in its slice. Drawing it mirrored about the
 * centre line reproduces the familiar shape without shipping 88,000 floats
 * per take.
 */

export interface WaveOpts {
  color?: string;
  /** A second envelope drawn behind, for before/after comparisons. */
  ghost?: number[];
  ghostColor?: string;
  /** Vertical guide lines in milliseconds, e.g. note starts. */
  marks?: number[];
  durationMs?: number;
  /** 0..1 position of a playhead, or null. */
  playhead?: number | null;
  /** Draws a time ruler along the bottom. */
  ruler?: boolean;
  fillAlpha?: number;
}

const clamp01 = (v: number): number => (v < 0 ? 0 : v > 1 ? 1 : v);

export function drawWave(s: Surface, wave: number[], opts: WaveOpts = {}): void {
  const { ctx, width, height } = s;
  const rulerH = opts.ruler ? 14 : 0;
  const mid = (height - rulerH) / 2;
  const peak = Math.max(...wave, 0.0001);
  // Normalising to the take's own peak would hide the level difference
  // between two takes, which is half of what this product measures. A fixed
  // ceiling of 1.0 (full scale) keeps two waveforms comparable, but with a
  // gentle lift so a quiet take is still legible.
  const ceiling = Math.max(peak, 0.35);

  if (opts.ghost && opts.ghost.length > 1) {
    drawBody(
      ctx,
      opts.ghost,
      width,
      mid,
      mid - 2,
      ceiling,
      opts.ghostColor ?? token("--fg-400"),
      0.5,
    );
  }

  hairline(ctx, 0, mid, width, mid, token("--rule"));
  drawBody(
    ctx,
    wave,
    width,
    mid,
    mid - 2,
    ceiling,
    opts.color ?? token("--cold-200"),
    opts.fillAlpha ?? 0.9,
  );

  if (opts.marks && opts.durationMs) {
    ctx.save();
    ctx.strokeStyle = token("--fg-400");
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 3]);
    for (const t of opts.marks) {
      const x = Math.round((t / opts.durationMs) * width) + 0.5;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, mid * 2);
      ctx.stroke();
    }
    ctx.restore();
  }

  if (opts.playhead !== null && opts.playhead !== undefined) {
    const x = Math.round(clamp01(opts.playhead) * width) + 0.5;
    hairline(ctx, x, 0, x, mid * 2, token("--cold-100"));
  }

  if (opts.ruler && opts.durationMs) {
    drawRuler(s, opts.durationMs, mid * 2);
  }
}

function drawBody(
  ctx: CanvasRenderingContext2D,
  wave: number[],
  width: number,
  centerY: number,
  amp: number,
  ceiling: number,
  color: string,
  alpha: number,
): void {
  if (wave.length === 0) return;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.fillStyle = color;
  // One column per device-independent pixel, taking the max of whatever
  // envelope points land in it. Drawing 900 rects into a 400px box would
  // otherwise alias into a moire pattern.
  const cols = Math.max(Math.floor(width), 1);
  const per = wave.length / cols;
  for (let i = 0; i < cols; i += 1) {
    const from = Math.floor(i * per);
    const to = Math.max(Math.floor((i + 1) * per), from + 1);
    let v = 0;
    for (let j = from; j < to && j < wave.length; j += 1) {
      const sample = wave[j];
      if (sample !== undefined && sample > v) v = sample;
    }
    const half = Math.max((v / ceiling) * amp, 0.5);
    ctx.fillRect(i, centerY - half, 1, half * 2);
  }
  ctx.restore();
}

function drawRuler(s: Surface, durationMs: number, top: number): void {
  const { ctx, width } = s;
  hairline(ctx, 0, top, width, top, token("--rule"));
  const seconds = durationMs / 1000;
  const step = seconds > 12 ? 2 : seconds > 5 ? 1 : 0.5;
  for (let t = 0; t <= seconds + 1e-6; t += step) {
    const x = (t / seconds) * width;
    hairline(ctx, x, top, x, top + 4, token("--rule-strong"));
    if (t > 0) {
      label(ctx, `${t.toFixed(step < 1 ? 1 : 0)}s`, x + 3, top + 9, { size: 8 });
    }
  }
}

/** Two envelopes stacked in one box — the duet "are we together" picture. */
export function drawStacked(
  s: Surface,
  waves: { wave: number[]; color: string; name: string }[],
  durationMs: number,
): void {
  const { ctx, width, height } = s;
  const rows = waves.length;
  const rowH = (height - 14) / rows;
  const ceiling = Math.max(
    ...waves.flatMap((w) => w.wave),
    0.35,
  );
  waves.forEach((row, i) => {
    const mid = i * rowH + rowH / 2;
    hairline(ctx, 0, mid, width, mid, token("--rule"));
    drawBody(ctx, row.wave, width, mid, rowH / 2 - 6, ceiling, row.color, 0.85);
    label(ctx, row.name, 4, i * rowH + 8, { color: row.color, size: 8 });
  });
  drawRuler(s, durationMs, height - 14);
}
