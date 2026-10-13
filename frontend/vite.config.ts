import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In dev, /api is proxied to the FastAPI service (same paths as nginx in production).
const apiTarget = process.env.SKYRADAR_API_PROXY ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: apiTarget,
        ws: true,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  build: {
    chunkSizeWarningLimit: 2000,
  },
  test: {
    environment: "node",
  },
});
