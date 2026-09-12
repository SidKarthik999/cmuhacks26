/* Canvas plumbing: device-pixel sizing, resize, and one shared clock.
 *
 * Every chart in the app is a canvas rather than SVG because the widest one
 * draws a few thousand waveform columns and the roster draws eight of those
 * at once. The tradeoff is that canvas has no layout, so the sizing has to be
 * handled explicitly — and handled in device pixels, or a 1px hairline on a
 * 2x display lands between physical pixels and renders as a grey smudge,
 * which is exactly the "vibe coded" tell this interface is trying to avoid.
 */

export interface Surface {
  canvas: HTMLCanvasElement;
  ctx: CanvasRenderingContext2D;
  /** CSS pixels, not device pixels: all drawing code works in these. */
  width: number;
  height: number;
  dpr: number;
}

export type DrawFn = (s: Surface, time: number) => void;

const REDUCED = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)");

export function prefersReducedMotion(): boolean {
  return REDUCED?.matches ?? false;
}

/** Reads a CSS custom property off `:root`, so the palette lives in one file. */
const paletteCache = new Map<string, string>();

export function token(name: string): string {
  let value = paletteCache.get(name);
  if (value === undefined) {
    value = getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
    // `color-mix()` is legal in CSS but not as a canvas fillStyle in every
    // engine, so the tokens that use it are resolved to a literal here.
    if (value.startsWith("color-mix")) value = "#2b3138";
    paletteCache.set(name, value);
  }
  return value;
}

/**
 * Attaches a draw function to a canvas.
 *
 * `animate: false` is the default because most charts here are static once
 * the numbers arrive; redrawing them sixty times a second would burn a
 * laptop battery to display an unchanging picture. Animated surfaces opt in,
 * and opt straight back out under `prefers-reduced-motion`.
 */
export function mount(
  canvas: HTMLCanvasElement,
  draw: DrawFn,
  opts: { animate?: boolean; height?: number } = {},
): () => void {
  const ctx = canvas.getContext("2d");
  if (!ctx) return () => {};

  const surface: Surface = { canvas, ctx, width: 0, height: 0, dpr: 1 };
  let frame = 0;
  let start = performance.now();
  let alive = true;

  const resize = (): void => {
    const dpr = Math.min(globalThis.devicePixelRatio || 1, 2);
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(rect.width, 1);
    const height = Math.max(opts.height ?? rect.height, 1);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    if (opts.height) canvas.style.height = `${opts.height}px`;
    surface.width = width;
    surface.height = height;
    surface.dpr = dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };

  const render = (now: number): void => {
    if (!alive) return;
    ctx.clearRect(0, 0, surface.width, surface.height);
    draw(surface, (now - start) / 1000);
    if (opts.animate && !prefersReducedMotion()) {
      frame = requestAnimationFrame(render);
    }
  };

  const kick = (): void => {
    resize();
    cancelAnimationFrame(frame);
    start = performance.now();
    frame = requestAnimationFrame(render);
  };

  const observer = new ResizeObserver(kick);
  observer.observe(canvas);
  canvas.addEventListener(REPAINT, kick);
  kick();

  return () => {
    alive = false;
    observer.disconnect();
    canvas.removeEventListener(REPAINT, kick);
    cancelAnimationFrame(frame);
  };
}

const REPAINT = "swarlink:repaint";

/** Redraws a mounted static surface — used when new numbers arrive. */
export function repaint(canvas: HTMLCanvasElement): void {
  canvas.dispatchEvent(new Event(REPAINT));
}

/* ------------------------------------------------------------ primitives */

/** A crisp 1px line. Canvas strokes straddle the coordinate, hence the 0.5. */
export function hairline(
  ctx: CanvasRenderingContext2D,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  color: string,
): void {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.beginPath();
  if (y1 === y2) {
    ctx.moveTo(x1, Math.round(y1) + 0.5);
    ctx.lineTo(x2, Math.round(y2) + 0.5);
  } else if (x1 === x2) {
    ctx.moveTo(Math.round(x1) + 0.5, y1);
    ctx.lineTo(Math.round(x2) + 0.5, y2);
  } else {
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
  }
  ctx.stroke();
  ctx.restore();
}

export function label(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  opts: { color?: string; size?: number; align?: CanvasTextAlign } = {},
): void {
  ctx.save();
  ctx.fillStyle = opts.color ?? token("--fg-300");
  ctx.font = `500 ${opts.size ?? 9}px ${token("--mono") || "monospace"}`;
  ctx.textAlign = opts.align ?? "left";
  ctx.textBaseline = "middle";
  // Tracked out to match the `.label` class. Not in every DOM typing yet.
  (ctx as { letterSpacing?: string }).letterSpacing = "0.1em";
  ctx.fillText(text.toUpperCase(), x, y);
  ctx.restore();
}

/** Maps a value in [lo, hi] onto [a, b], clamped. */
export function scale(
  v: number,
  lo: number,
  hi: number,
  a: number,
  b: number,
): number {
  if (hi === lo) return a;
  const t = Math.min(Math.max((v - lo) / (hi - lo), 0), 1);
  return a + t * (b - a);
}
