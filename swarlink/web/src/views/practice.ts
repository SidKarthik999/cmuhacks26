import { h, stat } from "../lib/dom";
import { mount, token } from "../lib/canvas";
import { audioUrl, isOffline } from "../api";
import { drawContour, midiRange } from "../draw/contour";
import { drawStacked } from "../draw/waveform";
import { drawWindows } from "../draw/spread";
import { caveatList, headline } from "../components/metric";
import { player } from "../components/player";
import { section, takeStrip } from "./shared";
import type { DuetResult } from "../types";

/* Practice mode.
 *
 * The feature is easy to state and easy to fake, so this panel is built
 * around the one comparison that cannot be faked: the same two takes summed
 * without correction, on the same player, at the same position. If the
 * corrected mix sounds together and the raw sum sounds like two people in
 * different rooms, the claim is demonstrated rather than asserted.
 *
 * Everything else on the page exists to say by how much. "Tightness" is the
 * worst moment the two takes are apart, measured in windows across the whole
 * performance — not an average, because an average hides the one bar where
 * they came apart, and that bar is the one worth practising.
 */

/** Whole cents, without the "-0" that `toFixed` produces for -0.3. */
function cents(value: number): string {
  const rounded = Math.round(value);
  return `${rounded > 0 ? "+" : ""}${rounded === 0 ? 0 : rounded}`;
}

