import { hairline, label, scale, token, type Surface } from "../lib/canvas";
import type { ConcertTiming, TimingWindow } from "../types";

/* The concert entry chart — the one picture this whole product exists for.
 *
 * One lane per performer on a shared millisecond axis. The hollow tick is
 * where that performer actually came in, measured against the lead's
 * original entry; the filled tick is where they sit after correction,
 * measured again on the corrected audio rather than read off the plan. Both
 * ticks are measurements, which is why the filled ones do not land in a
 * perfect column: if they did, the chart would be drawing the intention
 * instead of the result.
 */

export function drawSpread(
  s: Surface,
  timing: ConcertTiming,
  leadName: string,
): void {
  const { ctx, width, height } = s;
  const rows = timing.measured_delays;
  const nameW = 104;
  const pad = { t: 26, b: 22, r: 56 };
  const x0 = nameW;
  const x1 = width - pad.r;
  const lanes = Math.max(rows.length, 1);
  const laneH = (height - pad.t - pad.b) / lanes;

  const reach = Math.max(
    ...rows.map((r) => Math.abs(r.delay_ms)),
    ...rows.map((r) => Math.abs(r.residual_ms)),
    timing.nominal_delay_ms,
    60,
  );
  const lo = -reach * 0.18;
  const hi = reach * 1.12;
  const x = (ms: number): number => scale(ms, lo, hi, x0, x1);

  // Axis and the two reference verticals: the lead's original entry (zero)
  // and the nominal 0.1 s the feature is specified against.
  hairline(ctx, x0, height - pad.b, x1, height - pad.b, token("--rule"));
  for (const tick of axisTicks(hi)) {
    const tx = x(tick);
    hairline(ctx, tx, pad.t - 6, tx, height - pad.b, token("--rule"));
    label(ctx, `${tick} ms`, tx, height - pad.b + 9, { align: "center", size: 8 });
  }

  ctx.save();
  ctx.strokeStyle = token("--cold-300");
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  const nx = Math.round(x(timing.nominal_delay_ms)) + 0.5;
  ctx.beginPath();
  ctx.moveTo(nx, pad.t - 6);
  ctx.lineTo(nx, height - pad.b);
  ctx.stroke();
  ctx.restore();
  label(ctx, "nominal 100 ms", nx + 4, pad.t - 14, { color: token("--cold-300"), size: 8 });

  hairline(ctx, Math.round(x(0)) + 0.5, pad.t - 6, Math.round(x(0)) + 0.5, height - pad.b, token("--rule-strong"));
  label(ctx, "lead", x(0) - 4, pad.t - 14, { align: "right", size: 8, color: token("--fg-200") });

  rows.forEach((row, i) => {
    const y = pad.t + i * laneH + laneH / 2;
    const isLead = row.name === leadName;
    label(ctx, row.name, 0, y, {
      size: 9,
      color: isLead ? token("--cold-100") : token("--fg-100"),
    });

    // The travel: from measured entry to corrected entry.
    const bx = x(row.delay_ms);
    const ax = x(row.residual_ms);
    ctx.save();
    ctx.strokeStyle = token("--rule-strong");
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(bx, y);
    ctx.lineTo(ax, y);
    ctx.stroke();

    ctx.strokeStyle = token("--fg-300");
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.arc(bx, y, 3.5, 0, Math.PI * 2);
    ctx.stroke();

    ctx.fillStyle = token("--cold-100");
    ctx.beginPath();
    ctx.arc(ax, y, 3, 0, Math.PI * 2);
    ctx.fill();

    // Confidence, as the opacity of a short bar. A low-confidence entry
    // estimate should not look as solid as a clear one.
    ctx.globalAlpha = 0.25 + 0.75 * Math.min(row.confidence, 1);
    ctx.fillStyle = token("--cold-300");
    ctx.fillRect(x1 + 10, y - 2, 28 * Math.min(Math.max(row.confidence, 0), 1), 4);
    ctx.restore();

    label(ctx, `${row.delay_ms.toFixed(0)}`, bx, y - 11, { align: "center", size: 8 });
  });

  label(ctx, "conf", x1 + 10, pad.t - 14, { size: 8 });
}

