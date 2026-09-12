import { defineConfig } from "vite";
import path from "node:path";

export default defineConfig({
  root: path.resolve(__dirname),
  server: {
    port: 5173,
    // Dev-only: cloudflared quick tunnels get a random *.trycloudflare.com
    // hostname each run, so an allowlist can't be pinned in advance.
    allowedHosts: true,
    proxy: {
      "/rooms": "http://localhost:8787",
      "/health": "http://localhost:8787",
      "/ws": {
        target: "ws://localhost:8787",
        ws: true,
      },
    },
  },
  resolve: {
    alias: {
      "@shared": path.resolve(__dirname, "../shared"),
    },
  },
});
