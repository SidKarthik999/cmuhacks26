import { append, h } from "../lib/dom";
import { getGlossary } from "../api";
import { hairline, mount, token, type Surface } from "../lib/canvas";
import { GLYPHS } from "../lib/glyphs";
import { metricCard } from "../components/metric";
import type { Ctx } from "../main";

/* The landing page.
 *
 * One job: make a stranger understand, before they click anything, that this
 * is a measuring instrument for singing and not a conferencing skin. So the
 * page leads with the three numbers the product is actually built on — 70/30,
 * 0.1 s, 9,900 — and every claim on it is a figure the engine produces rather
 * than an adjective.
 */

const FEATURES = [
  {
    n: "01",
    route: "#/teach",
    glyph: GLYPHS.trebleClef,
    kicker: "teach",
    title: "A lesson that can show its working",
    body:
      "The teacher sings the line. Swarlink cleans it, tracks its pitch, " +
      "writes it out as notes and stores it. The student sings it back, and " +
      "the second take is stretched onto the first — tempo, entry and level " +
      "matched — so the two can be compared note by note rather than " +
      "impression by impression.",
    figures: [
      ["70 / 30", "pitch to volume, the fixed weighting behind every score"],
      ["± cents", "how sharp or flat, per note, against the teacher's own pitch"],
    ],
  },
  {
    n: "02",
    route: "#/practice",
    glyph: GLYPHS.beamedPair,
    kicker: "practice",
    title: "Two singers who never heard each other",
    body:
      "Both sing alone, at their own pace, to the same music. Swarlink " +
      "matches their levels, finds the one time-warp that lays one take over " +
      "the other, and mixes them. The result is what they would have sounded " +
      "like together — and the residual says how far apart they really were.",
    figures: [
      ["ms", "worst moment apart, before and after the correction"],
      ["cents", "the interval between the two voices, named"],
    ],
  },
  {
    n: "03",
    route: "#/perform",
    glyph: GLYPHS.fermata,
    kicker: "perform",
    title: "Five performers, one downbeat",
    body:
      "The lead sings. Roughly a tenth of a second later their voice reaches " +
      "everyone else, and everyone else comes in — correctly, on what they " +
      "heard. Swarlink measures each of those entries, pushes the lead " +
      "forward by the middle of them, cleans every voice separately, and " +
      "hands back one mix with a fader per performer.",
    figures: [
      ["0.1 s", "the delay each performer sang behind, and the lead's advance"],
      ["9,900", "peer streams a 100-person mesh would need. Swarlink sends one mix"],
    ],
  },
];

export function landing(ctx: Ctx): HTMLElement {
  const page = h("div", { class: "landing" });

  append(page, [
    masthead(ctx),
    hero(ctx),
    problem(),
    featureSections(ctx),
    numbers(),
    footer(ctx),
  ]);
  return page;
}

/* ------------------------------------------------------------- masthead */

function masthead(ctx: Ctx): HTMLElement {
  const status = ctx.offline
    ? "offline · captured results"
    : `engine ${ctx.health.version} · ${(ctx.health.sample_rate / 1000).toFixed(2)} kHz`;

  return h(
    "header",
    { class: "mast" },
    h("a", { class: "mast__mark", href: "#/" }, "Swarlink"),
    h(
      "nav",
      { class: "mast__nav", "aria-label": "Modes" },
      ...FEATURES.map((f) =>
        h("a", { class: "mast__link", href: f.route }, `${f.n} ${f.kicker}`),
      ),
    ),
    h(
      "div",
      { class: "mast__status" },
      h("span", { class: `dot${ctx.offline ? " dot--warm" : ""}` }),
      h("span", { class: "label" }, status),
    ),
  );
}

/* ----------------------------------------------------------------- hero */

