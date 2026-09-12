import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/components.css";
import "./styles/landing.css";
import "./styles/call.css";

import { clear } from "./lib/dom";
import { getHealth, getScenes, isOffline } from "./api";
import { landing } from "./views/landing";
import { callView, type CallMode } from "./views/call";
import type { Health, SceneDescription } from "./types";

/* Router.
 *
 * Hash routing, and not because a History API router is hard: this build has
 * to open correctly from a file path, from a hackathon laptop's `python -m
 * http.server`, and from a static host with no rewrite rules, and a hash
 * survives all three. Five routes do not justify a router dependency.
 */

export interface Ctx {
  health: Health;
  scenes: SceneDescription[];
  offline: boolean;
  go(route: string): void;
}

const MODES: CallMode[] = ["teach", "practice", "perform"];

async function boot(): Promise<void> {
  const found = document.querySelector<HTMLElement>("#app");
  if (!found) return;
  const root: HTMLElement = found;

  root.dataset.state = "loading";
  const [health, scenes] = await Promise.all([getHealth(), getScenes()]);
  delete root.dataset.state;

  let teardown: (() => void) | null = null;

  const ctx: Ctx = {
    health,
    scenes,
    offline: isOffline(),
    go(route) {
      if (globalThis.location.hash === route) render();
      else globalThis.location.hash = route;
    },
  };

  function render(): void {
    teardown?.();
    teardown = null;
    clear(root);
    root.scrollTop = 0;

    const hash = globalThis.location.hash.replace(/^#\/?/, "");
    const mode = MODES.find((m) => hash === m);
    if (mode) {
      const view = callView(ctx, mode);
      root.appendChild(view.el);
      teardown = view.dispose;
    } else {
      root.appendChild(landing(ctx));
    }
    globalThis.scrollTo({ top: 0 });
  }

  globalThis.addEventListener("hashchange", render);
  render();
}

void boot();