export function practicePanel(result: DuetResult): { el: HTMLElement; dispose: () => void } {
  const disposers: (() => void)[] = [];
  const { sync, harmony, singers } = result;
  const offline = isOffline();
  const url = (id: string): string | null => (offline ? null : audioUrl(id));
  const a = singers[0];
  const b = singers[1];
  if (!a || !b) {
    return { el: h("div", { class: "panel" }, "a duet needs two takes"), dispose: () => {} };
  }

  const mixPlayer = player({
    wave: result.mix.wave,
    durationMs: result.mix.duration_ms,
    url: url(result.audio.mix),
    title: "corrected mix",
    compare: {
      wave: result.naive_mix.wave,
      url: url(result.audio.naive_mix),
      title: "the raw sum, uncorrected",
    },
  });
  disposers.push(mixPlayer.dispose);

  const stackedRaw = h("canvas", {});
  disposers.push(
    mount(
      stackedRaw,
      (s) =>
        drawStacked(
          s,
          [
            { wave: a.wave, color: token("--cold-300"), name: a.name },
            { wave: b.wave, color: token("--fg-300"), name: b.name },
          ],
          Math.max(a.duration_ms, b.duration_ms),
        ),
      { height: 150 },
    ),
  );

  const stackedFixed = h("canvas", {});
  disposers.push(
    mount(
      stackedFixed,
      (s) =>
        drawStacked(
          s,
          [
            { wave: a.aligned_wave, color: token("--cold-100"), name: a.name },
            { wave: b.aligned_wave, color: token("--warm"), name: b.name },
          ],
          result.mix.duration_ms,
        ),
      { height: 150 },
    ),
  );

  const range = midiRange([a.contour, b.contour]);
  const contourCanvas = h("canvas", {});
  disposers.push(
    mount(
      contourCanvas,
      (s) =>
        drawContour(s, b.contour, {
          reference: a.contour,
          referenceColor: token("--cold-100"),
          color: token("--warm"),
          range,
          durationMs: Math.max(a.duration_ms, b.duration_ms),
        }),
      { height: 240 },
    ),
  );

  const windowsCanvas = h("canvas", {});
  disposers.push(
    mount(windowsCanvas, (s) => drawWindows(s, sync.windows, { span: 40 }), {
      height: 136,
    }),
  );

  const el = h(
    "div",
    { class: "panel panel--practice" },

    section(
      "are we together",
      sync.verdict,
      h(
        "div",
        { class: "panel__headline" },
        headline(
          `${sync.tightness_after_ms.toFixed(0)}`,
          "ms apart, worst moment",
          `Before correction the two takes were ${sync.tightness_before_ms.toFixed(0)} ms apart at their worst. ` +
            `Matching levels and fitting one time map closed ${sync.improvement_ms.toFixed(0)} ms of that.`,
        ),
        h(
          "div",
          { class: "panel__headaside stats" },
          stat("tightness before", `${sync.tightness_before_ms.toFixed(0)} ms`, {
            hint: "worst window, as recorded",
          }),
          stat("tightness after", `${sync.tightness_after_ms.toFixed(0)} ms`, {
            hint: "worst window, corrected",
            tone: sync.tightness_after_ms < 30 ? "good" : sync.tightness_after_ms < 70 ? "warn" : "bad",
          }),
          stat("confidence", sync.confidence.toFixed(3), {
            hint: "agreement of the onset evidence",
          }),
        ),
      ),
      caveatList(sync.caveats),
    ),

    section(
      "the mix",
      "The corrected sum, and the uncorrected one for comparison",
      mixPlayer.el,
      h(
        "p",
        { class: "prose" },
        "Both takes are normalised to ",
        h("b", { class: "num" }, `${result.mix.lufs.toFixed(1)} LUFS`),
        " before summing, so neither singer wins on microphone gain: ",
        h("b", {}, a.name),
        " was moved ",
        h("b", { class: "num" }, `${a.match_gain_db.toFixed(2)} dB`),
        " and ",
        h("b", {}, b.name),
        " ",
        h("b", { class: "num" }, `${b.match_gain_db.toFixed(2)} dB`),
        ". ",
        result.naive_mix.note,
      ),
      h("figure", { class: "figure" }, stackedRaw, h(
        "figcaption",
        {},
        "As recorded. Two people singing the same music with nothing in " +
          "common but the score.",
      )),
      h("figure", { class: "figure" }, stackedFixed, h(
        "figcaption",
        {},
        "After level matching and time warping. The attacks line up " +
          "vertically, which is what the mix is summing.",
      )),
    ),

    section(
      "time",
      "Entry, tempo and the shape of the disagreement",
      h(
        "div",
        { class: "stats stats--wide" },
        stat("entry offset", `${sync.entry_offset_ms.toFixed(0)} ms`, {
          hint: "second singer's start against the first",
        }),
        stat("tempo", `${sync.tempo_ratio.toFixed(4)}×`, {
          hint: `${sync.tempo_percent > 0 ? "+" : ""}${sync.tempo_percent.toFixed(2)}% over the phrase`,
        }),
        stat("drift by the end", `${sync.drift_ms.toFixed(0)} ms`, {
          hint: "where a constant-offset fix would leave them",
          tone: Math.abs(sync.drift_ms) > 120 ? "warn" : "",
        }),
        stat("rubato", `${sync.rubato_ms.toFixed(0)} ms`, {
          hint: "departure from any single steady tempo",
        }),
      ),
      h("figure", { class: "figure" }, windowsCanvas, h(
        "figcaption",
        {},
        "Residual timing error across the take after correction. Faded bars " +
          "are windows with too little onset evidence to trust.",
      )),
      h("p", { class: "panel__fine" }, sync.note),
    ),

    section(
      "harmony",
      harmony.label,
      h(
        "div",
        { class: "stats stats--wide" },
        stat("median interval", `${cents(harmony.cents)} cents`, {
          hint: `nearest equal-tempered interval is ${harmony.nearest_interval_cents.toFixed(0)}`,
        }),
        stat("off that interval", `${cents(harmony.error_cents)} cents`, {
          hint: harmony.in_tune ? "inside 50 cents — in tune" : "outside 50 cents",
          tone: harmony.in_tune ? "good" : "warn",
        }),
        stat(
          "spread",
          harmony.spread_cents === null ? "—" : `${harmony.spread_cents.toFixed(0)} cents`,
          { hint: "how much the interval moved" },
        ),
        stat("coverage", `${harmony.coverage_percent.toFixed(0)}%`, {
          hint: "frames where both voices were pitched",
        }),
      ),
      harmony.reliable
        ? null
        : h(
            "p",
            { class: "prose panel__warn" },
            "The pitch reading on at least one voice is not reliable enough " +
              "here to name an interval, so the figure above is reported but " +
              "should not be believed. This is usually a low voice under " +
              "mains hum, where the tracker locks confidently onto the wrong " +
              "octave.",
          ),
      h("figure", { class: "figure" }, contourCanvas, h(
        "figcaption",
        {},
        `Both pitch tracks on one semitone axis: ${a.name} in blue, ${b.name} in amber, the second warped onto the first.`,
      )),
      h("p", { class: "panel__fine" }, harmony.reading ?? ""),
    ),

    section(
      "the takes",
      "Recorded separately, neither singer hearing the other",
      h("div", { class: "takes" }, takeStrip(a, token("--cold-100")), takeStrip(b, token("--warm"))),
    ),
  );

  return {
    el,
    dispose() {
      for (const d of disposers) d();
    },
  };
}