function hero(ctx: Ctx): HTMLElement {
  const canvas = h("canvas", { class: "hero__canvas", "aria-hidden": "true" });
  mount(canvas, drawHero, { animate: true });

  return h(
    "section",
    { class: "hero", id: "main" },
    canvas,
    h(
      "div",
      { class: "hero__body" },
      h("span", { class: "label label--accent" }, "virtual music, measured"),
      h("h1", { class: "d-hero hero__title" }, "SWARLINK"),
      h(
        "p",
        { class: "prose hero__lede" },
        "Lessons, rehearsals and performances went online and stayed there. " +
          "Conferencing tools were never built to carry them: they duck one " +
          "voice under another, they hide the delay instead of measuring it, " +
          "and they have nothing to say about whether you sang the right note. ",
        h("strong", {}, "Swarlink measures the things that matter and tells you what the measurement means."),
      ),
      h(
        "div",
        { class: "hero__cta" },
        h("a", { class: "btn btn--primary", href: "#/teach" }, "open a lesson"),
        h("a", { class: "btn", href: "#/perform" }, "open the concert hall"),
      ),
      h(
        "dl",
        { class: "hero__facts" },
        ...[
          ["three", "modes on one call"],
          ["70 / 30", "pitch / volume weighting"],
          ["0.1 s", "delay corrected, not hidden"],
          [
            ctx.health.cleaning ? "on" : "off",
            ctx.health.cleaning ? "noise cleaning active" : "cleaning unavailable",
          ],
        ].flatMap(([v, k]) => [
          h("div", { class: "fact" }, h("dt", { class: "fact__v num" }, v ?? ""), h("dd", { class: "fact__k label" }, k ?? "")),
        ]),
      ),
    ),
  );
}

/**
 * The hero field: a drifting bundle of pitch contours over a stave.
 *
 * Three lines, incommensurate frequencies, one of them broken by rests. It
 * reads as three singers on one piece of music, which is the whole product in
 * one image, and it costs three sine evaluations per column.
 */
function drawHero(s: Surface, t: number): void {
  const { ctx, width, height } = s;
  const lines = 5;
  for (let i = 1; i <= lines; i += 1) {
    hairline(ctx, 0, (height * i) / (lines + 1), width, (height * i) / (lines + 1), token("--rule"));
  }

  const voices = [
    { amp: 0.16, rate: 0.21, phase: 0, color: token("--cold-100"), alpha: 0.95, gaps: false },
    { amp: 0.1, rate: 0.33, phase: 1.7, color: token("--cold-200"), alpha: 0.8, gaps: true },
    { amp: 0.22, rate: 0.13, phase: 3.4, color: token("--cold-200"), alpha: 0.55, gaps: false },
  ];

  for (const v of voices) {
    ctx.save();
    ctx.strokeStyle = v.color;
    ctx.globalAlpha = v.alpha;
    ctx.lineWidth = 1.6;
    ctx.lineJoin = "round";
    // The one bloom in the product. A stepped line on near-black reads as
    // flat vector art without it; with it, it reads as something lit.
    ctx.shadowColor = v.color;
    ctx.shadowBlur = 12;
    ctx.beginPath();
    let open = false;
    for (let x = 0; x <= width; x += 3) {
      const u = x / width;
      // Quantised to a semitone-ish ladder so it looks sung rather than
      // synthesised: a smooth sine reads as a signal, steps read as notes.
      const raw =
        Math.sin(u * 7 + t * v.rate + v.phase) * 0.6 +
        Math.sin(u * 13 - t * v.rate * 1.7 + v.phase) * 0.4;
      const stepped = Math.round(raw * 6) / 6;
      const y = height / 2 - stepped * height * v.amp;
      const rest = v.gaps && Math.sin(u * 21 + v.phase) > 0.86;
      if (rest) {
        open = false;
        continue;
      }
      if (open) ctx.lineTo(x, y);
      else ctx.moveTo(x, y);
      open = true;
    }
    ctx.stroke();
    ctx.restore();
  }
}

/* -------------------------------------------------------------- problem */

function problem(): HTMLElement {
  return h(
    "section",
    { class: "band" },
    h(
      "div",
      { class: "band__head" },
      h("span", { class: "label" }, "why this is not already solved"),
      h("h2", { class: "d-1" }, "The delay is physics. The mix is arithmetic."),
    ),
    h(
      "div",
      { class: "band__cols" },
      h(
        "div",
        { class: "band__col" },
        h("span", { class: "label label--accent" }, "the honest constraint"),
        h(
          "p",
          { class: "prose" },
          "Nothing removes the delay between two people on a network. A " +
            "performer who waits to hear the lead and then sings is not " +
            "late — they are doing the only thing a musician can do. Live " +
            "conferencing treats that as a problem to paper over with echo " +
            "suppression and automatic gain.",
        ),
      ),
      h(
        "div",
        { class: "band__col" },
        h("span", { class: "label label--accent" }, "what swarlink does instead"),
        h(
          "p",
          { class: "prose" },
          "Measure the delay, then move the lead forward by it. Every other " +
            "part stays exactly where it was sung, because every other part " +
            "was right. One mix goes out to the room, and the analysis that " +
            "produced it is on screen with its own error bars.",
        ),
      ),
    ),
  );
}