function axisTicks(hi: number): number[] {
  const step = hi > 400 ? 100 : hi > 200 ? 50 : 25;
  const out: number[] = [];
  for (let t = 0; t <= hi; t += step) out.push(t);
  return out;
}

/**
 * Timing residual over the length of a take, with confidence as opacity.
 * Used by teach and practice: a single "worst moment" number hides whether
 * the take drifted steadily or lurched once.
 */
export function drawWindows(
  s: Surface,
  windows: TimingWindow[],
  opts: { minConfidence?: number; span?: number } = {},
): void {
  const { ctx, width, height } = s;
  const pad = { l: 40, r: 10, t: 12, b: 16 };
  const plotW = width - pad.l - pad.r;
  const plotH = height - pad.t - pad.b;
  const minConf = opts.minConfidence ?? 0.3;
  // Scale to the confident windows only. A window with almost no attacks in
  // it can report a residual of a quarter second, and letting that set the
  // axis flattens the eight windows that actually measured something into a
  // single line — the chart would then be a picture of its own uncertainty.
  // The uncertain bars are still drawn, faded, clamped to the axis.
  const confident = windows.filter((w) => w.confidence >= minConf);
  const scaleSet = (confident.length >= 3 ? confident : windows).map((w) =>
    Math.abs(w.residual_ms),
  );
  scaleSet.sort((a, b) => a - b);
  const p90 = scaleSet.length
    ? (scaleSet[Math.min(Math.floor(scaleSet.length * 0.9), scaleSet.length - 1)] ?? 0)
    : 0;
  const span = Math.max(opts.span ?? 60, p90 * 1.25);
  const duration = Math.max(windows.at(-1)?.t ?? 1, 1);
  const y = (v: number): number =>
    pad.t + scale(Math.min(Math.max(v, -span), span), span, -span, 0, plotH);
  const x = (t: number): number => pad.l + (t / duration) * plotW;

  for (const v of [span, 0, -span]) {
    hairline(ctx, pad.l, y(v), width - pad.r, y(v), v === 0 ? token("--rule-strong") : token("--rule"));
    label(ctx, `${v > 0 ? "+" : ""}${Math.round(v)}`, pad.l - 6, y(v), {
      align: "right",
      size: 8,
    });
  }
  label(ctx, "ms", pad.l - 6, y(0), { align: "right", size: 8, color: token("--fg-200") });

  // Capped width: with eight windows across a wide panel, a bar per window
  // becomes a 120px slab and the chart reads as a bar chart of nothing.
  const barW = Math.min(Math.max(plotW / Math.max(windows.length, 1) - 6, 2), 26);

  ctx.save();
  ctx.strokeStyle = token("--rule-strong");
  ctx.lineWidth = 1;
  ctx.beginPath();
  windows.forEach((w, i) => {
    const px = x(w.t);
    const py = y(w.residual_ms);
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  ctx.stroke();

  for (const w of windows) {
    const solid = w.confidence >= minConf;
    ctx.globalAlpha = solid ? 0.4 + 0.6 * Math.min(w.confidence, 1) : 0.22;
    ctx.fillStyle = solid
      ? Math.abs(w.residual_ms) > span * 0.5
        ? token("--warm")
        : token("--cold-200")
      : token("--fg-300");
    const top = Math.min(y(w.residual_ms), y(0));
    const h = Math.max(Math.abs(y(w.residual_ms) - y(0)), 1);
    ctx.fillRect(x(w.t) - barW / 2, top, barW, h);
    ctx.globalAlpha = solid ? 1 : 0.35;
    ctx.fillRect(x(w.t) - barW / 2, y(w.residual_ms) - 1, barW, 2);
  }
  ctx.restore();
  label(ctx, "faded bars = low confidence", pad.l + 4, pad.t - 2, { size: 8 });
}
