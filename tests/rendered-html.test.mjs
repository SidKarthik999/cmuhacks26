import assert from "node:assert/strict";
import test from "node:test";

async function render(path = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request(`http://localhost${path}`, { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the Swarlink experience", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>Swarlink — Music, together in time<\/title>/i);
  assert.match(html, /SWARLINK/);
  assert.match(html, /MUSIC,/);
  assert.match(html, /TOGETHER/);
  assert.match(html, /ENTER STUDIO/);
  assert.doesNotMatch(html, /codex-preview|SkeletonPreview|Your site is taking shape/i);
});

test("a shared room link opens directly into the multi-device Stage", async () => {
  const response = await render("/?room=A7K2Q9");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /MULTI-DEVICE CONCERT LINK/i);
  assert.match(html, /A7K2Q9/);
  assert.match(html, /CREATE LIVE ROOM/i);
});

test("publishes product-specific metadata", async () => {
  const response = await render();
  const html = await response.text();
  assert.match(html, /precision studio for clean music lessons/i);
  assert.match(html, /og:title/i);
  assert.match(html, /twitter:card/i);
});
