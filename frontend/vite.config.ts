import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vite";

/**
 * The frontend is a separate deployable that talks to the API over REST.
 *
 * In development the `/api` proxy lets the browser call a same-origin path, so
 * no CORS preflight is involved locally. In production the app is built to
 * static files and served by a CDN or static host, and points at the API origin
 * via VITE_API_BASE_URL — the two are never coupled at build time.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