/* ------------------------------------------------------------- features */

function featureSections(ctx: Ctx): HTMLElement {
  const wrap = h("div", { class: "features" });
  FEATURES.forEach((f) => {
    const scenes = ctx.scenes.filter((s) => s.key.startsWith(f.kicker === "teach" ? "lesson" : f.kicker === "practice" ? "duet" : "concert"));
    wrap.appendChild(
      h(
        "section",
        { class: "feature" },
        h(
          "div",
          { class: "feature__rail" },
          h("span", { class: "feature__n d-2 num" }, f.n),
          h("span", { class: "feature__glyph", html: f.glyph, "aria-hidden": "true" }),
        ),
        h(
          "div",
          { class: "feature__body" },
          h("span", { class: "label label--accent" }, f.kicker),
          h("h3", { class: "d-2" }, f.title),
          h("p", { class: "prose" }, f.body),
          h(
            "ul",
            { class: "feature__figs" },
            ...f.figures.map(([v, k]) =>
              h(
                "li",
                {},
                h("span", { class: "feature__fig num" }, v ?? ""),
                h("span", { class: "feature__figk" }, k ?? ""),
              ),
            ),
          ),
          h(
            "div",
            { class: "feature__foot" },
            h("a", { class: "btn btn--primary", href: f.route }, `enter ${f.kicker}`),
            h(
              "span",
              { class: "label" },
              `${scenes.length} reference ${scenes.length === 1 ? "scene" : "scenes"} with known answers`,
            ),
          ),
        ),
      ),
    );
  });
  return wrap;
}

/* -------------------------------------------------------------- numbers */

function numbers(): HTMLElement {
  const pick = [
    "overall_score",
    "mean_abs_cents",
    "level_offset_db",
    "worst_window_ms",
  ];
  const section = h(
    "section",
    { class: "band band--numbers" },
    h(
      "div",
      { class: "band__head" },
      h("span", { class: "label" }, "every figure carries its meaning"),
      h("h2", { class: "d-1" }, "No number without a sentence."),
      h(
        "p",
        { class: "prose" },
        "A score of 91 means nothing on its own. Each measurement on every " +
          "screen arrives with what it measures, what this particular value " +
          "says, and a scale of perceptual anchors — the point at which a " +
          "trained ear starts to notice, the point at which an audience does.",
      ),
    ),
    h("div", { class: "metrics metrics--grid" }),
  );

  // The glossary is a separate request; fill the grid when it lands rather
  // than blocking the page on it.
  void getGlossary().then((glossary) => {
    const grid = section.querySelector(".metrics");
    if (!grid) return;
    const lookup = new Map(glossary.map((m) => [m.key, m]));
    for (const key of pick) {
      const metric = lookup.get(key);
      if (metric) grid.appendChild(metricCard(metric, { scale: metric.value !== null }));
    }
  });

  return section;
}

function footer(ctx: Ctx): HTMLElement {
  return h(
    "footer",
    { class: "foot" },
    h(
      "div",
      { class: "foot__col" },
      h("span", { class: "label" }, "swarlink"),
      h(
        "p",
        { class: "prose" },
        "An analysis engine for virtual music teaching, rehearsal and " +
          "performance, with a call around it.",
      ),
    ),
    h(
      "div",
      { class: "foot__col" },
      h("span", { class: "label" }, "engine"),
      h(
        "ul",
        { class: "foot__list" },
        h("li", {}, `version ${ctx.health.version}`),
        h("li", {}, `${ctx.health.sample_rate} Hz analysis rate`),
        h(
          "li",
          {},
          `noise cleaning ${ctx.health.cleaning ? "available" : `unavailable — ${ctx.health.cleaning_error ?? "no module"}`}`,
        ),
        h(
          "li",
          {},
          `weighting ${ctx.health.weighting.pitch * 100}/${ctx.health.weighting.volume * 100}`,
        ),
      ),
    ),
    h(
      "div",
      { class: "foot__col" },
      h("span", { class: "label" }, "modes"),
      h(
        "ul",
        { class: "foot__list" },
        ...FEATURES.map((f) => h("li", {}, h("a", { href: f.route }, f.kicker))),
      ),
    ),
  );
}
