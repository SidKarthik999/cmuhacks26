import { h, stat } from "../lib/dom";
import { mount, repaint, token } from "../lib/canvas";
import { audioUrl, isOffline, remixConcert } from "../api";
import { drawSpread } from "../draw/spread";
import { drawLevels } from "../draw/meter";
import { drawWave } from "../draw/waveform";
import { caveatList, headline } from "../components/metric";
import { faderBank } from "../components/fader";
import { player } from "../components/player";
import { section, takeStrip } from "./shared";
import type { ConcertResult } from "../types";

/* Perform mode — the concert.
 *
 * The correction is one sentence of arithmetic and the whole feature turns on
 * getting its direction right. Nobody sang late. Each performer entered on
 * the lead's voice as it reached them, which is what a musician is supposed
 * to do; the only channel that was never delayed is the lead's own. So the
 * lead is pushed forward onto the performers' shared clock, rather than four
 * performers being dragged back onto the lead's.
 *
 * The faders are live. Moving one re-sums the corrected stems and returns a
 * new mix without re-running cleaning or alignment, because neither depends
 * on the gains — which is also why a 95-person audience can be served one
 * mixed stream instead of ninety-nine peer connections each.
 */

export function performPanel(result: ConcertResult): { el: HTMLElement; dispose: () => void } {
  const disposers: (() => void)[] = [];
  const { timing, stems } = result;
  const offline = isOffline();
  const url = (id: string): string | null => (offline ? null : audioUrl(id));

  let mixWave = result.mix.wave;
  let mixUrl = url(result.audio.mix);
  let mixLufs = result.mix.lufs;

  const spreadCanvas = h("canvas", {});
  disposers.push(
    mount(spreadCanvas, (s) => drawSpread(s, timing, result.lead), {
      height: 42 * stems.length + 60,
    }),
  );

  let mixPlayer = player({
    wave: mixWave,
    durationMs: result.mix.duration_ms,
    url: mixUrl,
    title: "corrected mix",
    compare: {
      wave: result.naive_mix.wave,
      url: url(result.audio.naive_mix),
      title: "uncorrected, as it arrived",
    },
  });
  disposers.push(() => mixPlayer.dispose());
  const playerSlot = h("div", { class: "panel__player" }, mixPlayer.el);

  const mixCanvas = h("canvas", {});
  disposers.push(
    mount(
      mixCanvas,
      (s) =>
        drawWave(s, mixWave, {
          color: token("--cold-100"),
          durationMs: result.mix.duration_ms,
          ruler: true,
        }),
      { height: 96 },
    ),
  );

  const levelsCanvas = h("canvas", {});
  let gains = stems.map((s) => s.gain_db);
  disposers.push(
    mount(
      levelsCanvas,
      (s) =>
        drawLevels(
          s,
          stems.map((stem, i) => ({
            name: stem.name,
            lufs: stem.lufs,
            gainDb: gains[i] ?? 0,
            lead: stem.is_lead,
          })),
        ),
      { height: 26 * stems.length + 10 },
    ),
  );

  const mixLufsEl = h("span", { class: "num" }, `${mixLufs.toFixed(2)} LUFS`);
  const remixState = h("span", { class: "label" }, offline ? "faders need the live engine" : "matched");

  const bank = faderBank(
    stems.map((s) => ({
      name: s.name,
      lead: s.is_lead,
      db: s.gain_db,
    })),
    (next) => {
      gains = next;
      repaint(levelsCanvas);
      void applyRemix(next);
    },
  );
  if (offline) bank.setBusy(true);

  let pending = 0;
  async function applyRemix(next: number[]): Promise<void> {
    const ticket = ++pending;
    remixState.textContent = "re-mixing…";
    const remix = await remixConcert(result.result_id, next).catch(() => null);
    // A fader can be dragged faster than a round trip; only the newest
    // request is allowed to land.
    if (ticket !== pending) return;
    if (!remix) {
      remixState.textContent = offline
        ? "faders need the live engine"
        : "re-mix unavailable";
      return;
    }
    mixWave = remix.mix.wave;
    mixLufs = remix.mix.lufs;
    mixUrl = audioUrl(remix.audio.mix);
    mixLufsEl.textContent = `${mixLufs.toFixed(2)} LUFS`;
    remixState.textContent = `re-mixed in ${remix.elapsed_ms.toFixed(0)} ms`;
    repaint(mixCanvas);

    const fresh = player({
      wave: mixWave,
      durationMs: result.mix.duration_ms,
      url: mixUrl,
      title: "corrected mix, your fader settings",
      compare: {
        wave: result.naive_mix.wave,
        url: url(result.audio.naive_mix),
        title: "uncorrected, as it arrived",
      },
    });
    mixPlayer.dispose();
    playerSlot.replaceChildren(fresh.el);
    mixPlayer = fresh;
  }

  const el = h(
    "div",
    { class: "panel panel--perform" },

    section(
      "one downbeat",
      `${timing.verdict_before} → ${timing.verdict}`,
      h(
        "div",
        { class: "panel__headline" },
        headline(
          `${timing.spread_after_ms.toFixed(0)}`,
          "ms between entries",
          `The five performers came in over a ${timing.spread_before_ms.toFixed(0)} ms window because each ` +
            `waited to hear the lead. After advancing the lead by ` +
            `${timing.lead_advance_ms.toFixed(0)} ms, the spread measured on the corrected audio is ` +
            `${timing.spread_after_ms.toFixed(0)} ms.`,
        ),
        h(
          "div",
          { class: "panel__headaside stats" },
          stat("spread before", `${timing.spread_before_ms.toFixed(0)} ms`, {
            hint: timing.verdict_before,
          }),
          stat("spread after", `${timing.spread_after_ms.toFixed(0)} ms`, {
            hint: "re-measured on the corrected audio",
            tone: timing.spread_after_ms < 25 ? "good" : timing.spread_after_ms < 60 ? "warn" : "bad",
          }),
          stat("lead advanced", `${timing.lead_advance_ms.toFixed(0)} ms`, {
            hint: `the ${timing.advance_statistic} of the measured delays`,
          }),
          stat("nominal", `${timing.nominal_delay_ms.toFixed(0)} ms`, {
            hint: "the delay the feature is specified against",
          }),
        ),
      ),
      caveatList(timing.caveats),
    ),

    section(
      "every entry, measured",
      "Hollow is where they came in. Filled is where they sit now.",
      h("figure", { class: "figure" }, spreadCanvas, h(
        "figcaption",
        {},
        "Both marks are measurements. The filled ones do not form a perfect " +
          "column because the corrected audio is measured again rather than " +
          "the plan being restated — by construction the plan puts every " +
          "entry at exactly zero, which would prove nothing.",
      )),
      delayTable(result),
      h("p", { class: "panel__fine" }, timing.note),
    ),

    section(
      "the mix",
      `${stems.length} voices, cleaned separately, summed once`,
      playerSlot,
      h("figure", { class: "figure" }, mixCanvas, h("figcaption", {}, "The mix at the current fader settings.")),
      h(
        "div",
        { class: "panel__mixmeta" },
        h("span", { class: "label" }, "programme loudness"),
        mixLufsEl,
        remixState,
      ),
    ),

    section(
      "faders",
      "Manual balance, per performer, without re-running the analysis",
      bank.el,
      h("figure", { class: "figure" }, levelsCanvas, h(
        "figcaption",
        {},
        "Each voice's loudness with its fader applied. Zero on a fader is " +
          "the level the engine matched that voice to, not raw microphone " +
          "level.",
      )),
      h(
        "p",
        { class: "prose" },
        "The engine matched the five voices by moving them ",
        h(
          "b",
          { class: "num" },
          timing.match_gain_db.map((g) => `${g > 0 ? "+" : ""}${g.toFixed(1)}`).join(", "),
        ),
        " dB respectively. Those corrections are the zero point of the faders " +
          "above, so a mixing engineer starts from a balanced band rather " +
          "than from whatever five different rooms happened to produce.",
      ),
    ),

    section(
      "the audience",
      `${result.audience} listeners on one mixed stream`,
      h(
        "div",
        { class: "stats stats--wide" },
        stat("performers", String(stems.length), { hint: "uplinks carrying audio" }),
        stat("audience", String(result.audience), { hint: "receive-only" }),
        stat("mixed streams out", "1", { hint: "the same programme for everyone" }),
        stat(
          "mesh equivalent",
          (
            (stems.length + result.audience) *
            (stems.length + result.audience - 1)
          ).toLocaleString("en-US"),
          { hint: "streams a peer-to-peer room would carry", tone: "bad" },
        ),
      ),
      h(
        "p",
        { class: "prose" },
        "Group performance is not a feature conferencing tools forgot. A " +
          "hundred-person peer mesh is roughly ten thousand streams, so every " +
          "platform mixes centrally — and a central mix is exactly where a " +
          "delay correction belongs, because that is the one place all the " +
          "voices exist at once.",
      ),
    ),

    section(
      "the stems",
      "Each performer's own take, after cleaning and shifting",
      h(
        "div",
        { class: "takes takes--grid" },
        ...stems.map((s) =>
          takeStrip(s, s.is_lead ? token("--cold-100") : token("--cold-300")),
        ),
      ),
    ),
  );

  return {
    el,
    dispose() {
      for (const d of disposers) d();
    },
  };
}

