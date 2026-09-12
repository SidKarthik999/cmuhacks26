/* A forty-line element builder, in place of a framework.
 *
 * This interface has five screens and no shared mutable tree worth
 * diffing: each view builds its DOM once and then pushes numbers into a
 * handful of known nodes. A virtual DOM would add a dependency, a build step
 * and a mental model to solve a problem the app does not have.
 */

type Attrs = Record<string, string | number | boolean | EventListener | undefined>;
type Child = Node | string | number | null | undefined | false;

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Attrs = {},
  ...children: (Child | Child[])[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value as EventListener);
    } else if (key === "class") {
      el.className = String(value);
    } else if (key === "html") {
      el.innerHTML = String(value);
    } else if (value === true) {
      el.setAttribute(key, "");
    } else {
      el.setAttribute(key, String(value));
    }
  }
  append(el, children);
  return el;
}

export function append(parent: Node, children: (Child | Child[])[]): void {
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    parent.appendChild(
      typeof child === "string" || typeof child === "number"
        ? document.createTextNode(String(child))
        : child,
    );
  }
}

export function clear(el: Element): void {
  while (el.firstChild) el.removeChild(el.firstChild);
}

export function svg(markup: string, cls = ""): SVGElement {
  const wrap = document.createElement("div");
  wrap.innerHTML = markup.trim();
  const node = wrap.firstElementChild as SVGElement;
  if (cls) node.setAttribute("class", cls);
  return node;
}

/** `label` over `value`: the app's single unit of data display. */
export function stat(
  label: string,
  value: string,
  opts: { hint?: string; tone?: "" | "good" | "warn" | "bad" } = {},
): HTMLElement {
  return h(
    "div",
    { class: `stat${opts.tone ? ` stat--${opts.tone}` : ""}` },
    h("div", { class: "label" }, label),
    h("div", { class: "stat__value num" }, value),
    opts.hint && h("div", { class: "stat__hint" }, opts.hint),
  );
}

/**
 * Typographic pass over engine prose.
 *
 * The analysis modules write their sentences as ASCII — `--` for a dash and
 * `->` for an arrow — because they also have to read correctly in a terminal
 * report and in a log file, where an em dash is a liability. Converting them
 * once, here, keeps the engine's strings portable and stops `--` from
 * appearing in 40-point display type. Only text nodes are touched, so nothing
 * inside a numeric readout or an attribute can be affected.
 */
export function polish(root: Node): void {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const edits: Text[] = [];
  while (walker.nextNode()) edits.push(walker.currentNode as Text);
  for (const node of edits) {
    const next = (node.nodeValue ?? "")
      .replace(/ -- /g, " — ")
      .replace(/ -> /g, " → ")
      .replace(/(\d) x\b/g, "$1×");
    if (next !== node.nodeValue) node.nodeValue = next;
  }
}

export function fmtMs(ms: number): string {
  return Math.abs(ms) >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms.toFixed(0)} ms`;
}

export function signed(value: number, digits = 0): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}
