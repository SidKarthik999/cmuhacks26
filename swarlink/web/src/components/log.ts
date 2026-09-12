import { clear, h } from "../lib/dom";
import type { LogLine } from "../types";

/* The call log.
 *
 * A video platform without a log is a black box: something happened to the
 * audio and the user is asked to believe a score. Every join, mode change,
 * analysis run and mix is timestamped against the room clock, so the numbers
 * on the scorecards can be traced to the event that produced them. It doubles
 * as the chat panel, because in a lesson the teacher's "again, slower" and
 * the analysis that follows it belong in the same column.
 */

const MARK: Record<LogLine["kind"], string> = {
  join: "+",
  leave: "–",
  mode: "»",
  analysis: "∿",
  mix: "=",
  chat: "",
  note: "·",
};

export interface LogPanel {
  el: HTMLElement;
  render(lines: LogLine[]): void;
}

export function logPanel(opts: {
  onSend?: (text: string) => void;
  title?: string;
}): LogPanel {
  const list = h("div", { class: "log__list", role: "log", "aria-live": "polite" });

  const input = h("input", {
    type: "text",
    placeholder: "message the room",
    "aria-label": "Message the room",
  }) as HTMLInputElement;

  const send = (): void => {
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    opts.onSend?.(text);
  };

  const form = opts.onSend
    ? h(
        "form",
        {
          class: "log__compose",
          onsubmit: (e: Event) => {
            e.preventDefault();
            send();
          },
        },
        input,
        h("button", { class: "btn", type: "submit" }, "send"),
      )
    : null;

  const el = h(
    "section",
    { class: "log" },
    h(
      "header",
      { class: "log__head" },
      h("span", { class: "label" }, opts.title ?? "call log"),
    ),
    list,
    form,
  );

  let lastCount = -1;
  return {
    el,
    render(lines: LogLine[]) {
      if (lines.length === lastCount) return;
      lastCount = lines.length;
      clear(list);
      for (const line of lines) {
        list.appendChild(
          h(
            "div",
            { class: `log__row log__row--${line.kind}` },
            h("span", { class: "log__clock num" }, line.clock),
            h("span", { class: "log__mark" }, MARK[line.kind] ?? "·"),
            h(
              "span",
              { class: "log__text" },
              line.kind === "chat" && line.actor
                ? h("strong", {}, `${line.actor}: `)
                : null,
              line.text,
            ),
          ),
        );
      }
      list.scrollTop = list.scrollHeight;
    },
  };
}
