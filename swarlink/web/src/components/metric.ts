import { h } from "../lib/dom";
import { mount } from "../lib/canvas";
import { drawAnchored } from "../draw/meter";
import type { Metric } from "../types";

/* The metric card.
 *
 * Every number the engine produces reaches the screen through this one
 * component, and the component refuses to render a value without its
 * `meaning` and `reading`. That constraint is deliberate: the brief was to
 * "output numbers and what they mean", and the reliable way to honour it is
 * to make the explanation structurally impossible to omit rather than a thing
 * someone remembers to write. `meaning` is what the metric measures in
 * general; `reading` is what this particular value says.
 */

export function metricCard(metric: Metric, opts: { scale?: boolean } = {}): HTMLElement {
  const card = h(
    "article",
    { class: "metric" },
    h(
      "header",
      { class: "metric__head" },
      h("span", { class: "label" }, metric.label),
      h("span", { class: "metric__value num" }, metric.display),
    ),
    h("p", { class: "metric__reading" }, metric.reading),
  );

  if (opts.scale !== false && metric.anchors.length >= 2 && metric.value !== null) {
    const canvas = h("canvas", { class: "metric__scale" });
    card.appendChild(canvas);
    mount(canvas, (s) => drawAnchored(s, metric), { height: 56 });
  }

  card.appendChild(
    h(
      "details",
      { class: "metric__more" },
      h("summary", {}, "what this measures"),
      h("p", {}, metric.meaning),
      metric.anchor && h("p", { class: "metric__anchor" }, `Scale: ${metric.anchor}`),
    ),
  );
  return card;
}

/** A compact row of metric cards, for a scorecard column. */
export function metricList(
  metrics: Metric[],
  keys: string[],
  opts: { scale?: boolean } = {},
): HTMLElement {
  const byKey = new Map(metrics.map((m) => [m.key, m]));
  const chosen = keys
    .map((k) => byKey.get(k))
    .filter((m): m is Metric => m !== undefined);
  return h("div", { class: "metrics" }, ...chosen.map((m) => metricCard(m, opts)));
}

/** The big headline figure: one score, its verdict sentence, nothing else. */
export function headline(
  value: string,
  unit: string,
  sentence: string,
): HTMLElement {
  return h(
    "div",
    { class: "headline" },
    h(
      "div",
      { class: "headline__figure" },
      h("span", { class: "headline__num" }, value),
      h("span", { class: "headline__unit label" }, unit),
    ),
    h("p", { class: "headline__say prose" }, sentence),
  );
}

export function caveatList(caveats: string[]): HTMLElement | null {
  if (caveats.length === 0) return null;
  return h(
    "aside",
    { class: "caveats" },
    h("div", { class: "label label--accent" }, "what to distrust"),
    h(
      "ul",
      {},
      ...caveats.map((c) => h("li", {}, c)),
    ),
  );
}
