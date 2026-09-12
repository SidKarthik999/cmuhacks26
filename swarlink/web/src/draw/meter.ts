import { hairline, label, scale, token, type Surface } from "../lib/canvas";
import type { Metric } from "../types";

/* Score and metric meters.
 *
 * There is no dial or gauge anywhere in this app. A needle sweeping an arc
 * looks impressive and communicates one number badly: the reader cannot tell
 * 78 from 83, and cannot see what either would have to change to improve.
 * These meters are linear, they carry their own anchor labels, and the
 * weighted bar shows the 70/30 split as two literal lengths so the split is
 * visible rather than asserted.
 */

export interface ScoreBarOpts {
  overall: number;
  pitch: number;
  volume: number;
  weighting: { pitch: number; volume: number };
}

/**
 * The weighted-score bar. Width is 100 points of score; the pitch band is
 * `weighting.pitch` of the bar and fills to `pitch/100` of its own share, so
 * the visible length of each band *is* its contribution to the total.
 */
export function drawScoreBar(s: Surface, opts: ScoreBarOpts): void {
  const { ctx, width, height } = s;
  const barTop = 18;
  const barH = Math.min(height - barTop - 22, 30);
  const split = width * opts.weighting.pitch;

  ctx.save();
  ctx.fillStyle = token("--ink-200");
  ctx.fillRect(0, barTop, width, barH);

  ctx.fillStyle = token("--cold-200");
  ctx.fillRect(0, barTop, split * (opts.pitch / 100), barH);
  ctx.fillStyle = token("--cold-300");
  ctx.fillRect(split, barTop, (width - split) * (opts.volume / 100), barH);
  ctx.restore();

  hairline(ctx, split, barTop - 5, split, barTop + barH + 5, token("--fg-000"));

  label(ctx, `pitch ${(opts.weighting.pitch * 100).toFixed(0)}%`, 0, barTop - 9, {
    color: token("--cold-100"),
  });
  label(
    ctx,
    `volume ${(opts.weighting.volume * 100).toFixed(0)}%`,
    split + 6,
    barTop - 9,
    { color: token("--cold-300") },
  );

  label(ctx, `${opts.pitch.toFixed(1)} / 100`, 0, barTop + barH + 12, {
    color: token("--fg-200"),
  });
  label(ctx, `${opts.volume.toFixed(1)} / 100`, split + 6, barTop + barH + 12, {
    color: token("--fg-200"),
  });

  // The overall mark: where the weighted total lands on the same axis.
  const x = Math.round(width * (opts.overall / 100));
  ctx.save();
  ctx.fillStyle = token("--fg-000");
  ctx.beginPath();
  ctx.moveTo(x, barTop + barH + 2);
  ctx.lineTo(x - 4, barTop + barH + 8);
  ctx.lineTo(x + 4, barTop + barH + 8);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

/**
 * A metric on its own anchor scale. `metric.anchors` come from the engine's
 * perceptual curve, so "12 cents" is placed against "a trained ear starts to
 * notice" rather than against an arbitrary 0–100.
 */
export function drawAnchored(s: Surface, metric: Metric): void {
  const { ctx, width, height } = s;
  if (metric.value === null || metric.anchors.length < 2) {
    label(ctx, "no reading", 0, height / 2, { color: token("--fg-300") });
    return;
  }
  const pad = { l: 0, r: 0, t: 14, b: 22 };
  const axisY = pad.t + (height - pad.t - pad.b) / 2;
  const values = metric.anchors.map((a) => a.value);
  const signed = Math.min(...values) < 0;
  const reach = Math.max(...values.map(Math.abs), Math.abs(metric.value));
  const lo = signed ? -reach : 0;
  const hi = reach;
  const x = (v: number): number => scale(v, lo, hi, 2, width - 2);

  hairline(ctx, x(lo), axisY, x(hi), axisY, token("--rule-strong"));

  // Every anchor gets a tick; only the labels that fit get drawn.
  //
  // These curves carry five or six anchors with phrases like "audible to
  // anyone" attached, and at card width three of them overlap into an
  // unreadable smear — which is worse than showing two, because a reader
  // cannot tell it is a collision rather than a rendering fault. Labels are
  // laid out left to right and any that would touch the previous one is
  // dropped, with the anchor nearest the actual value always kept.
  const nearest = metric.anchors.reduce(
    (best, a, i) =>
      Math.abs(a.value - (metric.value as number)) <
      Math.abs((metric.anchors[best]?.value ?? 0) - (metric.value as number))
        ? i
        : best,
    0,
  );

  ctx.save();
  ctx.font = `500 8px ${token("--mono") || "monospace"}`;
  const placed: { from: number; to: number }[] = [];
  const order = [nearest, ...metric.anchors.map((_a, i) => i).filter((i) => i !== nearest)];
  for (const i of order) {
    const anchor = metric.anchors[i];
    if (!anchor) continue;
    const ax = x(anchor.value);
    const w = ctx.measureText(anchor.label.toUpperCase()).width * 1.12 + 10;
    const align: CanvasTextAlign =
      ax > width - w / 2 ? "right" : ax < w / 2 ? "left" : "center";
    const from = align === "right" ? ax - w : align === "left" ? ax : ax - w / 2;
    const to = from + w;
    if (placed.some((p) => from < p.to && to > p.from)) continue;
    placed.push({ from, to });
    label(ctx, anchor.label, ax, axisY + 14, {
      align,
      size: 8,
      color: i === nearest ? token("--fg-200") : token("--fg-300"),
    });
  }
  ctx.restore();

  for (const anchor of metric.anchors) {
    hairline(ctx, x(anchor.value), axisY - 4, x(anchor.value), axisY + 4, token("--rule-strong"));
  }

  const vx = x(metric.value);
  ctx.save();
  ctx.fillStyle = tone(metric);
  ctx.fillRect(vx - 1.5, axisY - 11, 3, 22);
  ctx.restore();
  label(ctx, metric.display, vx, axisY - 18, {
    align: vx > width * 0.75 ? "right" : vx < width * 0.25 ? "left" : "center",
    size: 9,
    color: tone(metric),
  });
}

function tone(metric: Metric): string {
  if (metric.value === null) return token("--fg-300");
  const anchors = metric.anchors;
  if (anchors.length < 2) return token("--cold-100");
  const magnitude = Math.abs(metric.value);
  const reach = Math.max(...anchors.map((a) => Math.abs(a.value)));
  const fraction = metric.good === "high" ? 1 - magnitude / reach : magnitude / reach;
  if (fraction < 0.34) return token("--cold-100");
  if (fraction < 0.67) return token("--warm");
  return token("--alert");
}

/** Per-voice level bars, for the concert mixer. */
export function drawLevels(
  s: Surface,
  rows: { name: string; lufs: number; gainDb: number; lead: boolean }[],
): void {
  const { ctx, width, height } = s;
  const rowH = height / Math.max(rows.length, 1);
  const lo = -40;
  const hi = 0;

  // The name column is sized to the longest name actually present. Fixing it
  // at a guess means "Harmony (soprano)" runs under its own bar.
  ctx.save();
  ctx.font = `500 9px ${token("--mono") || "monospace"}`;
  const nameW =
    Math.min(
      Math.max(...rows.map((r) => ctx.measureText(r.name.toUpperCase()).width * 1.12)),
      width * 0.4,
    ) + 12;
  ctx.restore();

  rows.forEach((row, i) => {
    const y = i * rowH + rowH / 2;
    label(ctx, row.name, 0, y, {
      size: 9,
      color: row.lead ? token("--cold-100") : token("--fg-200"),
    });
    const x0 = nameW;
    const x1 = width - 78;
    hairline(ctx, x0, y, x1, y, token("--rule"));
    const level = scale(row.lufs + row.gainDb, lo, hi, x0, x1);
    ctx.save();
    ctx.fillStyle = row.lead ? token("--cold-100") : token("--cold-300");
    ctx.fillRect(x0, y - 3, Math.max(level - x0, 1), 6);
    ctx.restore();
    label(ctx, `${(row.lufs + row.gainDb).toFixed(1)} LUFS`, width, y, {
      align: "right",
      size: 8,
    });
  });
}
