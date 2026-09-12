import { defineConfig } from "vite";

// The API runs as a separate FastAPI process. Proxying it in dev keeps the
// browser on one origin, so there is no CORS preflight on every analysis and
// the `fetch` paths in `src/api.ts` are identical in dev and in production
// behind any reverse proxy.
export default defineConfig({
  server: {
    port: 5183,
    proxy: {
      "/api": {
        target: process.env.SWARLINK_API ?? "http://127.0.0.1:8077",
        changeOrigin: true,
      },
    },
  },
  build: {
    target: "es2022",
    assetsInlineLimit: 0,
  },
});