function delayTable(result: ConcertResult): HTMLElement {
  const rows = result.timing.measured_delays;
  return h(
    "div",
    { class: "table__wrap" },
    h(
      "table",
      { class: "table" },
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          h("th", {}, "performer"),
          h("th", {}, "entered after lead"),
          h("th", {}, "shift applied"),
          h("th", {}, "residual"),
          h("th", {}, "confidence"),
        ),
      ),
      h(
        "tbody",
        {},
        ...rows.map((r) =>
          h(
            "tr",
            { class: r.name === result.lead ? "table__row--lead" : "" },
            h(
              "td",
              { class: "table__note" },
              r.name,
              r.name === result.lead ? h("span", { class: "table__tag label" }, "lead") : null,
            ),
            h("td", { class: "num" }, `${r.delay_ms.toFixed(0)} ms`),
            h(
              "td",
              { class: "num" },
              `${r.shift_applied_ms > 0 ? "+" : ""}${r.shift_applied_ms.toFixed(0)} ms`,
            ),
            h(
              "td",
              { class: `num ${Math.abs(r.residual_ms) <= 15 ? "cell--good" : Math.abs(r.residual_ms) <= 40 ? "cell--warn" : "cell--bad"}` },
              `${r.residual_ms > 0 ? "+" : ""}${r.residual_ms.toFixed(0)} ms`,
            ),
            h("td", { class: "num" }, r.confidence.toFixed(3)),
          ),
        ),
      ),
    ),
  );
}
